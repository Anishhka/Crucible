"""
Composition-level validity (PROJECT.md §5.4).

Two screens: charge neutrality under some assignment of common oxidation states,
and electronegativity ordering consistent with the implied cation/anion
assignment. Plus agreement between the formula string and any declared element
map, which is where a generator that emits both often contradicts itself.

The judgment that matters here is severity, and it is made explicitly in
ASSUMPTIONS.md §7.6: **a charge-neutrality failure is a warning, never a gate.**
Recent benchmark work puts the false-positive rate of this screen at roughly one
in six *real reference structures*, concentrated in alloys, mixed-valence
compounds and f-block chemistry. Treating its output as a verdict discards real
materials -- MgB2 in the public baseline fixture is a famous superconductor with
no charge-neutral ionic assignment.

The blind spot is therefore encoded generically, by bonding class, in
chemdata.is_metallic_bonding_class. Special-casing MgB2 by name would pass the
public fixture and fail on the unseen one, which contains different members of
the same class.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product

from . import chemdata

IONIC_MODEL_SCOPE = (
    "Charge balance is screened with a formal ionic model against a curated table "
    "of commonly-observed oxidation states. The model is not expected to apply to "
    "metals, alloys, intermetallics, or the metal-boride / silicide / carbide / "
    "phosphide class; those are reported not_applicable rather than failed."
)


@dataclass
class CompositionFinding:
    code: str
    json_pointer: str
    message: str
    observed: object | None = None
    expected: object | None = None
    level: str = "warning"


@dataclass
class ChargeBalanceResult:
    applicable: bool
    balanced: bool | None
    rationale: str
    oxidation_states: list[dict] | None = None


@dataclass
class CompositionResult:
    charge: ChargeBalanceResult
    findings: list[CompositionFinding] = field(default_factory=list)
    measured: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(f.level == "error" for f in self.findings)


def check_charge_balance(counts: dict[str, float], cfg: dict) -> ChargeBalanceResult:
    """Is there ANY assignment of common oxidation states summing to zero?"""
    elements = sorted(counts)

    if not elements:
        return ChargeBalanceResult(False, None, "No composition to screen.")

    if chemdata.is_metallic_bonding_class(elements):
        return ChargeBalanceResult(
            False, None,
            "Composition falls in the metallic / intermetallic or metal-boride, "
            "-silicide, -carbide, -phosphide class, where a formal ionic "
            "charge-balance model is known not to apply -- the MgB2 case. Reported "
            "as not_applicable rather than as a failure, because a screen that "
            "treats its own known blind spot as fatal discards materials that "
            "demonstrably exist. " + IONIC_MODEL_SCOPE,
        )

    unknown = [e for e in elements if not chemdata.common_oxidation_states(e)]
    if unknown:
        return ChargeBalanceResult(
            False, None,
            f"No curated oxidation-state data for {', '.join(sorted(unknown))}; the "
            f"charge-balance screen was skipped rather than run with a guessed "
            f"value. " + IONIC_MODEL_SCOPE,
        )

    if not all(float(v).is_integer() for v in counts.values()):
        return ChargeBalanceResult(
            False, None,
            "Non-integer stoichiometric coefficients present. An exact "
            "combinatorial charge-balance search is not meaningful for a "
            "fractional-occupancy composition, so it was not attempted.",
        )

    options = [chemdata.common_oxidation_states(e) for e in elements]
    combinations = 1
    for opt in options:
        combinations *= len(opt)
    if combinations > cfg["max_oxidation_state_combinations"]:
        return ChargeBalanceResult(
            False, None,
            f"Composition admits {combinations} candidate oxidation-state "
            f"combinations, above this check's budget of "
            f"{cfg['max_oxidation_state_combinations']}. Skipped rather than "
            f"truncated silently, because a truncated search that finds nothing is "
            f"indistinguishable from a complete one that finds nothing.",
        )

    int_counts = {e: int(counts[e]) for e in elements}
    for combo in product(*options):
        if sum(state * int_counts[el] for el, state in zip(elements, combo)) == 0:
            return ChargeBalanceResult(
                True, True,
                "A charge-neutral assignment of common oxidation states exists for "
                "this composition.",
                [{"element": el, "state": state} for el, state in zip(elements, combo)],
            )

    return ChargeBalanceResult(
        True, False,
        "No combination of each element's commonly-observed oxidation states sums "
        "to zero net charge. This flags the composition for review; it is not proof "
        "the material cannot exist, because uncommon and mixed valences are excluded "
        "from the search by design. " + IONIC_MODEL_SCOPE,
    )


def check_electronegativity_ordering(counts: dict[str, float],
                                     charge: ChargeBalanceResult) -> CompositionFinding | None:
    """Is the implied cation/anion assignment consistent with electronegativity?

    Only meaningful when an ionic assignment was actually found, so it does not
    fire on the metallic class where the roles are not defined.
    """
    if not charge.applicable or not charge.oxidation_states:
        return None

    states = {entry["element"]: entry["state"] for entry in charge.oxidation_states}
    anions = sorted(e for e, s in states.items() if s < 0)
    cations = sorted(e for e, s in states.items() if s > 0)
    if not anions or not cations:
        return None

    most = chemdata.most_electronegative(sorted(counts))
    if most is None or most in anions:
        return None

    en_anion = max((chemdata.electronegativity(a) or 0.0) for a in anions)
    en_most = chemdata.electronegativity(most) or 0.0
    if en_most <= en_anion:
        return None

    return CompositionFinding(
        "CHEM.ELECTRONEG.IMBALANCED", "/composition/formula",
        f"The charge-neutral assignment makes {', '.join(anions)} the anion(s), but "
        f"{most} is the most electronegative element present "
        f"({en_most:.2f} vs {en_anion:.2f} on the Pauling scale). The implied "
        f"cation/anion roles are inconsistent with electronegativity ordering.",
        observed=f"{most} assigned state {states.get(most)}",
        expected=f"{most} as the anion", level="warning",
    )


def check_declared_elements(proposal: dict, counts: dict[str, float]) -> CompositionFinding | None:
    """Does the optional `elements` map agree with the parsed formula?

    When both are present they may disagree, and that disagreement is itself the
    finding. Silently preferring whichever is read first produces a confident
    answer about the wrong material.
    """
    declared = (proposal.get("composition") or {}).get("elements")
    if not isinstance(declared, dict) or not declared:
        return None

    parsed = {k: float(v) for k, v in counts.items()}
    given = {}
    for key, value in declared.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            given[key] = float(value)

    if not given:
        return None

    differences = []
    for element in sorted(set(parsed) | set(given)):
        a = parsed.get(element)
        b = given.get(element)
        if a is None:
            differences.append(f"{element}: absent from formula, {b:g} in elements map")
        elif b is None:
            differences.append(f"{element}: {a:g} in formula, absent from elements map")
        elif abs(a - b) > 1e-9:
            differences.append(f"{element}: {a:g} in formula, {b:g} in elements map")

    if not differences:
        return None

    return CompositionFinding(
        "CHEM.FORMULA.INCONSISTENT_WITH_ELEMENTS", "/composition/elements",
        "The declared elements map disagrees with the parsed formula string ("
        + "; ".join(differences)
        + "). Only one of them can describe the intended material, and a pipeline "
          "that silently prefers whichever it reads first produces a confident "
          "answer about the wrong one.",
        observed="; ".join(differences)[:200],
        expected="the elements map to agree with the formula",
        level="error",
    )


def check_composition(proposal: dict, counts: dict[str, float] | None,
                      cfg: dict) -> CompositionResult:
    if not counts:
        return CompositionResult(
            ChargeBalanceResult(False, None, "No parsed composition to screen."), [], {},
        )

    findings: list[CompositionFinding] = []
    charge = check_charge_balance(counts, cfg)

    if charge.applicable and charge.balanced is False:
        findings.append(CompositionFinding(
            "CHEM.CHARGE.NOT_NEUTRAL", "/composition/formula",
            charge.rationale,
            observed="no neutral assignment found",
            expected="a charge-neutral assignment of common oxidation states",
            # Warning, never error: see ASSUMPTIONS.md §7.6.
            level=cfg["charge_neutrality_severity"],
        ))

    en_finding = check_electronegativity_ordering(counts, charge)
    if en_finding:
        findings.append(en_finding)

    declared_finding = check_declared_elements(proposal, counts)
    if declared_finding:
        findings.append(declared_finding)

    measured = {
        "n_elements": len(counts),
        "charge_model_applicable": charge.applicable,
        "charge_balanced": charge.balanced,
    }
    if charge.oxidation_states:
        measured["oxidation_states"] = {
            entry["element"]: entry["state"] for entry in charge.oxidation_states
        }

    return CompositionResult(charge, findings, measured)
