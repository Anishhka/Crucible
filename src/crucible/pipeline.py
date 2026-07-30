"""
The run pipeline (CONTRACT.md §3, §4).

Reads a batch, produces the full artifact set, returns the exit code.

Processing is serial by design (ASSUMPTIONS.md §7.9). `--max-workers` is
accepted and recorded, and deliberately does not change the execution strategy:
the contract requires byte-identical output at 1 and at 8 workers, the batch is
at most a thousand records of pure-Python arithmetic, and "concurrency cannot
affect the output because there is none" is a stronger guarantee than a sorted
merge after a parallel map.

The exit-code rules are subtle enough to state in one place:

    0  every proposal reached a terminal verdict and nothing was unavailable
    3  the envelope was rejected; no verdict about any proposal was possible
    4  degraded success -- a complete, valid artifact set, but at least one of:
       a quarantined record, an `unavailable` check, an `unavailable` evidence
       query, an unreachable provider, or the timeout being reached

Exit 0 is NOT returned when any check is unavailable. For a build whose
literature provider is a replay cassette, exit 4 is the ordinary healthy outcome
and exit 0 is the exception -- it means the local evidence happened to cover the
whole batch.
"""

from __future__ import annotations

import glob
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from . import (artifacts, cache as cache_mod, claims, composition, feasibility,
               providers, report, repairs as repairs_mod, reward as reward_mod,
               net, routes, structure, thermo)
from .config import Config, load_config
from .envelope import EnvelopeRejected, EnvelopeReport, read_envelope
from .normalize import composition_key, normalize_formula
from .recordcheck import check_record, safe_slug

MAX_FIGURE_PROPOSALS = 4


@dataclass
class RunResult:
    exit_code: int


class UsageError(Exception):
    """Bad invocation or unreadable configuration: exit 2, nothing processed."""


def _list_input_files(input_path: str) -> list[str]:
    if os.path.isdir(input_path):
        found = sorted(glob.glob(os.path.join(input_path, "*.json")))
        if not found:
            raise UsageError(f"no .json batch files in directory {input_path!r}")
        return found
    if not os.path.exists(input_path):
        raise UsageError(f"input path does not exist: {input_path!r}")
    return [input_path]


def _prepare_out_dir(out_dir: str) -> None:
    """An existing non-empty output directory is fully overwritten, never merged:
    a merged output directory is not reproducible."""
    if os.path.isdir(out_dir) and os.listdir(out_dir):
        for entry in os.listdir(out_dir):
            path = os.path.join(out_dir, entry)
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
    os.makedirs(out_dir, exist_ok=True)


def _logical_time(now: str | None) -> str:
    """The instant the run should CLAIM to have happened. Graded and
    deterministic; wall-clock time never appears here."""
    if now:
        return now
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch:
        try:
            return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat().replace(
                "+00:00", "Z")
        except (ValueError, OverflowError, OSError):
            pass
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _run_id(cfg_hash: str, seed: int, logical_time: str, file_hashes: list[str]) -> str:
    """Deterministic given inputs, config, seed and --now -- and independent of
    where the input happened to be mounted, so two runs of the same batch from
    different directories agree."""
    material = "|".join([cfg_hash, str(seed), logical_time, *sorted(file_hashes)])
    return "run-" + artifacts.sha256_bytes(material.encode("utf-8"))[:24]


def _calibration_pairs(proposal: dict, violations: list[dict],
                       any_unavailable: bool) -> dict | None:
    """Pair the generator's self-reported confidence against the outcome.

    "The model is most confident exactly where it is most wrong" is among the
    most useful things you can tell someone maintaining it, and this costs
    nothing beyond reading a field the generator already supplies.
    """
    confidence = proposal.get("generator_confidence")
    if not isinstance(confidence, dict):
        return None
    per_field = confidence.get("per_field")
    if not isinstance(per_field, dict) or not per_field:
        return None

    error_pointers = {v["json_pointer"] for v in violations if v["level"] == "error"}
    warn_pointers = {v["json_pointer"] for v in violations if v["level"] == "warning"}

    pairs = []
    for pointer in sorted(per_field):
        value = per_field[pointer]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        # A finding on a child of the field counts against the field: a bad
        # coordinate is a bad /structure/sites entry.
        hit_error = any(p == pointer or p.startswith(pointer + "/") for p in error_pointers)
        hit_warn = any(p == pointer or p.startswith(pointer + "/") for p in warn_pointers)
        if any_unavailable and not (hit_error or hit_warn):
            outcome = "unverifiable"
        elif hit_error:
            outcome = "incorrect"
        else:
            outcome = "correct"
        pairs.append({
            "json_pointer": pointer[:256],
            "self_confidence": float(value),
            "outcome": outcome,
            "verifier_confidence": 0.6 if outcome != "unverifiable" else None,
            "verifier_determinism": "deterministic",
        })

    if not pairs:
        return None
    block = {"pairs": pairs}
    overall = confidence.get("overall")
    if isinstance(overall, (int, float)) and not isinstance(overall, bool):
        block["overall_self_confidence"] = float(overall)
    return block


