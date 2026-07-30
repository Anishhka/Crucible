"""
`crucible verify --out DIR` (CONTRACT.md §2, PROJECT.md §5.10).

Re-derives, from an output directory alone, every invariant that can be checked
without re-running anything. It answers a different question from `run`: not
"is this batch any good" but **"is this artifact set self-consistent?"**

It MUST NOT re-run the pipeline, and it does not: nothing here imports the
verification modules, consults a corpus, or looks at the input batch. The only
inputs are the files in the directory.

What is re-derived:

  1. every artifact validates against its schema
  2. the manifest's count identities, including proposals_in = verdicts_out
     + quarantined and the status counts summing to verdicts_out
  3. every sha256 in outputs[] matches the file on disk
  4. every violation row references a verdict that exists, and carries the same
     candidate_key as that verdict
  5. every figure referenced by a verdict exists and has its declared companion
  6. every reward equals the value implied by its own recorded components
  7. feedback projections are consistent with the verdicts they claim to project
  8. ordering and JSON-formatting rules hold
  9. gates_failed agrees with the components it summarises
 10. no absolute host path leaked into a graded artifact
"""

from __future__ import annotations

import json
import math
import os

GRADED_ARTIFACTS = ("verdicts.jsonl", "violations.jsonl", "data_quality.json",
                    "run_manifest.json")
SCHEMA_DIR = os.path.join(os.path.dirname(__file__), "schemas")

_HOST_PATH_MARKERS = ("/data/in/", "/data/out/", "/Users/", "/home/", "/root/", "C:\\")


def _sha256_file(path: str) -> str:
    import hashlib
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_jsonl(path: str) -> tuple[list[dict], list[str]]:
    problems: list[str] = []
    rows: list[dict] = []
    if not os.path.exists(path):
        return rows, problems
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw and not raw.endswith(b"\n"):
        problems.append(f"{os.path.basename(path)}: file does not end with a newline")
    text = raw.decode("utf-8")
    for index, line in enumerate(text.split("\n")[:-1] if text else [], start=1):
        if not line.strip():
            problems.append(f"{os.path.basename(path)}: line {index} is blank; "
                            f"JSONL files must contain no blank lines")
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            problems.append(f"{os.path.basename(path)}: line {index} is not JSON: {exc}")
    return rows, problems


def _expected_reward(components: dict) -> float:
    """The contract's arithmetic, re-derived from the components alone.

    reward = (product over gates) x (sum of value*weight over non-gates)
             / (sum of weight over non-gates)
    """
    gate_product = 1.0
    weighted = 0.0
    weight_total = 0.0
    for component in components.values():
        if component.get("gate"):
            gate_product *= float(component["value"])
        else:
            weighted += float(component["value"]) * float(component["weight"])
            weight_total += float(component["weight"])
    factor = (weighted / weight_total) if weight_total > 0 else 1.0
    return gate_product * factor


def _validate_schemas(out_dir: str, problems: list[str]) -> None:
    try:
        from jsonschema import Draft202012Validator
    except ImportError:  # pragma: no cover - jsonschema is a pinned dependency
        problems.append("jsonschema is not importable, so schema conformance could "
                        "not be re-checked")
        return

    targets = [
        ("verdicts.jsonl", "verdict.schema.json", True),
        ("violations.jsonl", "violation.schema.json", True),
        ("run_manifest.json", "run_manifest.schema.json", False),
        ("data_quality.json", "data_quality.schema.json", False),
        (os.path.join("feedback", "index.json"), "feedback_bundle.schema.json", False),
    ]
    for relative, schema_file, is_jsonl in targets:
        path = os.path.join(out_dir, relative)
        if not os.path.exists(path):
            continue
        with open(os.path.join(SCHEMA_DIR, schema_file), "r", encoding="utf-8") as fh:
            validator = Draft202012Validator(json.load(fh))
        if is_jsonl:
            rows, jsonl_problems = _read_jsonl(path)
            problems.extend(jsonl_problems)
            for index, row in enumerate(rows, start=1):
                for error in validator.iter_errors(row):
                    problems.append(
                        f"{relative}: line {index}: "
                        f"{'/'.join(map(str, error.absolute_path))}: {error.message}")
        else:
            with open(path, "r", encoding="utf-8") as fh:
                try:
                    payload = json.load(fh)
                except json.JSONDecodeError as exc:
                    problems.append(f"{relative}: not valid JSON: {exc}")
                    continue
            for error in validator.iter_errors(payload):
                problems.append(
                    f"{relative}: {'/'.join(map(str, error.absolute_path))}: "
                    f"{error.message}")


