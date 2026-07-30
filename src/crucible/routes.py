"""
Synthesis routes and assessment of the generator's own hint (PROJECT.md §5.6).

Two things happen here.

**Route proposal.** For each in-scope proposal, enumerate balanced reactions
from obtainable precursors -- binary oxides, carbonates, and the elements. The
equations are genuinely balanced by solving element counts, not decorative.

The scale of a balanced equation is arbitrary, so the convention that fixes it
is recorded on every reaction: **one formula unit of the target**. Without that,
two correct implementations disagree by a constant factor and neither is
reproducible.

**Driving forces are real where the thermochemical table covers every species in
the reaction**, computed as the enthalpy of reaction at 298.15 K normalised per
atom of product, and routes are ranked most-exothermic-first on that basis.
Where any species is uncovered the driving force is `null` with the missing
species named -- a route with no computable driving force is still a route, and
reporting it without one beats decorating it with a number nobody calculated.

One caveat travels with every computed value, and it is on the route's own
assumption list: these are enthalpies, not free energies at synthesis
temperature. A carbonate route is endothermic at 298 K and is driven by the
entropy of CO2 release in the furnace, so this number understates that whole
family badly. Reading a positive value as "this route does not work" would be a
misreading, which is why the caveat is attached to the data rather than left in
a document.

**Hint assessment.** The generator supplies its own guess at a synthesis. Echoing
it back is not assessment. What is checked: whether the named precursors are
real obtainable compounds, whether the proposed atmosphere is chemically
compatible with the target, and whether the conditions are self-consistent.

The atmosphere check is the one that earns its place. A copper-oxide
superconductor calcined under hydrogen is a real and common failure mode -- the
composition is right, the temperature is reasonable, and the atmosphere will
reduce the material to something else entirely. A verifier that checks only the
composition misses it completely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction

from . import chemdata, thermo
from .normalize import normalize_formula

# Reducing atmospheres: these will strip oxygen from an oxide at temperature.
REDUCING_ATMOSPHERES = {"H2", "forming_gas", "NH3"}
# Oxidising atmospheres: these will oxidise a nitride, sulfide or elemental metal.
OXIDISING_ATMOSPHERES = {"air", "O2"}

# Binary oxides used as precursors, by element. Chosen to be the ordinary
# laboratory starting materials rather than the thermodynamically preferred ones.
_BINARY_OXIDE = {
    "Li": ("Li2O", {"Li": 2, "O": 1}), "Na": ("Na2O", {"Na": 2, "O": 1}),
    "K": ("K2O", {"K": 2, "O": 1}), "Mg": ("MgO", {"Mg": 1, "O": 1}),
    "Ca": ("CaO", {"Ca": 1, "O": 1}), "Sr": ("SrO", {"Sr": 1, "O": 1}),
    "Ba": ("BaO", {"Ba": 1, "O": 1}), "Al": ("Al2O3", {"Al": 2, "O": 3}),
    "Si": ("SiO2", {"Si": 1, "O": 2}), "Ti": ("TiO2", {"Ti": 1, "O": 2}),
    "Zr": ("ZrO2", {"Zr": 1, "O": 2}), "Hf": ("HfO2", {"Hf": 1, "O": 2}),
    "V": ("V2O5", {"V": 2, "O": 5}), "Nb": ("Nb2O5", {"Nb": 2, "O": 5}),
    "Ta": ("Ta2O5", {"Ta": 2, "O": 5}), "Cr": ("Cr2O3", {"Cr": 2, "O": 3}),
    "Mo": ("MoO3", {"Mo": 1, "O": 3}), "W": ("WO3", {"W": 1, "O": 3}),
    "Mn": ("MnO2", {"Mn": 1, "O": 2}), "Fe": ("Fe2O3", {"Fe": 2, "O": 3}),
    "Co": ("Co3O4", {"Co": 3, "O": 4}), "Ni": ("NiO", {"Ni": 1, "O": 1}),
    "Cu": ("CuO", {"Cu": 1, "O": 1}), "Zn": ("ZnO", {"Zn": 1, "O": 1}),
    "Y": ("Y2O3", {"Y": 2, "O": 3}), "La": ("La2O3", {"La": 2, "O": 3}),
    "Ce": ("CeO2", {"Ce": 1, "O": 2}), "Sc": ("Sc2O3", {"Sc": 2, "O": 3}),
    "Ga": ("Ga2O3", {"Ga": 2, "O": 3}), "In": ("In2O3", {"In": 2, "O": 3}),
    "Sn": ("SnO2", {"Sn": 1, "O": 2}), "Pb": ("PbO", {"Pb": 1, "O": 1}),
    "Bi": ("Bi2O3", {"Bi": 2, "O": 3}), "P": ("P2O5", {"P": 2, "O": 5}),
    "B": ("B2O3", {"B": 2, "O": 3}), "Ge": ("GeO2", {"Ge": 1, "O": 2}),
}

# Carbonates, which release CO2 on heating. The gas is part of the reaction and
# part of the driving force, which is why it appears as a product.
_CARBONATE = {
    "Li": ("Li2CO3", {"Li": 2, "C": 1, "O": 3}),
    "Na": ("Na2CO3", {"Na": 2, "C": 1, "O": 3}),
    "K": ("K2CO3", {"K": 2, "C": 1, "O": 3}),
    "Mg": ("MgCO3", {"Mg": 1, "C": 1, "O": 3}),
    "Ca": ("CaCO3", {"Ca": 1, "C": 1, "O": 3}),
    "Sr": ("SrCO3", {"Sr": 1, "C": 1, "O": 3}),
    "Ba": ("BaCO3", {"Ba": 1, "C": 1, "O": 3}),
}


@dataclass
class RouteFinding:
    code: str
    json_pointer: str
    message: str
    observed: object | None = None
    expected: object | None = None
    level: str = "note"


@dataclass
class RouteResult:
    routes: list[dict] = field(default_factory=list)
    hint_assessment: dict | None = None
    findings: list[RouteFinding] = field(default_factory=list)


def _fmt(coefficient: Fraction) -> float:
    return float(coefficient)


def _species(formula: str, counts: dict[str, int]) -> dict:
    return {"formula": formula, "counts": counts}


def _balance(target: dict[str, float], precursors: list[tuple[str, dict[str, int]]],
             target_formula: str) -> tuple[list[dict], list[dict]] | None:
    """Balance one formula unit of the target against a precursor set.

    Each non-oxygen, non-carbon element is supplied by exactly one precursor, so
    its coefficient is fixed directly. Oxygen is then reconciled with O2, and
    carbon (from carbonates) leaves as CO2. That is the whole solve -- it is a
    triangular system by construction, which is why no matrix is needed and why
    the result is exact rational arithmetic rather than floating point.
    """
    reactants: list[dict] = []
    supplied_o = Fraction(0)
    supplied_c = Fraction(0)

    for formula, counts in precursors:
        element = next((e for e in counts if e not in ("O", "C")), None)
        if element is None or element not in target:
            continue
        coefficient = Fraction(target[element]).limit_denominator(10_000) / counts[element]
        if coefficient <= 0:
            return None
        reactants.append({"formula": formula, "coefficient": _fmt(coefficient)})
        supplied_o += coefficient * counts.get("O", 0)
        supplied_c += coefficient * counts.get("C", 0)

    if not reactants:
        return None

    products = [{"formula": target_formula, "coefficient": 1.0}]

    if supplied_c > 0:
        products.append({"formula": "CO2", "coefficient": _fmt(supplied_c)})
        supplied_o -= supplied_c * 2

    needed_o = Fraction(target.get("O", 0)).limit_denominator(10_000)
    delta = supplied_o - needed_o
    if delta > 0:
        products.append({"formula": "O2", "coefficient": _fmt(delta / 2)})
    elif delta < 0:
        reactants.append({"formula": "O2", "coefficient": _fmt(-delta / 2)})

    reactants.sort(key=lambda r: r["formula"])
    products.sort(key=lambda p: p["formula"])
    return reactants, products


def _target_formula(counts: dict[str, float]) -> str:
    parts = []
    for element in sorted(counts):
        value = counts[element]
        if float(value).is_integer():
            parts.append(element if int(value) == 1 else f"{element}{int(value)}")
        else:
            parts.append(f"{element}{value:g}")
    return "".join(parts)


def _verify_balanced(reactants: list[dict], products: list[dict],
                     lookup: dict[str, dict[str, int]], target: dict[str, float],
                     target_formula: str) -> bool:
    """Confirm the equation actually balances. Asserting `balanced: true` without
    checking is the kind of claim this whole system exists to catch."""
    def totals(side: list[dict], is_product: bool) -> dict[str, float]:
        acc: dict[str, float] = {}
        for term in side:
            formula = term["formula"]
            counts = (target if is_product and formula == target_formula
                      else lookup.get(formula))
            if counts is None:
                return {}
            for element, n in counts.items():
                acc[element] = acc.get(element, 0.0) + n * term["coefficient"]
        return acc

    left = totals(reactants, False)
    right = totals(products, True)
    if not left or not right or set(left) != set(right):
        return False
    return all(abs(left[e] - right[e]) < 1e-6 for e in left)


def propose_routes(counts: dict[str, float] | None, cfg: dict,
                   target_formula: str | None = None) -> list[dict]:
    if not counts:
        return []

    target_formula = target_formula or _target_formula(counts)
    metals = [e for e in sorted(counts) if e not in ("O", "C")]
    if not metals:
        return []

    lookup: dict[str, dict[str, int]] = {"O2": {"O": 2}, "CO2": {"C": 1, "O": 2}}
    routes: list[dict] = []

    families: list[tuple[str, list[tuple[str, dict[str, int]]], str]] = []

    if "O" in counts:
        oxide_set = [_BINARY_OXIDE[e] for e in metals if e in _BINARY_OXIDE]
        # A single-metal oxide whose own binary oxide IS the target would give
        # the degenerate route "1 TiO2 -> 1 TiO2", which is not a synthesis.
        degenerate = (len(oxide_set) == 1
                      and dict(oxide_set[0][1]) == {k: int(v) for k, v in counts.items()
                                                    if float(v).is_integer()})
        if len(oxide_set) == len(metals) and not degenerate:
            families.append(("binary_oxide", oxide_set,
                             "Conventional solid-state reaction from binary oxides."))
        carbonate_set = []
        for element in metals:
            if element in _CARBONATE:
                carbonate_set.append(_CARBONATE[element])
            elif element in _BINARY_OXIDE:
                carbonate_set.append(_BINARY_OXIDE[element])
        if len(carbonate_set) == len(metals) and any(
                name in {v[0] for v in _CARBONATE.values()} for name, _ in carbonate_set):
            families.append(("carbonate", carbonate_set,
                             "Carbonate route; CO2 is released on heating and is part "
                             "of the reaction and of its driving force."))

    element_set = [(e, {e: 1}) for e in metals]
    if "O" in counts:
        element_set = element_set + [("O2", {"O": 2})]
    families.append(("element", [(f, c) for f, c in element_set if f != "O2"],
                     "Direct combination of the elements."))

    for rank, (family, precursors, description) in enumerate(families, start=1):
        if rank > cfg["max_routes_per_proposal"]:
            break
        for formula, species_counts in precursors:
            lookup[formula] = species_counts
        balanced = _balance(counts, precursors, target_formula)
        if balanced is None:
            continue
        reactants, products = balanced
        if any(term["coefficient"] <= 0 for term in reactants + products):
            continue
        is_balanced = _verify_balanced(reactants, products, lookup, counts, target_formula)

        energy = thermo.reaction_energy(reactants, products)

        assumptions = [
            description,
            "Reaction scale is fixed by the stated normalisation convention "
            f"({cfg['reaction_normalisation']}); a balanced equation has an "
            "arbitrary overall scale and two implementations that do not state "
            "the convention will disagree by a constant factor.",
            energy.reason[:500],
            "Precursors are assumed obtainable and anhydrous; hydration water "
            "and carrier gases are not tracked.",
        ]
        if energy.value_ev_per_atom is not None:
            assumptions.append(
                "The driving force is an enthalpy of reaction at 298.15 K, not a "
                "free energy at synthesis temperature. For a route that releases a "
                "gas -- the carbonate and nitrate families -- the entropy term is "
                "what actually drives it, and this number will therefore understate "
                "those routes badly at furnace temperature.")

        routes.append({
            "route_id": f"route-{family}",
            "rank": len(routes) + 1,
            "reaction": {
                "reactants": reactants,
                "products": products,
                "balanced": is_balanced,
                "normalization": cfg["reaction_normalisation"],
            },
            "driving_force_ev_per_atom": (round(energy.value_ev_per_atom, 6)
                                          if energy.value_ev_per_atom is not None else None),
            "conditions": {
                "temperature_k": None,
                "pressure_pa": 101325.0,
                "atmosphere": None,
                "duration_hours": None,
                "notes": "Conditions are not proposed by this build; no kinetic model "
                         "is implemented that would justify a temperature or a dwell.",
            },
            "assumptions": assumptions,
            "competing_products": [],
            "confidence": 0.45 if energy.value_ev_per_atom is not None else 0.3,
        })

    # Rank by driving force where it is known: most exothermic first. Routes
    # with no computable driving force keep their family order and sort after
    # the ranked ones, because an unknown is not evidence of a bad route.
    ranked = sorted(
        routes,
        key=lambda r: (r["driving_force_ev_per_atom"] is None,
                       r["driving_force_ev_per_atom"]
                       if r["driving_force_ev_per_atom"] is not None else 0.0,
                       r["route_id"]))
    for position, route in enumerate(ranked, start=1):
        route["rank"] = position

    return ranked[:cfg["max_routes_per_proposal"]]


def route_findings(routes_list: list[dict], cfg: dict) -> list[RouteFinding]:
    """Findings that depend on the driving forces, once they are known.

    A small driving force is worth flagging on its own: in practice it is
    associated with slow kinetics, and slow kinetics is a leading cause of
    syntheses that are thermodynamically fine and nonetheless do not work.
    """
    findings: list[RouteFinding] = []
    if not routes_list:
        return findings

    computed = [r for r in routes_list if r["driving_force_ev_per_atom"] is not None]
    if not computed:
        return findings

    best = min(r["driving_force_ev_per_atom"] for r in computed)
    threshold = cfg["low_driving_force_threshold_ev_per_atom"]

    if best > 0:
        findings.append(RouteFinding(
            "FEAS.SYNTH.NO_VIABLE_ROUTE", "/composition/formula",
            f"Every route with a computable driving force is endothermic at 298 K; "
            f"the best is {best:+.4f} eV/atom. Note the bound before reading this as "
            f"fatal: these are enthalpies, and a gas-releasing route is driven by "
            f"entropy at furnace temperature, which this build does not compute.",
            observed=best, expected="a negative (exothermic) driving force",
            level="note"))
    elif abs(best) < threshold:
        findings.append(RouteFinding(
            "FEAS.SYNTH.LOW_DRIVING_FORCE", "/composition/formula",
            f"The best available route has a driving force of {best:+.4f} eV/atom, "
            f"inside the {threshold} eV/atom band this build treats as small. Small "
            f"driving force is associated with slow kinetics, which is a leading "
            f"real-world cause of syntheses that are thermodynamically fine and "
            f"nonetheless do not work.",
            observed=best, expected=f"<= -{threshold} eV/atom", level="note"))

    return findings


def _precursor_formula(text: str) -> str:
    """Strip the annotation a generator attaches to a precursor name.

    'TiO2 (anatase)' names a real precursor and a polymorph. The polymorph is
    not part of the formula, and refusing the whole string because of it would
    report a perfectly ordinary starting material as unobtainable.
    """
    cleaned = re.sub(r"\([^)]*\)", " ", text)
    cleaned = re.sub(r"\[[^\]]*\]", " ", cleaned)
    return cleaned.strip().split()[0] if cleaned.strip() else ""


def assess_hint(proposal: dict, counts: dict[str, float] | None) -> RouteResult:
    """Judge the generator's own synthesis_hint. Echoing it back is not assessment."""
    hint = proposal.get("synthesis_hint")
    result = RouteResult()
    if not isinstance(hint, dict) or not hint:
        return result

    issues: list[str] = []
    findings: list[RouteFinding] = []

    # --- are the named precursors real, obtainable compounds? --------------
    precursors = hint.get("precursors")
    if isinstance(precursors, list):
        for index, raw in enumerate(precursors):
            if not isinstance(raw, str) or not raw.strip():
                continue
            candidate = _precursor_formula(raw)
            parsed = normalize_formula(candidate) if candidate else None
            if parsed is None or not parsed.parsed_ok:
                issues.append(f"precursor {raw!r} could not be identified as a compound")
                findings.append(RouteFinding(
                    "FEAS.SYNTH.PRECURSOR_UNAVAILABLE",
                    f"/synthesis_hint/precursors/{index}",
                    f"Named precursor {raw!r} could not be resolved to a real "
                    f"chemical formula, so it cannot be confirmed as an obtainable "
                    f"starting material.",
                    observed=raw, level="note",
                ))

    # --- is the atmosphere compatible with the target? ---------------------
    atmosphere = hint.get("atmosphere")
    if counts and isinstance(atmosphere, str):
        elements = set(counts)
        if "O" in elements and atmosphere in REDUCING_ATMOSPHERES:
            issues.append(
                f"atmosphere {atmosphere!r} is reducing and the target is an oxide"
            )
            findings.append(RouteFinding(
                "FEAS.SYNTH.HINT_INCOHERENT", "/synthesis_hint/atmosphere",
                f"The target contains oxygen and the proposed atmosphere "
                f"{atmosphere!r} is reducing. At the proposed temperature a reducing "
                f"atmosphere removes oxygen from an oxide rather than preserving it, "
                f"so the composition and the stated processing conditions are "
                f"mutually inconsistent. The composition alone looks fine, which is "
                f"why a verifier that checks only the composition misses this.",
                observed=atmosphere,
                expected="an inert or oxidising atmosphere for an oxide target",
                level="warning",
            ))
        if (elements & {"N", "S"}) and "O" not in elements and atmosphere in OXIDISING_ATMOSPHERES:
            issues.append(
                f"atmosphere {atmosphere!r} is oxidising and the target is an "
                f"oxygen-free nitride or sulfide"
            )
            findings.append(RouteFinding(
                "FEAS.SYNTH.HINT_INCOHERENT", "/synthesis_hint/atmosphere",
                f"The target is an oxygen-free nitride or sulfide and the proposed "
                f"atmosphere {atmosphere!r} is oxidising, which favours the oxide "
                f"over the intended phase at synthesis temperature.",
                observed=atmosphere,
                expected="an inert or reducing atmosphere for a nitride or sulfide",
                level="warning",
            ))

    # --- are the conditions internally sensible? ---------------------------
    temperature = hint.get("temperature")
    unit = hint.get("temperature_unit")
    if isinstance(temperature, (int, float)) and not isinstance(temperature, bool):
        kelvin = (float(temperature) + 273.15 if unit == "C"
                  else (float(temperature) - 32.0) * 5.0 / 9.0 + 273.15 if unit == "F"
                  else float(temperature))
        if kelvin <= 0:
            issues.append("proposed temperature is at or below absolute zero")
            findings.append(RouteFinding(
                "FEAS.SYNTH.HINT_INCOHERENT", "/synthesis_hint/temperature",
                f"Proposed temperature resolves to {kelvin:.1f} K, at or below "
                f"absolute zero.", observed=kelvin, expected="> 0 K", level="warning",
            ))
        elif kelvin > 4000:
            issues.append(f"proposed temperature {kelvin:.0f} K exceeds any "
                          f"ordinary solid-state furnace")
            findings.append(RouteFinding(
                "FEAS.SYNTH.HINT_INCOHERENT", "/synthesis_hint/temperature",
                f"Proposed temperature resolves to {kelvin:.0f} K, above the melting "
                f"point of every ordinary crucible and furnace element, which makes "
                f"the stated route unrealisable as written.",
                observed=kelvin, expected="<= 4000 K", level="warning",
            ))
        if unit is None:
            issues.append("temperature supplied without a unit")

    consistent = not issues
    result.hint_assessment = {
        "consistent": consistent,
        "rationale": (
            "The generator's hint is coherent with the target composition on every "
            "check this build applies: named precursors resolve to real compounds, "
            "the atmosphere is compatible with the target chemistry, and the stated "
            "conditions are internally sensible. Note the bound: no thermodynamic "
            "or kinetic model was run, so this is an absence of detected "
            "contradiction, not a prediction that the route works."
            if consistent else
            "The generator's hint contradicts the proposal it accompanies: "
            + "; ".join(issues) + "."
        )[:2000],
        "issues": [i[:500] for i in issues],
    }
    result.findings = findings
    return result