def _build_record(proposal: dict, *, run_id: str, cfg: Config,
                  batch_units: dict[str, str], dq: EnvelopeReport,
                  generate_repairs: bool = True) -> tuple[dict, list, list]:
    """Verify one proposal and build its canonical verdict record."""
    proposal_id = str(proposal.get("proposal_id", "<unknown>"))
    key = artifacts.candidate_key(proposal)
    record_id = f"{run_id}:{safe_slug(proposal_id)}"

    validity_cfg = cfg["validity"]
    feas_cfg = cfg["feasibility"]

    violations: list[dict] = []
    checks: list[dict] = []

    # --- record-level contract conformance --------------------------------
    schema_result = check_record(proposal)
    if not schema_result.conforms:
        dq.hit("record.schema_conformance", example=proposal_id)
    for issue in schema_result.issues:
        violations.append(artifacts.make_violation(
            issue.code, "error", issue.json_pointer, issue.message,
            observed=issue.observed, expected=issue.expected))
    checks.append({
        "check_id": "schema.record_conformance", "phase": "schema",
        "outcome": "pass" if schema_result.conforms else "fail",
        "detail": artifacts.truncate(
            "Record conforms to proposals.schema.json."
            if schema_result.conforms else
            "Record violates the input contract: "
            + "; ".join(f"{i.code} at {i.json_pointer or '/'}" for i in schema_result.issues),
            1000),
    })

    checks.append({"check_id": "parse.envelope", "phase": "parse", "outcome": "pass",
                   "detail": "Record was admitted at the envelope level."})

    # --- normalisation ------------------------------------------------------
    raw_formula = ""
    comp = proposal.get("composition")
    if isinstance(comp, dict) and isinstance(comp.get("formula"), str):
        raw_formula = comp["formula"]
    norm = normalize_formula(raw_formula)

    for code, message in norm.findings:
        level = "note" if code == "CHEM.FORMULA.NOT_REDUCED" else "error"
        violations.append(artifacts.make_violation(
            code, level, "/composition/formula", message, observed=raw_formula))

    if not norm.parsed_ok:
        violations.append(artifacts.make_violation(
            norm.rejected_code or "CHEM.FORMULA.UNPARSEABLE", "error",
            "/composition/formula", norm.rejected_reason or "Formula could not be parsed.",
            observed=raw_formula))

    normalized_block = {
        "formula_raw": raw_formula[:200],
        "formula_normalized": norm.formula_normalized,
        "reduced_formula": norm.reduced_formula,
        "elements": norm.elements,
        "n_elements": len(norm.elements) if norm.elements else None,
        "chemsys": norm.chemsys,
        "normalization_steps": [
            {k: artifacts.truncate(v, 200) for k, v in step.items()}
            for step in norm.steps
        ],
        "in_scope": bool(norm.in_scope),
    }
    if not norm.in_scope:
        normalized_block["scope_reason"] = artifacts.truncate(
            norm.scope_reason or norm.rejected_reason
            or "Formula could not be normalised, so scope could not be determined.",
            500)
        if norm.parsed_ok:
            violations.append(artifacts.make_violation(
                "CHEM.SCOPE.NOT_INORGANIC_CRYSTALLINE", "error", "/composition/formula",
                norm.scope_reason or "Not an inorganic crystalline solid.",
                observed=norm.reduced_formula))

    checks.append({
        "check_id": "composition.formula_normalize", "phase": "composition",
        "outcome": "pass" if norm.parsed_ok else "fail",
        "detail": artifacts.truncate(
            norm.rejected_reason or f"Formula normalised to {norm.reduced_formula}.", 1000),
        "measured": {"normalization_steps": len(norm.steps)},
    })

    # --- composition screens ------------------------------------------------
    composition_result = None
    if norm.parsed_ok:
        composition_result = composition.check_composition(proposal, norm.counts, validity_cfg)
        for finding in composition_result.findings:
            violations.append(artifacts.make_violation(
                finding.code, finding.level, finding.json_pointer, finding.message,
                observed=finding.observed, expected=finding.expected))
        checks.append({
            "check_id": "composition.charge_neutrality", "phase": "composition",
            "outcome": ("not_applicable" if not composition_result.charge.applicable
                        else "pass" if composition_result.charge.balanced else "warn"),
            "detail": artifacts.truncate(composition_result.charge.rationale, 1000),
            "measured": composition_result.measured,
        })
    else:
        checks.append({
            "check_id": "composition.charge_neutrality", "phase": "composition",
            "outcome": "not_applicable",
            "detail": "Formula did not normalise, so no composition screen was run.",
        })

    # --- structure ----------------------------------------------------------
    structure_result = structure.check_structure(
        proposal, norm.counts if norm.parsed_ok else None, validity_cfg)
    for finding in structure_result.findings:
        violations.append(artifacts.make_violation(
            finding.code, finding.level, finding.json_pointer, finding.message,
            observed=finding.observed, expected=finding.expected))
    checks.append({
        "check_id": "structure.coherence", "phase": "structure",
        "outcome": ("not_applicable" if not structure_result.evaluated
                    else "fail" if any(f.level == "error" for f in structure_result.findings)
                    else "warn" if structure_result.findings else "pass"),
        "detail": artifacts.truncate(structure_result.detail, 1000),
        "measured": structure_result.measured or None,
    })

    # --- prior art ----------------------------------------------------------
    novelty_block = None
    evidence_queries: list[dict] = []
    query_objects: list = []
    if norm.parsed_ok:
        query_objects = providers.query_all(norm.counts, only=cfg["evidence"].get("only"))
        evidence_queries = [q.to_json() for q in query_objects]
        novelty_block = providers.novelty_block(query_objects)

        tier = novelty_block["tier"]
        if tier == "experimentally_reported":
            violations.append(artifacts.make_violation(
                "NOVEL.DUPLICATE.EXACT_MATCH", "error", "/composition/formula",
                novelty_block["rationale"], observed=norm.reduced_formula,
                confidence=novelty_block["confidence"]))
        elif tier == "computed_only":
            violations.append(artifacts.make_violation(
                "NOVEL.DUPLICATE.COMPOSITION_MATCH", "warning", "/composition/formula",
                novelty_block["rationale"], observed=norm.reduced_formula,
                confidence=novelty_block["confidence"]))
        elif tier == "literature_mention":
            violations.append(artifacts.make_violation(
                "NOVEL.PRIOR_ART.LITERATURE_MENTION", "note", "/composition/formula",
                novelty_block["rationale"], observed=norm.reduced_formula,
                confidence=novelty_block["confidence"]))
        for conflict in novelty_block.get("conflicts", []):
            violations.append(artifacts.make_violation(
                "NOVEL.EVIDENCE.SOURCES_DISAGREE", "note", "/composition/formula",
                conflict["description"], observed=", ".join(conflict["providers"])))
        for query in query_objects:
            if query.status == "unavailable":
                violations.append(artifacts.make_violation(
                    "VERIFY.UNAVAILABLE.OFFLINE_CACHE_MISS" if query.mode == "replay"
                    else "VERIFY.UNAVAILABLE.PROVIDER_ERROR",
                    "none", "/composition/formula",
                    f"Provider {query.provider} could not answer: {query.error}",
                    observed=query.provider, unverifiable=True))

    unavailable_queries = [q for q in query_objects if q.status == "unavailable"]
    checks.append({
        "check_id": "novelty.prior_art", "phase": "novelty",
        "outcome": ("not_applicable" if not norm.parsed_ok
                    else "unavailable" if unavailable_queries
                    else "pass"),
        "detail": artifacts.truncate(
            "Formula did not normalise, so no provider was queried."
            if not norm.parsed_ok else novelty_block["rationale"], 1000),
        "measured": {"providers_queried": len(query_objects),
                     "providers_unavailable": len(unavailable_queries)},
    })

    # --- feasibility --------------------------------------------------------
    feasibility_result = None
    feasibility_block = None
    if norm.parsed_ok:
        charge = composition_result.charge if composition_result else None
        feasibility_result = feasibility.screen(norm.counts, charge, feas_cfg, proposal)
        feasibility_block = feasibility.feasibility_block(feasibility_result, charge, feas_cfg)
        for finding in feasibility_result.findings:
            violations.append(artifacts.make_violation(
                finding.code, finding.level, finding.json_pointer, finding.message,
                observed=finding.observed, expected=finding.expected))
        for finding in feasibility.check_frame_applicability(
                proposal, feas_cfg, feasibility_result):
            violations.append(artifacts.make_violation(
                finding.code, finding.level, finding.json_pointer, finding.message,
                observed=finding.observed, expected=finding.expected))
    checks.append({
        "check_id": "feasibility.screen", "phase": "feasibility",
        "outcome": ("not_applicable" if not norm.parsed_ok
                    else "pass" if feasibility_result.verdict == "plausible"
                    else "warn"),
        "detail": artifacts.truncate(
            "Formula did not normalise." if not norm.parsed_ok
            else feasibility_result.rationale, 1000),
        "measured": feasibility_result.measured if feasibility_result else None,
    })

    # --- routes -------------------------------------------------------------
    route_block = None
    if norm.parsed_ok and norm.in_scope:
        proposed = routes.propose_routes(norm.counts, cfg["routes"], norm.reduced_formula)
        hint = routes.assess_hint(proposal, norm.counts)
        for finding in hint.findings:
            violations.append(artifacts.make_violation(
                finding.code, finding.level, finding.json_pointer, finding.message,
                observed=finding.observed, expected=finding.expected))
        for finding in routes.route_findings(proposed, cfg["routes"]):
            violations.append(artifacts.make_violation(
                finding.code, finding.level, finding.json_pointer, finding.message,
                observed=finding.observed, expected=finding.expected))
        if not proposed and feasibility_result and feasibility_result.verdict in ("plausible", "marginal"):
            violations.append(artifacts.make_violation(
                "FEAS.SYNTH.NO_VIABLE_ROUTE", "note", "/composition/formula",
                "No balanced route from the configured precursor families reaches "
                "this composition.", observed=norm.reduced_formula))
        route_block = {"routes": proposed, "hint_assessment": hint.hint_assessment}
    checks.append({
        "check_id": "route.enumeration", "phase": "route",
        "outcome": ("not_applicable" if route_block is None
                    else "pass" if route_block["routes"] else "warn"),
        "detail": artifacts.truncate(
            "Not attempted: formula did not normalise or proposal is out of scope."
            if route_block is None
            else f"{len(route_block['routes'])} balanced route(s) proposed; driving "
                 f"forces are null because this build computes no energies.", 1000),
    })

    # --- claims -------------------------------------------------------------
    claim_result = claims.audit_claims(proposal, batch_units)
    for finding in claim_result.findings:
        violations.append(artifacts.make_violation(
            finding.code, finding.level, finding.json_pointer, finding.message,
            observed=finding.observed, expected=finding.expected))
    for norm_entry in claim_result.unit_normalizations:
        dq.hit("claims.unit_normalization", example=proposal_id)
    for sentinel in claim_result.sentinels:
        dq.hit("claims.sentinel_values", example=proposal_id)
    checks.append({
        "check_id": "claims.audit", "phase": "claims",
        "outcome": ("not_applicable" if not claim_result.evaluated
                    else "fail" if any(f.level == "error" for f in claim_result.findings)
                    else "warn" if claim_result.findings else "pass"),
        "detail": artifacts.truncate(claim_result.detail, 1000),
        "measured": {"n_claims": len(claim_result.entries)},
    })

    # --- reward and status --------------------------------------------------
    reward_result = reward_mod.compute_reward(
        parse_ok=True, schema_ok=schema_result.conforms, formula_ok=norm.parsed_ok,
        composition_result=composition_result, structure_result=structure_result,
        novelty_tier=novelty_block["tier"] if novelty_block else None,
        feasibility_verdict=feasibility_result.verdict if feasibility_result else None,
        claim_result=claim_result, in_scope=norm.in_scope, cfg=cfg["reward"],
    )

    any_unavailable = any(c["outcome"] == "unavailable" for c in checks)
    has_error = any(v["level"] == "error" for v in violations)
    status, summary = reward_mod.decide_status(
        gates_failed=reward_result.gates_failed, any_unavailable=any_unavailable,
        has_error_finding=has_error, in_scope=norm.in_scope,
    )

    checks.sort(key=lambda c: (c["phase"], c["check_id"]))
    for check in checks:
        if check.get("measured") is None:
            check.pop("measured", None)

    violations.sort(key=lambda v: (v["code"], v["json_pointer"]))

    record: dict = {
        "record_schema_version": artifacts.RECORD_SCHEMA_VERSION,
        "record_id": record_id,
        "run_id": run_id,
        "proposal_id": proposal_id[:64],
        "candidate_key": key,
        "verdict": {
            "status": status,
            "reward": reward_result.value,
            "reward_fn_id": cfg["reward"]["id"],
            "reward_components": reward_result.components,
            "gates_failed": reward_result.gates_failed,
            "verifiable": not any_unavailable,
            "summary": artifacts.truncate(summary, 1000),
        },
        "normalized": normalized_block,
        "checks": checks,
        "violations": violations,
        "evidence": {"queries": evidence_queries},
        "assumptions": feasibility.assumption_stack(
            proposal, validity_cfg, feas_cfg, structure_result.evaluated,
            feasibility_result),
    }
    if novelty_block is not None:
        record["novelty"] = novelty_block
    if feasibility_block is not None:
        record["feasibility"] = feasibility_block
    if route_block is not None:
        record["route"] = route_block
    if claim_result.entries:
        record["claim_audit"] = claim_result.entries
    calibration = _calibration_pairs(proposal, violations, any_unavailable)
    if calibration:
        record["calibration"] = calibration

    # --- repairs, re-verified against the same checks ----------------------
    # generate_repairs is False on the re-verification pass, which is what stops
    # a repaired proposal from proposing repairs of its own forever.
    if generate_repairs:
        proposed_repairs = repairs_mod.propose_repairs(
            proposal, violations, norm.counts if norm.parsed_ok else None)
        if proposed_repairs:
            def _reverify_once(candidate: dict) -> dict:
                patched_record, _, _ = _build_record(
                    candidate, run_id=run_id, cfg=cfg, batch_units=batch_units,
                    dq=EnvelopeReport(), generate_repairs=False)
                return patched_record

            repairs_mod.reverify(proposal, proposed_repairs, _reverify_once)
            record["repairs"] = [r.to_json() for r in proposed_repairs]

    return record, claim_result.unit_normalizations, claim_result.sentinels