def verify_output_dir(out_dir: str) -> tuple[bool, list[str]]:
    problems: list[str] = []

    if not os.path.isdir(out_dir):
        return False, [f"{out_dir!r} is not a directory"]

    manifest_path = os.path.join(out_dir, "run_manifest.json")
    if not os.path.exists(manifest_path):
        return False, ["run_manifest.json is missing; nothing else can be checked "
                       "without it"]
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except json.JSONDecodeError as exc:
        return False, [f"run_manifest.json is not valid JSON: {exc}"]

    # ---- 1. schemas -------------------------------------------------------
    _validate_schemas(out_dir, problems)

    verdicts, verdict_problems = _read_jsonl(os.path.join(out_dir, "verdicts.jsonl"))
    violations, violation_problems = _read_jsonl(os.path.join(out_dir, "violations.jsonl"))
    problems.extend(verdict_problems)
    problems.extend(violation_problems)

    counts = manifest.get("counts", {})
    envelope_rejection = manifest.get("status") == "failed"

    # ---- 2. count identities ---------------------------------------------
    try:
        left = counts["proposals_in"]
        right = counts["verdicts_out"] + counts["quarantined"]
        if left != right:
            problems.append(
                f"count identity violated: proposals_in={left} != "
                f"verdicts_out+quarantined={right}; the run lost or invented records")
    except (KeyError, TypeError):
        problems.append(f"manifest counts are missing or malformed: {counts!r}")

    if not envelope_rejection:
        terminal = (counts.get("accepted", 0) + counts.get("rejected", 0)
                    + counts.get("unverifiable", 0))
        if terminal != counts.get("verdicts_out"):
            problems.append(
                f"status counts do not sum to verdicts_out: "
                f"accepted+rejected+unverifiable={terminal}, "
                f"verdicts_out={counts.get('verdicts_out')}")
        if len(verdicts) != counts.get("verdicts_out"):
            problems.append(
                f"verdicts.jsonl holds {len(verdicts)} rows but the manifest says "
                f"verdicts_out={counts.get('verdicts_out')}")

        reference = manifest.get("reference_data")
        if not reference:
            problems.append(
                "reference_data is empty on a run that produced verdicts; a novelty "
                "claim without the corpus version it is relative to is not a "
                "reproducible claim")
        else:
            for row in reference:
                if str(row.get("snapshot", "")).lower() in ("", "latest", "none", "null"):
                    problems.append(
                        f"reference_data entry {row.get('id')!r} has an unpinned "
                        f"snapshot; 'latest' is not a version")

    # ---- 3. output hashes -------------------------------------------------
    for entry in manifest.get("outputs", []):
        path = os.path.join(out_dir, entry["path"])
        if ".." in entry["path"] or os.path.isabs(entry["path"]):
            problems.append(f"outputs[] path escapes the output directory: {entry['path']!r}")
            continue
        if not os.path.exists(path):
            problems.append(f"listed output is missing on disk: {entry['path']}")
            continue
        actual = _sha256_file(path)
        if actual != entry["sha256"]:
            problems.append(
                f"hash mismatch for {entry['path']}: manifest records "
                f"{entry['sha256'][:16]}..., file on disk hashes to {actual[:16]}...")

    # ---- 4. referential integrity ----------------------------------------
    by_record = {r["record_id"]: r for r in verdicts}
    for index, row in enumerate(violations, start=1):
        record = by_record.get(row.get("record_id"))
        if record is None:
            problems.append(
                f"violations.jsonl line {index} references record_id "
                f"{row.get('record_id')!r}, which no verdict declares")
            continue
        if row.get("candidate_key") != record.get("candidate_key"):
            problems.append(
                f"violations.jsonl line {index} carries a candidate_key that "
                f"disagrees with its verdict record")
        if row.get("proposal_id") != record.get("proposal_id"):
            problems.append(
                f"violations.jsonl line {index} carries a proposal_id that "
                f"disagrees with its verdict record")

    # every violation on a verdict must also appear as a row
    row_keys = {(r.get("record_id"), r.get("code"), r.get("json_pointer"))
                for r in violations}
    for record in verdicts:
        for violation in record.get("violations", []):
            key = (record["record_id"], violation["code"], violation["json_pointer"])
            if key not in row_keys:
                problems.append(
                    f"verdict {record['record_id']} declares {violation['code']} at "
                    f"{violation['json_pointer']} but violations.jsonl has no such row")

    # ---- 5. figures and companions ---------------------------------------
    for record in verdicts:
        for artifact in record.get("artifacts", []):
            for key in ("path", "data_companion"):
                relative = artifact.get(key)
                if not relative:
                    if key == "data_companion":
                        problems.append(
                            f"verdict {record['record_id']} references figure "
                            f"{artifact.get('path')!r} with no data companion; the "
                            f"companion is the graded artifact")
                    continue
                if ".." in relative or os.path.isabs(relative):
                    problems.append(f"artifact path escapes the output directory: {relative!r}")
                    continue
                if not os.path.exists(os.path.join(out_dir, relative)):
                    problems.append(
                        f"verdict {record['record_id']} references {relative}, "
                        f"which does not exist")

    # ---- 6. rewards re-derived from their own components ------------------
    for record in verdicts:
        verdict = record.get("verdict", {})
        components = verdict.get("reward_components") or {}
        if not components:
            problems.append(f"verdict {record['record_id']} records no reward components")
            continue
        expected = _expected_reward(components)
        actual = float(verdict.get("reward", -1))
        if not math.isclose(expected, actual, rel_tol=1e-6, abs_tol=1e-6):
            problems.append(
                f"verdict {record['record_id']}: reward {actual} does not equal "
                f"{expected} implied by its own components")

        declared_gates = sorted(verdict.get("gates_failed") or [])
        derived_gates = sorted(name for name, component in components.items()
                               if component.get("gate") and float(component["value"]) == 0.0)
        if declared_gates != derived_gates:
            problems.append(
                f"verdict {record['record_id']}: gates_failed={declared_gates} but "
                f"the components imply {derived_gates}")

        if any(float(c["value"]) == 0.0 for c in components.values() if c.get("gate")) \
                and actual != 0.0:
            problems.append(
                f"verdict {record['record_id']}: a gate component is 0 but the "
                f"reward is {actual}, which the contract forbids")

    # ---- 7. feedback projections -----------------------------------------
    feedback_path = os.path.join(out_dir, "feedback", "index.json")
    if os.path.exists(feedback_path):
        with open(feedback_path, "r", encoding="utf-8") as fh:
            try:
                feedback = json.load(fh)
            except json.JSONDecodeError as exc:
                feedback = None
                problems.append(f"feedback/index.json is not valid JSON: {exc}")
        if feedback:
            if feedback.get("run_id") != manifest.get("run_id"):
                problems.append("feedback/index.json declares a different run_id "
                                "from the manifest")
            for projection in feedback.get("projections", []):
                relative = projection["path"]
                path = os.path.join(out_dir, relative)
                if not os.path.exists(path):
                    problems.append(f"projection {projection['name']} lists "
                                    f"{relative}, which does not exist")
                    continue
                rows, projection_problems = _read_jsonl(path)
                problems.extend(projection_problems)
                if len(rows) != projection["n_rows"]:
                    problems.append(
                        f"projection {projection['name']} declares "
                        f"n_rows={projection['n_rows']} but the file holds {len(rows)}")
                if "record_id" not in projection.get("row_spec", []):
                    problems.append(
                        f"projection {projection['name']} row_spec omits record_id, "
                        f"so its rows cannot be traced to a canonical record")
                excluded = set(projection.get("eligibility", {}).get("excluded_statuses", []))
                for row_index, row in enumerate(rows, start=1):
                    if "record_id" not in row:
                        problems.append(
                            f"projection {projection['name']} row {row_index} has no "
                            f"record_id")
                        continue
                    record = by_record.get(row["record_id"])
                    if record is None:
                        problems.append(
                            f"projection {projection['name']} row {row_index} "
                            f"references a record no verdict declares")
                        continue
                    status = record["verdict"]["status"]
                    if status in excluded:
                        problems.append(
                            f"projection {projection['name']} row {row_index} projects "
                            f"a record whose status {status!r} the projection "
                            f"declares excluded")
                    if projection["name"] == "scalar_reward" and "reward" in row:
                        if not math.isclose(float(row["reward"]),
                                            float(record["verdict"]["reward"]),
                                            rel_tol=1e-9, abs_tol=1e-9):
                            problems.append(
                                f"scalar_reward row {row_index} disagrees with the "
                                f"reward on the record it projects")
                if projection["name"] == "preference_pairs":
                    for row_index, row in enumerate(rows, start=1):
                        if "repair_id" not in row:
                            problems.append(
                                f"preference_pairs row {row_index} has no repair_id; "
                                f"preference pairs may be built only from re-verified "
                                f"repairs")

    # ---- 8. ordering and formatting --------------------------------------
    verdict_order = [(r["proposal_id"].encode("utf-8"), r["candidate_key"].encode("utf-8"))
                     for r in verdicts]
    if verdict_order != sorted(verdict_order):
        problems.append("verdicts.jsonl is not sorted by (proposal_id, candidate_key) "
                        "under byte-wise UTF-8 ordering")

    violation_order = [
        (r["proposal_id"].encode("utf-8"), r["code"].encode("utf-8"),
         r["json_pointer"].encode("utf-8"), r["candidate_key"].encode("utf-8"))
        for r in violations]
    if violation_order != sorted(violation_order):
        problems.append("violations.jsonl is not sorted by "
                        "(proposal_id, code, json_pointer, candidate_key)")

    # ---- 9. required check phases ----------------------------------------
    required_phases = {"parse", "composition", "structure", "novelty", "claims"}
    for record in verdicts:
        present = {check["phase"] for check in record.get("checks", [])}
        missing = required_phases - present
        if missing:
            problems.append(
                f"verdict {record['record_id']} has no check for phase(s) "
                f"{sorted(missing)}; a phase that is simply absent cannot be told "
                f"apart from one that was forgotten")

    # ---- 10. no host paths in graded artifacts ---------------------------
    for name in GRADED_ARTIFACTS:
        path = os.path.join(out_dir, name)
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        if name == "run_manifest.json":
            try:
                payload = json.loads(text)
                payload.get("provenance", {}).pop("environment", None)
                text = json.dumps(payload)
            except json.JSONDecodeError:
                pass
        for marker in _HOST_PATH_MARKERS:
            if marker in text:
                problems.append(f"{name} contains what looks like a host path: {marker!r}")

    report_path = os.path.join(out_dir, "report.html")
    if os.path.exists(report_path):
        with open(report_path, "r", encoding="utf-8", errors="replace") as fh:
            report_text = fh.read()
        for scheme in ("http://", "https://"):
            if scheme in report_text:
                problems.append(
                    f"report.html contains {scheme!r}; the report must render with no "
                    f"network access at all, and a checker cannot tell a fetched "
                    f"asset from a hyperlink")

    return (not problems), problems
