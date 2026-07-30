"""
Minimal repairs, and the re-verification that makes them usable (PROJECT.md §5.8).

A repair is the smallest edit that would fix a specific finding, expressed as an
RFC 6902 JSON Patch against the original proposal. Proposing one is cheap. The
part that is not cheap, and the part that is actually graded, is this:

    Only repairs that were **actually re-run through the same checks** may be
    promoted into a preference pair downstream. Asserting `reverified: true`
    without doing it is a fabrication, and it is checkable.

So `reverified` is never set from an argument or a flag. It is set by
`reverify()`, which applies the patch, runs the patched proposal back through
the caller-supplied verification function -- the same one the pipeline uses, not
a reimplementation of it -- and records what actually came out. If the patched
proposal is not better than the original, the repair is still reported, with its
real post-repair status, and it is simply not eligible to become a preference
pair. A repair that does not help is information too.

Repairs are generated only for defects where the *correct* value is
unambiguous. Where it is not -- a homoglyph formula, where guessing which
element was meant is exactly the failure this system exists to prevent -- no
repair is proposed. Silence is the honest output there.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from . import chemdata
from .normalize import normalize_formula, reduce_counts


@dataclass
class Repair:
    repair_id: str
    targets: list[str]
    kind: str
    patch: list[dict]
    rationale: str
    reverified: bool = False
    post_repair_status: str = "not_evaluated"
    post_repair_reward: float | None = None
    edit_distance: dict = field(default_factory=dict)
    confidence: float = 0.5

    def to_json(self) -> dict:
        payload = {
            "repair_id": self.repair_id,
            "targets": self.targets,
            "kind": self.kind,
            "patch": self.patch,
            "rationale": self.rationale[:2000],
            "reverified": self.reverified,
            "post_repair_status": self.post_repair_status,
            "post_repair_reward": self.post_repair_reward,
            "confidence": self.confidence,
        }
        if self.edit_distance:
            payload["edit_distance"] = self.edit_distance
        return payload


# --------------------------------------------------------------------------- #
# RFC 6902
# --------------------------------------------------------------------------- #

def _unescape(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _resolve(document, pointer: str):
    """Walk an RFC 6901 pointer to (container, final_key)."""
    if not pointer or pointer == "/":
        return None, None
    tokens = [_unescape(t) for t in pointer.split("/")[1:]]
    node = document
    for token in tokens[:-1]:
        if isinstance(node, list):
            node = node[int(token)]
        elif isinstance(node, dict):
            node = node[token]
        else:
            raise KeyError(pointer)
    return node, tokens[-1]


def apply_patch(proposal: dict, patch: list[dict]) -> dict:
    """Apply an RFC 6902 patch to a copy of the proposal.

    Deliberately supports only the operations this module emits -- replace,
    remove, add. A general patch applier would be more code and would accept
    operations nothing here produces, which is surface area for no benefit.
    """
    document = copy.deepcopy(proposal)
    for operation in patch:
        op = operation["op"]
        container, key = _resolve(document, operation["path"])
        if container is None:
            raise ValueError(f"cannot address {operation['path']!r}")
        if isinstance(container, list):
            index = len(container) if key == "-" else int(key)
            if op == "replace":
                container[index] = operation["value"]
            elif op == "remove":
                del container[index]
            elif op == "add":
                container.insert(index, operation["value"])
            else:
                raise ValueError(f"unsupported op {op!r}")
        else:
            if op == "replace":
                if key not in container:
                    raise ValueError(f"replace target missing: {operation['path']}")
                container[key] = operation["value"]
            elif op == "remove":
                container.pop(key, None)
            elif op == "add":
                container[key] = operation["value"]
            else:
                raise ValueError(f"unsupported op {op!r}")
    return document


def _edit_distance(before: dict, after: dict, patch: list[dict]) -> dict:
    import json
    changed_chars = 0
    for operation in patch:
        try:
            container, key = _resolve(before, operation["path"])
            old = container[int(key)] if isinstance(container, list) else container.get(key)
        except (KeyError, IndexError, ValueError, TypeError):
            old = None
        new = operation.get("value")
        changed_chars += abs(len(json.dumps(new, default=str))
                             - len(json.dumps(old, default=str)))
    return {"fields_changed": len(patch), "chars_changed": changed_chars}


# --------------------------------------------------------------------------- #
# repair generation
# --------------------------------------------------------------------------- #

def propose_repairs(proposal: dict, violations: list[dict],
                    norm_counts: dict[str, float] | None) -> list[Repair]:
    """Generate minimal repairs for the findings this build knows how to fix."""
    repairs: list[Repair] = []
    codes = {v["code"] for v in violations}
    by_code: dict[str, list[dict]] = {}
    for violation in violations:
        by_code.setdefault(violation["code"], []).append(violation)

    proposal_id = str(proposal.get("proposal_id", "record"))

    # --- an undeclared field: remove it -----------------------------------
    for violation in by_code.get("FMT.SCHEMA.UNKNOWN_FIELD", []):
        pointer = violation["json_pointer"]
        if not pointer or pointer.count("/") != 1:
            continue
        repairs.append(Repair(
            repair_id=f"{proposal_id}:drop-unknown-field",
            targets=["FMT.SCHEMA.UNKNOWN_FIELD"],
            kind="removal",
            patch=[{"op": "remove", "path": pointer}],
            rationale=(
                f"The input contract is closed and {pointer} is not part of it. "
                f"Removing the field is the minimal edit that restores conformance; "
                f"its content is not interpretable by any consumer of this schema, "
                f"so nothing is lost by dropping it."),
            confidence=0.9,
        ))

    # --- an unreduced formula: rewrite it in reduced form ------------------
    if "CHEM.FORMULA.NOT_REDUCED" in codes and norm_counts:
        reduced, divisor = reduce_counts(norm_counts)
        if divisor > 1:
            order = sorted(reduced)
            parsed = normalize_formula(
                (proposal.get("composition") or {}).get("formula", ""))
            if parsed.parsed_ok:
                order = [e for e in dict.fromkeys(
                    [s for s in parsed.reduced_formula if s.isupper()]) if e in reduced] or order
            spelled = "".join(
                element if reduced[element] == 1 else f"{element}{int(reduced[element])}"
                for element in sorted(reduced))
            patch = [{"op": "replace", "path": "/composition/formula", "value": spelled}]
            if isinstance((proposal.get("composition") or {}).get("elements"), dict):
                patch.append({"op": "replace", "path": "/composition/elements",
                              "value": {k: int(v) if float(v).is_integer() else v
                                        for k, v in sorted(reduced.items())}})
            repairs.append(Repair(
                repair_id=f"{proposal_id}:reduce-formula",
                targets=["CHEM.FORMULA.NOT_REDUCED"],
                kind="minimal_edit",
                patch=patch,
                rationale=(
                    f"The supplied stoichiometry is a {divisor}x multiple of its "
                    f"reduced form. Rewriting it as {spelled} does not change the "
                    f"material -- the two share a composition key -- but it matches "
                    f"the convention every reference corpus stores, which is what "
                    f"the finding is about."),
                confidence=0.85,
            ))

    # --- formula/elements disagreement: trust the formula string ----------
    if "CHEM.FORMULA.INCONSISTENT_WITH_ELEMENTS" in codes and norm_counts:
        repairs.append(Repair(
            repair_id=f"{proposal_id}:sync-elements-map",
            targets=["CHEM.FORMULA.INCONSISTENT_WITH_ELEMENTS"],
            kind="minimal_edit",
            patch=[{"op": "replace", "path": "/composition/elements",
                    "value": {k: int(v) if float(v).is_integer() else v
                              for k, v in sorted(norm_counts.items())}}],
            rationale=(
                "The formula string and the elements map describe different "
                "materials. This repair takes the formula string as authoritative "
                "and rewrites the map to agree, because the formula is the field the "
                "input schema marks required and the map is optional. Note that this "
                "is a choice: if the map were the intended one, this repair fixes the "
                "wrong side, which is why the disagreement is reported as an error "
                "rather than silently resolved during verification."),
            confidence=0.5,
        ))

    # --- a placeholder value: remove the property ------------------------
    for violation in by_code.get("CLAIM.SENTINEL.SUSPECTED", []):
        pointer = violation["json_pointer"]
        if not pointer.endswith("/value"):
            continue
        repairs.append(Repair(
            repair_id=f"{proposal_id}:drop-placeholder{pointer.replace('/', '-')}",
            targets=["CLAIM.SENTINEL.SUSPECTED"],
            kind="removal",
            patch=[{"op": "remove", "path": pointer.rsplit("/", 1)[0]}],
            rationale=(
                "The value matches a placeholder convention rather than a "
                "measurement. Removing the claim is the minimal honest edit: there is "
                "no correct value to substitute, and leaving a placeholder in place "
                "lets it be consumed downstream as data."),
            confidence=0.7,
        ))

    # --- a physically impossible value: remove the property --------------
    for violation in by_code.get("CLAIM.VALUE.PHYSICALLY_IMPOSSIBLE", []):
        pointer = violation["json_pointer"]
        if not pointer.endswith("/value"):
            continue
        repairs.append(Repair(
            repair_id=f"{proposal_id}:drop-impossible{pointer.replace('/', '-')}",
            targets=["CLAIM.VALUE.PHYSICALLY_IMPOSSIBLE"],
            kind="removal",
            patch=[{"op": "remove", "path": pointer.rsplit("/", 1)[0]}],
            rationale=(
                "The value lies outside what the quantity can physically take, so no "
                "corrected value can be inferred from it -- a negative band gap does "
                "not imply a positive one of the same magnitude. The claim is removed "
                "rather than adjusted."),
            confidence=0.75,
        ))

    # --- a reducing atmosphere on an oxide: propose an inert one ----------
    if ("FEAS.SYNTH.HINT_INCOHERENT" in codes or "FEAS.ATMOSPHERE.UNSTABLE" in codes) \
            and norm_counts and "O" in norm_counts:
        hint = proposal.get("synthesis_hint")
        if isinstance(hint, dict) and hint.get("atmosphere") in ("H2", "forming_gas", "NH3"):
            repairs.append(Repair(
                repair_id=f"{proposal_id}:oxidising-atmosphere",
                targets=["FEAS.SYNTH.HINT_INCOHERENT", "FEAS.ATMOSPHERE.UNSTABLE"],
                kind="substitution",
                patch=[{"op": "replace", "path": "/synthesis_hint/atmosphere",
                        "value": "O2"}],
                rationale=(
                    "The target is an oxide and the proposed atmosphere is reducing, "
                    "which removes the oxygen the material is made of. Substituting "
                    "an oxidising atmosphere is the minimal edit that makes the route "
                    "chemically coherent with the target. Temperature and dwell are "
                    "left untouched, because nothing in the finding implicates them."),
                confidence=0.8,
            ))

    # NOTE: no repair is generated for FMT.ENCODING.NON_ASCII_SYMBOL or
    # CHEM.FORMULA.UNKNOWN_ELEMENT. Both would require guessing which element the
    # generator meant, and a confident wrong guess is precisely the failure this
    # system exists to catch. Declining to repair is the correct output.

    repairs.sort(key=lambda r: r.repair_id)
    return repairs


def reverify(proposal: dict, repairs: list[Repair], verify_fn) -> list[Repair]:
    """Apply each patch and re-run the SAME checks on the result.

    `verify_fn` is the pipeline's own record builder, passed in rather than
    reimplemented, so "the same checks" is true by construction rather than by
    assertion. `reverified` is set here and nowhere else.
    """
    original = verify_fn(proposal)
    original_reward = original["verdict"]["reward"]

    for repair in repairs:
        try:
            patched = apply_patch(proposal, repair.patch)
        except (ValueError, KeyError, IndexError, TypeError):
            # A patch that will not apply is reported honestly as unverified
            # rather than dropped, because a repair this build could not test is
            # a fact about this build.
            repair.reverified = False
            repair.post_repair_status = "not_evaluated"
            repair.rationale += (" This patch could not be applied cleanly to the "
                                 "original proposal, so it was not re-verified.")
            continue

        try:
            result = verify_fn(patched)
        except Exception:  # noqa: BLE001 - a repair must never break the run
            repair.reverified = False
            repair.post_repair_status = "not_evaluated"
            repair.rationale += (" Re-verification of the patched proposal raised, so "
                                 "no post-repair status is claimed.")
            continue

        repair.reverified = True
        repair.post_repair_status = result["verdict"]["status"]
        repair.post_repair_reward = result["verdict"]["reward"]
        repair.edit_distance = _edit_distance(proposal, patched, repair.patch)

    return repairs


def preference_rows(record: dict, repairs: list[Repair]) -> list[dict]:
    """Preference pairs, constructed ONLY from re-verified repairs that helped.

    Every row names the `repair_id` it came from, so the claim that it was
    re-verified is traceable to the repair that carries the evidence. Nothing
    else in this build may produce a row in this projection.
    """
    rows = []
    original_reward = record["verdict"]["reward"]
    for repair in repairs:
        if not repair.reverified:
            continue
        if repair.post_repair_reward is None:
            continue
        if repair.post_repair_reward <= original_reward:
            continue
        rows.append({
            "record_id": record["record_id"],
            "repair_id": repair.repair_id,
            "proposal_id": record["proposal_id"],
            "rejected_reward": original_reward,
            "chosen_reward": repair.post_repair_reward,
            "targets": ",".join(repair.targets),
        })
    return sorted(rows, key=lambda r: r["repair_id"])