def run(*, input_path: str, out_dir: str, seed: int = 0, offline: bool = False,
        now: str | None = None, config_path: str | None = None,
        max_workers: int = 1, timeout: float | None = None,
        provider_filter: list[str] | None = None, verbose: bool = False,
        cache_dir: str | None = None) -> RunResult:
    started = time.monotonic()

    # Arm the transport chokepoint before any other module runs, so an
    # offline run cannot open a socket even by mistake.
    net.set_offline(offline)

    # --max-workers and --timeout are execution knobs, not verification
    # parameters, and they are deliberately NOT folded into the resolved config.
    # Doing so would move config.sha256, which moves run_id, which moves
    # record_id, which changes every graded artifact -- and CONTRACT.md §6
    # requires byte-identical output at --max-workers 1 and 8. They are recorded
    # in provenance.environment instead, the one subtree exempt from
    # byte-identity, which is exactly what that exemption is for.
    try:
        cfg = load_config(config_path, overrides={
            "evidence": {"offline": offline, "only": provider_filter or None},
        })
    except (OSError, ValueError) as exc:
        raise UsageError(str(exc)) from exc

    files = _list_input_files(input_path)
    _prepare_out_dir(out_dir)

    digits = cfg["output"]["float_significant_digits"]
    logical_time = _logical_time(now)
    dq = EnvelopeReport()

    input_records: list[dict] = []
    file_hashes: list[str] = []
    all_proposals: list[dict] = []
    naive_pointers: list[str] = []

    for path in files:
        file_hash = artifacts.sha256_file(path)
        file_hashes.append(file_hash)
        try:
            doc, naive = read_envelope(path, dq, cfg["ingest"])
        except EnvelopeRejected as exc:
            # Record which rule fired before writing the rejection. Everything
            # that IS known must still be recorded on an exit-3 path, and a
            # data_quality report where every rule reads zero cannot show which
            # bound the input actually breached.
            if exc.rule_id and exc.rule_id in dq.rules and dq.rules[exc.rule_id].n_triggered == 0:
                dq.hit(exc.rule_id, example=os.path.basename(path))
            run_id = _run_id(cfg.sha256, seed, logical_time, file_hashes)
            return _write_envelope_rejection(
                out_dir=out_dir, run_id=run_id, path=path, file_hash=file_hash,
                exc=exc, dq=dq, cfg=cfg, seed=seed, offline=offline,
                logical_time=logical_time, digits=digits)

        naive_pointers.extend(naive)
        input_records.append({
            "name": os.path.basename(path)[:256],
            "sha256": file_hash,
            "bytes": os.path.getsize(path),
            "batch_id": doc.get("batch_id"),
            "schema_version": doc.get("schema_version"),
            "accepted": True,
            "reject_reason": None,
        })
        all_proposals.extend(doc.get("proposals", []))

    run_id = _run_id(cfg.sha256, seed, logical_time, file_hashes)

    # --- duplicate proposal_id: quarantine the later record, never merge ----
    seen_ids: set[str] = set()
    admitted: list[dict] = []
    quarantined: list[dict] = []
    for proposal in all_proposals:
        pid = proposal.get("proposal_id") if isinstance(proposal, dict) else None
        locator = str(pid) if isinstance(pid, str) else f"/proposals/{len(admitted) + len(quarantined)}"
        if isinstance(pid, str) and pid in seen_ids:
            dq.hit("record.duplicate_proposal_id", example=pid)
            quarantined.append({
                "locator": locator[:256],
                "reason": (f"proposal_id {pid!r} already appeared in this batch. A "
                           f"duplicate identifier is a defect in the generator, not "
                           f"an instruction to merge or overwrite, so the later "
                           f"record is quarantined and both are accounted for."),
                "violation_code": "FMT.ID.DUPLICATE_PROPOSAL_ID",
                "raw_excerpt": None,
            })
            continue
        # A record that violates the declared input contract cannot be admitted:
        # when the generator and the verifier disagree about the contract itself,
        # every downstream conclusion about that record is meaningless. Detect,
        # quarantine, count, report -- never coerce and continue. This is also
        # what keeps a hostile `proposal_id` away from the code that names
        # per-proposal artifacts (see recordcheck.safe_slug for the second
        # barrier).
        conformance = check_record(proposal)
        if not conformance.conforms:
            dq.hit("record.schema_conformance", example=locator)
            detail = "; ".join(
                f"{issue.code} at {issue.json_pointer or '/'}"
                for issue in conformance.issues[:4])
            quarantined.append({
                "locator": locator[:256],
                "reason": (f"Record does not conform to proposals.schema.json "
                           f"({detail}). The input contract is closed, so an "
                           f"undeclared field, a missing required field or a "
                           f"violated constraint means the generator and the "
                           f"verifier disagree about the contract -- a condition to "
                           f"surface rather than tolerate."),
                "violation_code": conformance.issues[0].code,
                "raw_excerpt": None,
            })
            continue

        if isinstance(pid, str):
            seen_ids.add(pid)
        admitted.append(proposal)

    batch_units = claims.collect_batch_units(admitted)

    verdicts: list[dict] = []
    unit_normalizations: list[dict] = []
    sentinels: list[dict] = []
    budget_exhausted = False

    # The cache key needs the corpus versions, so they are resolved before the
    # record loop rather than after it.
    provider_filter_list = cfg["evidence"].get("only")
    reference_rows = providers.reference_data(provider_filter_list)
    thermo_row = thermo.dataset_manifest_entry()
    if thermo_row is not None:
        reference_rows = sorted(reference_rows + [thermo_row], key=lambda r: r["id"])

    cache = cache_mod.VerificationCache(cache_dir)

    # --- online mode: was the live evidence we were asked for obtainable? ----
    # Graded runs pass --offline and never reach this. Without --offline the
    # caller is asking for live sources as well as local ones, and this build
    # must not report a clean success when it could not even try. See net.py.
    live_degradation: dict | None = None
    if not offline:
        probe = net.reachable(
            cfg["evidence"]["request"]["reachability_endpoints"],
            cfg["evidence"]["request"]["connect_timeout_seconds"],
            cfg["evidence"]["request"]["read_timeout_seconds"])
        if probe.reachable:
            # Reachable, but there is still no live provider implemented. That is
            # a planned degradation, not a failure, and it is recorded as one.
            live_degradation = {
                "provider": "live_literature",
                "reason": ("Network is reachable, but this build implements no live "
                           "evidence provider: all prior art comes from committed "
                           "corpora. Deliberately not consulted, which is a planned "
                           "degradation rather than an error. "
                           + probe.detail),
                "planned": True,
            }
        else:
            live_degradation = {
                "provider": "live_literature",
                "reason": ("Live evidence was requested by the absence of --offline "
                           "and could not be obtained. " + probe.detail
                           + " This run therefore cannot claim the live sources were "
                             "consulted, and is reported as a degraded success."),
                "planned": False,
            }

    for proposal in admitted:
        if timeout is not None and (time.monotonic() - started) > timeout:
            budget_exhausted = True
            break

        proposal_id = str(proposal.get("proposal_id", "<unknown>"))
        record_id = f"{run_id}:{safe_slug(proposal_id)}"
        cache_key = cache_mod.compute_key(
            candidate_key=artifacts.candidate_key(proposal),
            config_sha256=cfg.sha256, reference_rows=reference_rows,
            crucible_version=artifacts.CRUCIBLE_VERSION,
            taxonomy_version=artifacts.TAXONOMY_VERSION,
            record_schema_version=artifacts.RECORD_SCHEMA_VERSION)

        cached = cache.get(cache_key, run_id=run_id, record_id=record_id)
        if cached is not None:
            record, units, found_sentinels = cached
        else:
            record, units, found_sentinels = _build_record(
                proposal, run_id=run_id, cfg=cfg, batch_units=batch_units, dq=dq)
            cache.put(cache_key, record, units, found_sentinels)
        verdicts.append(record)
        unit_normalizations.extend(
            u if isinstance(u, dict) else
            {"proposal_id": u.proposal_id, "property": u.property,
             "from_unit": u.from_unit, "to_unit": u.to_unit, "factor": u.factor}
            for u in units)
        sentinels.extend(
            s if isinstance(s, dict) else
            {"proposal_id": s.proposal_id, "json_pointer": s.json_pointer,
             "value": s.value, "rationale": artifacts.truncate(s.rationale, 500)}
            for s in found_sentinels)

    for proposal in admitted[len(verdicts):]:
        pid = proposal.get("proposal_id") if isinstance(proposal, dict) else None
        quarantined.append({
            "locator": (str(pid) if isinstance(pid, str) else "/proposals/?")[:256],
            "reason": "Wall-clock budget reached before this record was verified. "
                      "Recorded as unprocessed rather than silently dropped.",
            "violation_code": "VERIFY.BUDGET.EXHAUSTED",
            "raw_excerpt": None,
        })

    verdicts = artifacts.sort_verdicts(verdicts)

    # --- violations.jsonl: denormalised long-format projection --------------
    phase_by_code = _phase_index()
    violation_rows: list[dict] = []
    for record in verdicts:
        for violation in record["violations"]:
            row = {
                "run_id": run_id,
                "record_id": record["record_id"],
                "proposal_id": record["proposal_id"],
                "candidate_key": record["candidate_key"],
                "taxonomy_version": artifacts.TAXONOMY_VERSION,
                "code": violation["code"],
                "level": violation["level"],
                "phase": phase_by_code.get(violation["code"], "meta"),
                "json_pointer": violation["json_pointer"],
                "fingerprint": violation["fingerprint"],
                "detector": "crucible.pipeline@1.0.0",
                "gate": violation["code"].startswith(("FMT.JSON.", "FMT.SCHEMA.")),
            }
            for optional in ("message", "observed", "expected", "confidence",
                             "unverifiable"):
                if optional in violation:
                    row[optional] = violation[optional]
            violation_rows.append(row)
    violation_rows = artifacts.sort_violations(violation_rows)

    # --- counts and status --------------------------------------------------
    counts = {
        "proposals_in": len(all_proposals),
        "verdicts_out": len(verdicts),
        "accepted": sum(1 for r in verdicts if r["verdict"]["status"] == "accepted"),
        "rejected": sum(1 for r in verdicts if r["verdict"]["status"] == "rejected"),
        "unverifiable": sum(1 for r in verdicts if r["verdict"]["status"] == "unverifiable"),
        "quarantined": len(quarantined),
        "out_of_scope": sum(1 for r in verdicts if not r["normalized"]["in_scope"]),
        "duplicates_within_batch": sum(
            1 for q in quarantined if q["violation_code"] == "FMT.ID.DUPLICATE_PROPOSAL_ID"),
    }

    dq.totals = {
        "records_seen": len(all_proposals),
        "records_admitted": len(verdicts),
        "records_modified": sum(
            1 for r in verdicts if r["normalized"]["normalization_steps"]),
    }

    unavailable_providers = providers.unavailable_providers(provider_filter_list)
    if live_degradation is not None and not live_degradation["planned"]:
        unavailable_providers = sorted(
            unavailable_providers + [{"provider": live_degradation["provider"],
                                      "reason": live_degradation["reason"][:500]}],
            key=lambda p: p["provider"])

    unavailable_queries = sum(
        1 for r in verdicts for q in r["evidence"]["queries"]
        if q.get("status") == "unavailable")
    any_unavailable_check = any(
        c["outcome"] == "unavailable" for r in verdicts for c in r["checks"])

    degraded = bool(quarantined or unavailable_queries or any_unavailable_check
                    or unavailable_providers or budget_exhausted)
    exit_code = 4 if degraded else 0
    status = "partial" if degraded else "ok"

    # --- artifacts ----------------------------------------------------------
    outputs: list[dict] = []

    def record_output(path: str, digest: str, size: int, graded: bool = True) -> None:
        outputs.append({"path": path, "sha256": digest, "bytes": size, "graded": graded})

    v_sha, v_bytes = artifacts.write_jsonl(
        os.path.join(out_dir, "verdicts.jsonl"), verdicts, digits=digits)
    record_output("verdicts.jsonl", v_sha, v_bytes)

    x_sha, x_bytes = artifacts.write_jsonl(
        os.path.join(out_dir, "violations.jsonl"), violation_rows, digits=digits)
    record_output("violations.jsonl", x_sha, x_bytes)

    data_quality = {
        "schema_version": "1-0-0",
        "run_id": run_id,
        "totals": {
            "records_seen": dq.totals["records_seen"],
            "records_admitted": dq.totals["records_admitted"],
            "records_quarantined": len(quarantined),
            "records_modified": dq.totals["records_modified"],
            "bytes_read": dq.bytes_read,
        },
        "rules": dq.as_list(),
        "quarantined": sorted(quarantined, key=lambda q: q["locator"]),
        "unit_normalizations": sorted(
            unit_normalizations, key=lambda u: (u["proposal_id"], u["property"])),
        "sentinel_detections": sorted(
            sentinels, key=lambda s: (s["proposal_id"], s["json_pointer"])),
    }
    d_sha, d_bytes = artifacts.write_json(
        os.path.join(out_dir, "data_quality.json"), data_quality, digits=digits)
    record_output("data_quality.json", d_sha, d_bytes)

    aggregates = artifacts.build_aggregates(verdicts, violation_rows)
    recommendations = artifacts.build_recommendations(verdicts, violation_rows)
    caveats = artifacts.build_caveats(verdicts, reference_rows, unavailable_queries)

    feedback, feedback_outputs = _write_feedback(
        out_dir=out_dir, run_id=run_id, verdicts=verdicts, violation_rows=violation_rows,
        recommendations=recommendations, caveats=caveats, cfg=cfg, digits=digits)
    for path, digest, size in feedback_outputs:
        record_output(path, digest, size)

    figures = _write_figures(out_dir, verdicts, seed, digits, cfg)
    for figure, digest, size, companion_digest, companion_size in figures:
        record_output(figure.path, digest, size, graded=False)
        record_output(figure.companion_path, companion_digest, companion_size)

    # Figures are referenced from the record they illustrate, so a reader can
    # get from a verdict to its evidence without scanning the directory.
    figures_by_record: dict[str, list[dict]] = {}
    for figure, *_ in figures:
        figures_by_record.setdefault(figure.companion["record_id"], []).append({
            "kind": figure.kind, "path": figure.path,
            "media_type": figure.media_type, "data_companion": figure.companion_path,
        })
    if figures_by_record:
        for record in verdicts:
            attached = figures_by_record.get(record["record_id"])
            if attached:
                record["artifacts"] = sorted(attached, key=lambda a: a["path"])
        v_sha, v_bytes = artifacts.write_jsonl(
            os.path.join(out_dir, "verdicts.jsonl"), verdicts, digits=digits)
        for entry in outputs:
            if entry["path"] == "verdicts.jsonl":
                entry["sha256"], entry["bytes"] = v_sha, v_bytes

    manifest = _build_manifest(
        run_id=run_id, status=status, input_records=input_records, cfg=cfg,
        seed=seed, offline=offline, reference_rows=reference_rows,
        unavailable_providers=unavailable_providers, counts=counts,
        aggregates=aggregates, outputs=[], logical_time=logical_time,
        naive_pointers=naive_pointers, budget_exhausted=budget_exhausted,
        elapsed=time.monotonic() - started, max_workers=max_workers,
        timeout=timeout, cache_stats=cache.stats.as_json(),
        live_degradation=live_degradation,
    )

    report_html = report.render_report(
        manifest={**manifest, "outputs": outputs}, verdicts=verdicts,
        violations=violation_rows, data_quality=data_quality, feedback=feedback,
        figures=[f for f, *_ in figures])
    r_sha, r_bytes = artifacts.write_text(os.path.join(out_dir, "report.html"), report_html)
    record_output("report.html", r_sha, r_bytes, graded=False)

    manifest["outputs"] = sorted(outputs, key=lambda o: o["path"])
    artifacts.write_json(os.path.join(out_dir, "run_manifest.json"), manifest, digits=digits)

    return RunResult(exit_code=exit_code)


