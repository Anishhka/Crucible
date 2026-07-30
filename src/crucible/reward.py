"""
The decomposed reward (PROJECT.md §5.8, ASSUMPTIONS.md §7.7).

The arithmetic is fixed by the contract and is not a choice:

    reward = (product over gate components of value)
             x (sum over non-gate components of value x weight)
             / (sum over non-gate weights)

Non-gate weights need not sum to 1; they are normalised by their own total. If
there are no non-gate components that factor is 1. If any gate component is 0
the reward is 0. A third party can re-derive the scalar from the components
alone, which is what `crucible verify` does.

Components are stored raw alongside their weights so that a change of weights
never requires re-running verification.

Two decisions carry the weight here, both argued in ASSUMPTIONS.md §7.7:

  * **Only structural preconditions gate.** parse_ok, schema_valid and
    formula_valid. If a record could not be parsed, does not conform to the
    input contract, or has a formula that is not chemistry, then every
    downstream number about it is meaningless and it must not collect partial
    credit from a lucky novelty lookup. Nothing scientific gates -- not charge
    balance, not novelty, not feasibility -- because each has a known
    false-positive rate and a gate is unrecoverable.

  * **A component that could not be evaluated scores 0.5, not 0.** Scoring an
    unavailable lookup as zero trains the generator against our own
    infrastructure flakiness, which is worse than not training it at all. A
    component that does not *apply* -- structure checks on a proposal with no
    structure -- is omitted entirely instead, so the remaining weights
    renormalise rather than the proposal being penalised for an optional field
    it did not supply.
"""

from __future__ import annotations

from dataclasses import dataclass

# How much a novelty tier is worth. The objective is finding materials that are
# NOT already known, so strong prior art scores low: re-proposing hematite is a
# correct chemical statement and a useless generator output.
NOVELTY_VALUE = {
    "no_evidence": 1.0,
    "computed_only": 0.6,
    "literature_mention": 0.4,
    "experimentally_reported": 0.0,
    "unknown": None,  # -> unknown_component_value
}

FEASIBILITY_VALUE = {
    "plausible": 1.0,
    "marginal": 0.6,
    "implausible": 0.0,
    "unknown": None,
}

# Claim assessments that count against the generator. `unsupported` is not one
# of them: this build has no property reference corpus, so "uncorroborated" is a
# statement about our coverage, not about the claim.
BAD_CLAIM_ASSESSMENTS = {"contradicted", "implausible", "unit_error"}


@dataclass
class RewardResult:
    value: float
    components: dict[str, dict]
    gates_failed: list[str]


def _component(value: float, weight: float, gate: bool, rationale: str) -> dict:
    return {
        "value": max(0.0, min(1.0, float(value))),
        "weight": float(weight),
        "gate": bool(gate),
        "rationale": rationale[:500],
    }


def compute_reward(*, parse_ok: bool, schema_ok: bool, formula_ok: bool,
                   composition_result, structure_result, novelty_tier: str | None,
                   feasibility_verdict: str | None, claim_result,
                   in_scope: bool, cfg: dict) -> RewardResult:
    weights = cfg["components"]
    unknown_value = cfg["unknown_component_value"]
    components: dict[str, dict] = {}

    # --- gates --------------------------------------------------------------
    components["parse_ok"] = _component(
        1.0 if parse_ok else 0.0, weights["parse_ok"]["weight"], True,
        "The record was admitted at the envelope level and is well-formed JSON."
        if parse_ok else
        "The record could not be admitted at the envelope level, so nothing "
        "downstream about it is trustworthy.",
    )
    components["schema_valid"] = _component(
        1.0 if schema_ok else 0.0, weights["schema_valid"]["weight"], True,
        "The record conforms to proposals.schema.json."
        if schema_ok else
        "The record violates the input contract (undeclared field, missing "
        "required field, or a violated constraint), which means the generator and "
        "the verifier disagree about the contract itself.",
    )
    components["formula_valid"] = _component(
        1.0 if formula_ok else 0.0, weights["formula_valid"]["weight"], True,
        "The formula normalised to real element symbols."
        if formula_ok else
        "The formula could not be normalised to real element symbols, so every "
        "composition-dependent conclusion is undefined for this record.",
    )

    # --- composition --------------------------------------------------------
    if formula_ok and composition_result is not None:
        errors = [f for f in composition_result.findings if f.level == "error"]
        warnings = [f for f in composition_result.findings if f.level == "warning"]
        if errors:
            value, why = 0.0, ("Composition-level error: "
                               + "; ".join(f.code for f in errors))
        elif warnings:
            value, why = 0.5, (
                "Composition screens raised warnings ("
                + "; ".join(f.code for f in warnings)
                + "). Partial credit rather than zero: these screens have a known "
                  "false-positive rate on real materials, so a warning is a prompt "
                  "for review, not a verdict."
            )
        else:
            value, why = 1.0, "Charge balance and electronegativity ordering both pass."
        components["composition_valid"] = _component(
            value, weights["composition_valid"]["weight"], False, why)

    # --- structure ----------------------------------------------------------
    # Omitted entirely when no structure was supplied: the input schema makes it
    # optional, and scoring its absence would penalise a legitimate proposal.
    if structure_result is not None and structure_result.evaluated:
        errors = [f for f in structure_result.findings if f.level == "error"]
        warnings = [f for f in structure_result.findings if f.level == "warning"]
        if errors:
            value, why = 0.0, ("Structure-level error: "
                               + "; ".join(f.code for f in errors))
        elif warnings:
            value, why = 0.5, ("Structure warnings: "
                               + "; ".join(f.code for f in warnings))
        else:
            value, why = 1.0, ("Geometry, lattice, density, occupancy and asserted "
                               "symmetry are all self-consistent.")
        components["structure_valid"] = _component(
            value, weights["structure_valid"]["weight"], False, why)

    # --- novelty ------------------------------------------------------------
    if novelty_tier is not None:
        mapped = NOVELTY_VALUE.get(novelty_tier)
        if mapped is None:
            components["novelty"] = _component(
                unknown_value, weights["novelty"]["weight"], False,
                "Novelty tier is unknown because at least one provider could not "
                "answer. Scored neutral rather than zero: an unavailable lookup is "
                "not evidence about the generator.",
            )
        else:
            components["novelty"] = _component(
                mapped, weights["novelty"]["weight"], False,
                f"Novelty tier {novelty_tier}. Strong prior art scores low because "
                f"re-proposing a known compound is a correct statement and a "
                f"useless generator output.",
            )

    # --- feasibility --------------------------------------------------------
    if feasibility_verdict is not None:
        mapped = FEASIBILITY_VALUE.get(feasibility_verdict)
        if mapped is None:
            components["feasibility"] = _component(
                unknown_value, weights["feasibility"]["weight"], False,
                "Feasibility is unknown: no applicable screen ran, and this build "
                "computes no thermodynamics. Scored neutral rather than penalised.",
            )
        else:
            components["feasibility"] = _component(
                mapped, weights["feasibility"]["weight"], False,
                f"Feasibility screen returned {feasibility_verdict}. Capped below "
                f"1.0 for every proposal because no hull distance was computed.",
            )

    # --- claims -------------------------------------------------------------
    if claim_result is not None and claim_result.evaluated and claim_result.entries:
        total = len(claim_result.entries)
        bad = sum(1 for e in claim_result.entries
                  if e["assessment"] in BAD_CLAIM_ASSESSMENTS)
        value = (total - bad) / total
        components["claims_supported"] = _component(
            value, weights["claims_supported"]["weight"], False,
            f"{total - bad} of {total} claimed properties survived the audit; {bad} "
            f"were contradicted, implausible, or carried an unusable unit.",
        )

    # --- combine, exactly as the contract specifies -------------------------
    gate_product = 1.0
    non_gate_weighted = 0.0
    non_gate_weight_total = 0.0
    for component in components.values():
        if component["gate"]:
            gate_product *= component["value"]
        else:
            non_gate_weighted += component["value"] * component["weight"]
            non_gate_weight_total += component["weight"]

    non_gate_factor = (non_gate_weighted / non_gate_weight_total
                       if non_gate_weight_total > 0 else 1.0)
    value = gate_product * non_gate_factor

    gates_failed = sorted(k for k, c in components.items()
                          if c["gate"] and c["value"] == 0.0)

    return RewardResult(value=value, components=components, gates_failed=gates_failed)


def decide_status(*, gates_failed: list[str], any_unavailable: bool,
                  has_error_finding: bool, in_scope: bool) -> tuple[str, str]:
    """Reduce the checks to one of the three verdict statuses, with a summary.

    `unverifiable` is not a euphemism for `rejected`. It means the system
    declined to conclude, and those records are excluded from every downstream
    training projection -- a provider timeout is not evidence that a material is
    bad, and training a generator against infrastructure flakiness is worse than
    not training it at all.
    """
    if gates_failed:
        return ("rejected",
                "Rejected at a gate: " + ", ".join(gates_failed)
                + ". A record that fails a structural precondition collects no "
                  "partial credit from the checks that did run.")

    if not in_scope:
        return ("unverifiable",
                "Out of scope for this tool: the proposal is not an inorganic "
                "crystalline solid. Reported as out of scope with a stated reason "
                "rather than scored as an incorrect inorganic material, and "
                "excluded from training projections.")

    if any_unavailable:
        return ("unverifiable",
                "At least one check could not be completed, so no defensible "
                "terminal conclusion is available. This is not a statement about "
                "the material, and this record is excluded from every training "
                "projection.")

    if has_error_finding:
        return ("rejected",
                "Every required check ran and at least one returned an error-level "
                "finding.")

    return ("accepted",
            "Every required check ran and none returned an error-level finding.")