def _phase_index() -> dict[str, str]:
    """code -> phase, read from the committed taxonomy rather than hardcoded, so
    the two cannot drift."""
    import json
    path = os.path.join(os.path.dirname(__file__), "taxonomy", "violations.core.v1.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            taxonomy = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return {rule["id"]: rule["phase"] for rule in taxonomy.get("rules", [])}


def _write_feedback(*, out_dir: str, run_id: str, verdicts: list[dict],
                    violation_rows: list[dict], recommendations: list[dict],
                    caveats: list[str], cfg: Config, digits: int):
    """The feedback bundle. Every projection is a pure projection of the verdict
    records -- regenerable from them, containing no fact that is not in them."""
    outputs = []

    # Records whose status is `unverifiable` are excluded from every training
    # projection: a provider timeout is not evidence about the generator.
    eligible = [r for r in verdicts if r["verdict"]["status"] in ("accepted", "rejected")]

    scalar_rows = [{
        "record_id": r["record_id"],
        "proposal_id": r["proposal_id"],
        "candidate_key": r["candidate_key"],
        "reward": r["verdict"]["reward"],
        "status": r["verdict"]["status"],
    } for r in eligible]

    # Preference pairs may be constructed ONLY from re-verified repairs that
    # actually improved the record. Nothing else in this build may write a row
    # here, and every row names the repair_id that carries the evidence.
    preference_rows: list[dict] = []
    for record in verdicts:
        for payload in record.get("repairs", []):
            if not payload.get("reverified"):
                continue
            if payload.get("post_repair_reward") is None:
                continue
            if payload["post_repair_reward"] <= record["verdict"]["reward"]:
                continue
            preference_rows.append({
                "record_id": record["record_id"],
                "repair_id": payload["repair_id"],
                "proposal_id": record["proposal_id"],
                "rejected_reward": record["verdict"]["reward"],
                "chosen_reward": payload["post_repair_reward"],
                "targets": ",".join(payload["targets"]),
            })
    preference_rows.sort(key=lambda r: (r["record_id"], r["repair_id"]))

    calibration_rows = [{
        "record_id": r["record_id"],
        "proposal_id": r["proposal_id"],
        "json_pointer": pair["json_pointer"],
        "self_confidence": pair["self_confidence"],
        "outcome": pair["outcome"],
    } for r in verdicts for pair in (r.get("calibration") or {}).get("pairs", [])
        if pair["outcome"] != "unverifiable"]

    violation_table_rows = [{
        "record_id": row["record_id"],
        "proposal_id": row["proposal_id"],
        "code": row["code"],
        "level": row["level"],
        "json_pointer": row["json_pointer"],
    } for row in violation_rows]

    specs = [
        ("scalar_reward", "feedback/scalar_reward.jsonl", scalar_rows,
         ["accepted", "rejected"], ["unverifiable"],
         "Unverifiable records are excluded because an unavailable check is a "
         "statement about our infrastructure, not about the generator; training "
         "against it is worse than not training at all."),
        ("preference_pairs", "feedback/preference_pairs.jsonl", preference_rows,
         ["rejected"], ["accepted", "unverifiable"],
         "Rows come only from repairs whose patched proposal was actually re-run "
         "through the same checks and scored higher than the original. A repair "
         "that was not re-verified, or that did not improve the record, is "
         "reported on the verdict but never promoted to a preference pair."),
        ("calibration_pairs", "feedback/calibration_pairs.jsonl", calibration_rows,
         ["accepted", "rejected"], ["unverifiable"],
         "Pairs whose outcome was unverifiable are excluded: they measure our "
         "coverage rather than the generator's calibration."),
        ("violation_table", "feedback/violation_table.jsonl", violation_table_rows,
         ["accepted", "rejected", "unverifiable"], [],
         "Every finding is included regardless of status, because this projection "
         "is a diagnostic index rather than a training set."),
    ]

    projections = []
    for name, path, rows, included, excluded, rationale in specs:
        digest, size = artifacts.write_jsonl(
            os.path.join(out_dir, path), rows, digits=digits)
        outputs.append((path, digest, size))
        row_spec = sorted(rows[0].keys()) if rows else sorted(
            {"scalar_reward": ["record_id", "proposal_id", "candidate_key", "reward", "status"],
             "preference_pairs": ["record_id", "repair_id", "proposal_id",
                                  "rejected_reward", "chosen_reward", "targets"],
             "calibration_pairs": ["record_id", "proposal_id", "json_pointer",
                                   "self_confidence", "outcome"],
             "violation_table": ["record_id", "proposal_id", "code", "level",
                                 "json_pointer"]}[name])
        projections.append({
            "name": name, "path": path, "format": "jsonl", "n_rows": len(rows),
            "row_spec": row_spec,
            "eligibility": {"included_statuses": included,
                            "excluded_statuses": excluded, "rationale": rationale},
            "sha256": digest,
        })

    feedback = {
        "schema_version": "1-0-0",
        "run_id": run_id,
        "taxonomy_version": artifacts.TAXONOMY_VERSION,
        "reward_fn_id": cfg["reward"]["id"],
        "projections": projections,
        "recommendations": recommendations,
        "caveats": caveats,
    }
    digest, size = artifacts.write_json(
        os.path.join(out_dir, "feedback", "index.json"), feedback, digits=digits)
    outputs.append(("feedback/index.json", digest, size))
    return feedback, outputs


def _write_figures(out_dir: str, verdicts: list[dict], seed: int, digits: int,
                   cfg: Config):
    """Emit at least one thermodynamic-context figure and one route figure, each
    with the JSON companion that is the actually-graded artifact."""
    corpus_phases = _corpus_phases_by_chemsys()
    written = []

    context_done = route_done = chempot_done = 0
    for record in verdicts:
        if context_done >= MAX_FIGURE_PROPOSALS and route_done >= MAX_FIGURE_PROPOSALS:
            break
        normalized = record.get("normalized") or {}
        if not normalized.get("in_scope") or not normalized.get("chemsys"):
            continue

        counts = _counts_from_record(record)
        if counts and context_done < MAX_FIGURE_PROPOSALS:
            enriched = dict(record)
            enriched["normalized"] = {**normalized, "_counts": counts}

            # Prefer the real hull diagram. It is only possible for a binary
            # system with actual energies; where those are missing, the
            # composition-context figure is emitted instead and its companion
            # says plainly that it carries no energy information.
            hull = thermo.convex_hull(counts)
            phases = thermo.phases_in_system(set(counts))
            figure = report.hull_figure(enriched, counts, hull, phases, seed)
            if figure is None:
                figure = report.chemical_context_figure(
                    enriched, corpus_phases.get(normalized["chemsys"], []), seed)
            if figure is not None:
                written.append(figure)
                context_done += 1

            if chempot_done < MAX_FIGURE_PROPOSALS:
                atmospheres = feasibility._atmosphere_screen(
                    counts, {"synthesis_hint": None}, cfg["feasibility"])
                chempot = report.chempot_figure(enriched, atmospheres, seed)
                if chempot is not None:
                    written.append(chempot)
                    chempot_done += 1

        if route_done < MAX_FIGURE_PROPOSALS:
            figure = report.route_figure(record, seed)
            if figure is not None:
                written.append(figure)
                route_done += 1

    results = []
    for figure in written:
        svg_digest, svg_size = artifacts.write_text(
            os.path.join(out_dir, figure.path), report.standalone_svg(figure.svg))
        c_digest, c_size = artifacts.write_json(
            os.path.join(out_dir, figure.companion_path), figure.companion, digits=digits)
        results.append((figure, svg_digest, svg_size, c_digest, c_size))
    return results


def _counts_from_record(record: dict) -> dict[str, float] | None:
    formula = (record.get("normalized") or {}).get("reduced_formula")
    if not formula:
        return None
    parsed = normalize_formula(formula)
    return parsed.counts if parsed.parsed_ok else None


def _corpus_phases_by_chemsys() -> dict[str, list[dict]]:
    """Known phases grouped by chemical system, for the context figure.

    Built from the same committed corpora the novelty lookup uses, so the figure
    cannot show a phase the verdict did not consider.
    """
    grouped: dict[str, list[dict]] = {}
    for spec in providers.PROVIDERS:
        corpus = providers._load(spec)  # noqa: SLF001 - same package, one loader
        if corpus is None:
            continue
        for entries in corpus.index.values():
            for entry in entries:
                parsed = normalize_formula(entry["reduced_formula"])
                if not parsed.parsed_ok:
                    continue
                grouped.setdefault(parsed.chemsys, []).append({
                    "identifier": entry["identifier"],
                    "reduced_formula": entry["reduced_formula"],
                    "provenance": entry["provenance"],
                    "counts": parsed.counts,
                })
    for chemsys, rows in grouped.items():
        seen = set()
        unique = []
        for row in sorted(rows, key=lambda r: (r["reduced_formula"], r["identifier"])):
            if row["reduced_formula"] in seen:
                continue
            seen.add(row["reduced_formula"])
            unique.append(row)
        grouped[chemsys] = unique
    return grouped


def _build_manifest(*, run_id: str, status: str, input_records: list[dict],
                    cfg: Config, seed: int, offline: bool, reference_rows: list[dict],
                    unavailable_providers: list[dict], counts: dict, aggregates: dict,
                    outputs: list[dict], logical_time: str, naive_pointers: list[str],
                    budget_exhausted: bool, elapsed: float, max_workers: int,
                    timeout: float | None, cache_stats: dict | None = None,
                    live_degradation: dict | None = None) -> dict:
    import platform
    import sys

    git_commit, git_dirty = artifacts.git_provenance()

    notes = []
    if naive_pointers:
        notes.append(
            f"{len(naive_pointers)} timezone-naive timestamp(s) were found "
            f"({', '.join(sorted(naive_pointers)[:5])}). The fields were refused "
            f"rather than assumed to be UTC; assuming an offset is a coercion that "
            f"changes meaning.")
    if live_degradation is not None and live_degradation["planned"]:
        notes.append(live_degradation["reason"][:1000])
    if budget_exhausted:
        notes.append("The wall-clock budget was reached before every record was "
                     "verified. Unprocessed records are listed in "
                     "data_quality.quarantined rather than silently dropped.")

    return {
        "manifest_schema_version": "1-0-0",
        "run_id": run_id,
        "crucible_version": artifacts.CRUCIBLE_VERSION,
        "taxonomy_version": artifacts.TAXONOMY_VERSION,
        "record_schema_version": artifacts.RECORD_SCHEMA_VERSION,
        "reward_fn_id": cfg["reward"]["id"],
        "status": status,
        "input": {"files": input_records},
        "config": {
            "sha256": cfg.sha256,
            "resolved": cfg.redacted,
            "offline": offline,
            "seed": seed,
            "providers_enabled": [p.name for p in providers.active_providers(
                cfg["evidence"].get("only"))],
            "providers_unavailable": unavailable_providers,
        },
        "reference_data": reference_rows,
        "counts": counts,
        "aggregates": aggregates,
        "outputs": outputs,
        "tool_versions": {
            "python": sys.version.split()[0],
            "crucible": artifacts.CRUCIBLE_VERSION,
            # The content hash of the code that produced this run. The manifest
            # schema closes `provenance` to additional properties, and this is a
            # version of something whose behaviour moves numbers in the output,
            # so tool_versions is where it belongs. `crucible lineage` reads it.
            "crucible_source_sha256": artifacts.source_tree_sha256(),
        },
        "provenance": {
            "logical_time": logical_time,
            "git_commit": git_commit,
            "git_dirty": git_dirty,
            # The ONLY subtree exempt from byte-identity. Everything
            # nondeterministic lives here and nowhere else.
            "environment": {
                "platform": platform.platform(),
                "python_implementation": platform.python_implementation(),
                "wall_clock_seconds": round(elapsed, 3),
                # Execution knobs live here, not in config.resolved: they must
                # not move a graded byte (CONTRACT.md §6).
                "max_workers": max_workers,
                "timeout_seconds": timeout,
                "cache": cache_stats,
            },
        },
        "notes": notes,
    }


def _write_envelope_rejection(*, out_dir: str, run_id: str, path: str, file_hash: str,
                              exc: EnvelopeRejected, dq: EnvelopeReport, cfg: Config,
                              seed: int, offline: bool, logical_time: str,
                              digits: int) -> RunResult:
    _rejection_git = artifacts.git_provenance()
    """Exit 3: the envelope was refused, so no verdict about any proposal is
    possible.

    A run that never admitted a proposal cannot honestly populate every field the
    schemas require of a completed run, and it must not invent them. The
    minItems constraints on reference_data, input.files and outputs are waived on
    this path, and aggregates.reward is omitted -- but everything that IS known,
    which file was read and which rule fired, is still recorded.
    """
    dq.totals = {"records_seen": 0, "records_admitted": 0, "records_modified": 0}

    data_quality = {
        "schema_version": "1-0-0",
        "run_id": run_id,
        "totals": {
            "records_seen": 0, "records_admitted": 0, "records_quarantined": 0,
            "records_modified": 0, "bytes_read": dq.bytes_read,
        },
        "rules": dq.as_list(),
        "quarantined": [],
        "unit_normalizations": [],
        "sentinel_detections": [],
    }
    artifacts.write_json(os.path.join(out_dir, "data_quality.json"),
                         data_quality, digits=digits)

    manifest = {
        "manifest_schema_version": "1-0-0",
        "run_id": run_id,
        "crucible_version": artifacts.CRUCIBLE_VERSION,
        "taxonomy_version": artifacts.TAXONOMY_VERSION,
        "record_schema_version": artifacts.RECORD_SCHEMA_VERSION,
        "reward_fn_id": cfg["reward"]["id"],
        "status": "failed",
        "input": {"files": [{
            "name": os.path.basename(path)[:256],
            "sha256": file_hash,
            "bytes": os.path.getsize(path),
            "batch_id": None,
            "schema_version": None,
            "accepted": False,
            "reject_reason": artifacts.truncate(
                f"{exc.violation_code}: {exc.reason}", 500),
        }]},
        "config": {
            "sha256": cfg.sha256, "resolved": cfg.redacted,
            "offline": offline, "seed": seed,
            "providers_enabled": [], "providers_unavailable": [],
        },
        # Empty, not invented: a run that read no proposal consulted no corpus.
        "reference_data": [],
        "counts": {
            "proposals_in": 0, "verdicts_out": 0, "accepted": 0, "rejected": 0,
            "unverifiable": 0, "quarantined": 0,
        },
        "aggregates": {"code_histogram": {exc.violation_code: 1},
                       "field_histogram": {"": 1}},
        "outputs": [],
        "tool_versions": {
            "crucible": artifacts.CRUCIBLE_VERSION,
            "crucible_source_sha256": artifacts.source_tree_sha256(),
        },
        "provenance": {
            "logical_time": logical_time,
            "git_commit": _rejection_git[0], "git_dirty": _rejection_git[1],
            "environment": {},
        },
        "notes": [artifacts.truncate(
            f"Envelope rejected: {exc.violation_code}: {exc.reason}", 1000)],
    }
    artifacts.write_json(os.path.join(out_dir, "run_manifest.json"),
                         manifest, digits=digits)
    return RunResult(exit_code=3)
