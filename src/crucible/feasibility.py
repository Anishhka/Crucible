"""
Feasibility, the assumption stack, and open-system screening (PROJECT.md §5.5).

This build computes real thermodynamics **where the committed thermochemical
table covers the composition**, and says so explicitly where it does not. Three
regimes, and which one a record is in is always stated on the record:

  1. **The composition is in the table.** A convex hull is assembled from every
     known phase in its chemical system, and the record carries a real
     `formation_energy_ev_per_atom`, `energy_above_hull_ev_per_atom`, and a
     decomposition. The stability threshold in §7.4 applies. Frame: `closed_0k`.

  2. **The composition is NOT in the table, but its chemical system is.** The
     compound's own energy is unknown, so `energy_above_hull` stays `null` --
     but the hull *at that composition* is computable, so the record still
     names the competing phases and what they are worth. That is the honest and
     genuinely useful statement: "here is what your compound would have to beat".

  3. **Neither is covered.** Everything energetic is `null` with a reason, and
     the verdict falls back to composition-level screening alone.

Where the generator supplies an atmosphere, an open-system screen runs on top of
this: the oxygen chemical potential that atmosphere imposes, against the
formation energy of the oxide. That is what licenses `FEAS.ATMOSPHERE.UNSTABLE`,
which the taxonomy explicitly forbids raising from a closed zero-temperature
analysis. When it runs, the frame becomes `grand_potential`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import chemdata, thermo


@dataclass
class FeasibilityFinding:
    code: str
    json_pointer: str
    message: str
    observed: object | None = None
    expected: object | None = None
    level: str = "warning"


@dataclass
class FeasibilityResult:
    verdict: str  # plausible | marginal | implausible | unknown
    rationale: str
    findings: list[FeasibilityFinding] = field(default_factory=list)
    measured: dict = field(default_factory=dict)
    hull: thermo.HullResult | None = None
    atmospheres: list[thermo.AtmosphereResult] = field(default_factory=list)
    frame: str = "not_evaluated"


def _atmosphere_screen(counts: dict[str, float], proposal: dict,
                       cfg: dict) -> list[thermo.AtmosphereResult]:
    """Screen the proposal against the atmospheres in config, plus whichever one
    the generator actually proposed -- the latter matters most, because it is
    the one the generator is asserting will work."""
    if "O" not in counts:
        return []

    temperature = cfg["screen_temperature_k"]
    hint = proposal.get("synthesis_hint")
    atmospheres = list(cfg["atmospheres_to_screen"])
    if isinstance(hint, dict):
        proposed = hint.get("atmosphere")
        if isinstance(proposed, str) and proposed not in atmospheres and proposed != "unknown":
            atmospheres.append(proposed)
        supplied = hint.get("temperature")
        unit = hint.get("temperature_unit")
        if isinstance(supplied, (int, float)) and not isinstance(supplied, bool):
            if unit == "C":
                temperature = float(supplied) + 273.15
            elif unit == "F":
                temperature = (float(supplied) - 32.0) * 5.0 / 9.0 + 273.15
            elif unit in ("K", None):
                temperature = float(supplied)
            if not (0.0 < temperature < 6000.0):
                temperature = cfg["screen_temperature_k"]

    results = []
    for atmosphere in sorted(set(atmospheres)):
        results.append(thermo.atmosphere_stability(counts, atmosphere, temperature))
    return results


def screen(counts: dict[str, float] | None, charge_result, cfg: dict,
           proposal: dict | None = None) -> FeasibilityResult:
    """Place a composition in its chemical context."""
    if not counts:
        return FeasibilityResult(
            "unknown",
            "No parsed composition, so no feasibility statement is possible. "
            "Reported as unknown rather than defaulting to a verdict.")

    proposal = proposal or {}
    elements = sorted(counts)
    findings: list[FeasibilityFinding] = []
    measured: dict = {"elements": elements}

    hull = thermo.convex_hull(counts)
    atmospheres = _atmosphere_screen(counts, proposal, cfg)
    threshold = cfg["stability_threshold_ev_per_atom"]

    frame = "not_evaluated"
    if any(a.stable is not None for a in atmospheres):
        frame = "grand_potential"
    elif hull.hull_energy_per_atom is not None:
        frame = "closed_0k"

    if hull.hull_energy_per_atom is not None:
        measured["hull_energy_ev_per_atom"] = round(hull.hull_energy_per_atom, 6)
        measured["competing_phases"] = hull.competing_phases[:20]
    if hull.formation_energy_per_atom is not None:
        measured["formation_energy_ev_per_atom"] = round(hull.formation_energy_per_atom, 6)
    if hull.energy_above_hull is not None:
        measured["energy_above_hull_ev_per_atom"] = round(hull.energy_above_hull, 6)

    # --- open-system findings ------------------------------------------------
    for result in atmospheres:
        if result.stable is False:
            findings.append(FeasibilityFinding(
                "FEAS.ATMOSPHERE.UNSTABLE", "/synthesis_hint/atmosphere",
                f"Under {result.atmosphere} the oxygen chemical potential is "
                f"{result.mu_oxygen_ev:.2f} eV and this composition is predicted to "
                f"decompose to {', '.join(result.products)} with a driving force of "
                f"{result.driving_force_ev_per_atom:+.3f} eV/atom. {result.reason}",
                observed=result.atmosphere,
                expected="an atmosphere in which the composition survives",
                level="warning"))

    # --- the ionic-model veto still applies ---------------------------------
    if charge_result is not None and charge_result.applicable and charge_result.balanced is False:
        return FeasibilityResult(
            "implausible",
            "The ionic model applies to this composition and no assignment of "
            "common oxidation states balances it to neutral. That is "
            "composition-level evidence against the stoichiometry, independent of "
            "any thermodynamics. It is not proof the material cannot exist, since "
            "uncommon and mixed valences are excluded from the search by design. "
            + hull.reason,
            findings, measured, hull, atmospheres, frame)

    # --- regime 1: the compound's own energy is known ------------------------
    if hull.energy_above_hull is not None:
        above = hull.energy_above_hull
        measured["stability_threshold_ev_per_atom"] = threshold

        if above > threshold:
            findings.append(FeasibilityFinding(
                "FEAS.THERMO.EHULL_ABOVE_THRESHOLD", "/composition/formula",
                f"Energy above the convex hull is {above:.4f} eV/atom, above the "
                f"applied threshold of {threshold} eV/atom. {cfg['threshold_basis']}",
                observed=above, expected=f"<= {threshold} eV/atom", level="warning"))
        if hull.decomposition and above > threshold:
            products = ", ".join(f"{d['fraction']:.2f} {d['formula']}"
                                 for d in hull.decomposition)
            findings.append(FeasibilityFinding(
                "FEAS.THERMO.DECOMPOSES", "/composition/formula",
                f"At this composition the hull is held by {products}, which lie "
                f"{above:.4f} eV/atom below the compound as tabulated. It is "
                f"predicted to decompose into that assemblage.",
                observed=products, level="warning"))

        if above <= threshold:
            verdict = "plausible"
            summary = (f"Energy above the convex hull is {above:.4f} eV/atom, within "
                       f"the applied threshold of {threshold} eV/atom.")
        elif above <= 2.0 * threshold:
            verdict = "marginal"
            summary = (f"Energy above the convex hull is {above:.4f} eV/atom, above "
                       f"the {threshold} eV/atom threshold but within twice it -- the "
                       f"band where the metastability distribution of observed "
                       f"compounds overlaps heavily with that of never-observed ones.")
        else:
            verdict = "implausible"
            summary = (f"Energy above the convex hull is {above:.4f} eV/atom, more "
                       f"than twice the applied threshold.")

        if any(a.stable is False for a in atmospheres):
            unstable = ", ".join(a.atmosphere for a in atmospheres if a.stable is False)
            if verdict == "plausible":
                verdict = "marginal"
            summary += (f" Downgraded on an open-system basis: the composition does "
                        f"not survive {unstable} at the screened temperature, so a "
                        f"closed-system stability statement does not settle whether "
                        f"it can be made under the proposed conditions.")

        return FeasibilityResult(verdict, summary + " " + hull.reason,
                                 findings, measured, hull, atmospheres, frame)

    # --- regime 2: hull known, compound's own energy unknown -----------------
    if hull.hull_energy_per_atom is not None:
        products = ", ".join(f"{d['fraction']:.2f} {d['formula']}"
                             for d in hull.decomposition) or "no assemblage"
        return FeasibilityResult(
            "unknown",
            f"The competing phases at this composition are {products}, worth "
            f"{hull.hull_energy_per_atom:.4f} eV/atom. This composition is not in "
            f"the thermochemical table, so its own formation energy is unknown and "
            f"no distance above the hull can be computed -- the number above is what "
            f"a compound here would have to beat, not a statement about this one. "
            f"Reported as unknown rather than assumed marginal. " + hull.reason,
            findings, measured, hull, atmospheres, frame)

    # --- regime 3: no thermochemical coverage at all -------------------------
    if chemdata.is_metallic_bonding_class(elements):
        return FeasibilityResult(
            "unknown",
            "Composition is in the metallic / intermetallic or metal-boride, "
            "-silicide, -carbide class, and no phase in the thermochemical table "
            "lies in its chemical system. Neither a hull nor the ionic screens "
            "apply, so no feasibility statement is made. " + hull.reason,
            findings, measured, hull, atmospheres, frame)

    return FeasibilityResult(
        "unknown",
        "No thermochemical data covers this chemical system, so no formation "
        "energy, hull distance or decomposition could be computed. The "
        "composition-level screens found nothing disqualifying, but that is an "
        "absence of evidence rather than evidence of stability. " + hull.reason,
        findings, measured, hull, atmospheres, frame)


def check_frame_applicability(proposal: dict, cfg: dict,
                              result: FeasibilityResult | None = None) -> list[FeasibilityFinding]:
    """Do the generator's proposed conditions fall outside the applied frame?

    Now narrower than it was, and deliberately so. When an open-system screen
    actually ran for the proposed atmosphere, the frame *does* reach the
    question and there is nothing to complain about -- raising
    FEAS.FRAME.INAPPROPRIATE there would be crying wolf. It fires when the
    conditions sit outside what was actually evaluated.
    """
    hint = proposal.get("synthesis_hint")
    if not isinstance(hint, dict):
        return []

    findings: list[FeasibilityFinding] = []
    screened = {a.atmosphere for a in (result.atmospheres if result else [])
                if a.stable is not None}
    frame = result.frame if result else "not_evaluated"

    temperature = hint.get("temperature")
    unit = hint.get("temperature_unit")
    kelvin = None
    if isinstance(temperature, (int, float)) and not isinstance(temperature, bool):
        if unit == "C":
            kelvin = float(temperature) + 273.15
        elif unit == "F":
            kelvin = (float(temperature) - 32.0) * 5.0 / 9.0 + 273.15
        elif unit in ("K", None):
            kelvin = float(temperature)

    max_t = cfg["frame_validity_max_temperature_k"]
    if kelvin is not None and kelvin > max_t and frame == "closed_0k":
        findings.append(FeasibilityFinding(
            "FEAS.FRAME.INAPPROPRIATE", "/synthesis_hint/temperature",
            f"The stability numbers on this record were computed in a closed "
            f"zero-kelvin frame from standard formation enthalpies, and the proposed "
            f"synthesis temperature is {kelvin:.0f} K -- far outside the regime where "
            f"that frame settles anything (this build treats {max_t:.0f} K as its "
            f"outer edge). Configurational and vibrational entropy, which are not "
            f"modelled here, are of the same order as the threshold applied.",
            observed=kelvin, expected=f"<= {max_t} K for the applied frame",
            level="warning"))

    atmosphere = hint.get("atmosphere")
    if (isinstance(atmosphere, str) and atmosphere not in ("unknown", None)
            and atmosphere not in screened
            and atmosphere in cfg["reactive_atmospheres"]):
        findings.append(FeasibilityFinding(
            "FEAS.FRAME.INAPPROPRIATE", "/synthesis_hint/atmosphere",
            f"The proposed atmosphere {atmosphere!r} is chemically active, so the "
            f"system is open to it and its chemical potential participates -- but no "
            f"open-system screen could be run for this composition (either it "
            f"contains no oxygen or it is absent from the thermochemical table). No "
            f"claim is made either way; what is recorded is that the frame does not "
            f"reach the question.",
            observed=atmosphere, expected="an open-system screen to be available",
            level="note"))

    return findings


def assumption_stack(proposal: dict, validity_cfg: dict, feas_cfg: dict,
                     structure_evaluated: bool,
                     result: FeasibilityResult | None = None) -> dict:
    """The frame under which every number on this record was computed.

    A feasibility number without its frame is not a result. Every tolerance that
    could move a conclusion appears here, because a disagreement at one
    tolerance is an agreement at another.
    """
    frame = result.frame if result else "not_evaluated"
    hull = result.hull if result else None
    atmospheres = result.atmospheres if result else []

    if frame == "not_evaluated":
        primary = (
            "Thermodynamic frame is not_evaluated: no phase in the committed "
            "thermochemical table lies in this composition's chemical system, so no "
            "formation energy, hull distance or decomposition was computed and every "
            "energy field on this record is null rather than estimated. The verdict "
            "rests only on composition-level screening -- charge balance under common "
            "oxidation states, and electronegativity ordering -- against the curated "
            "element tables in chemdata.py."
        )
    elif frame == "closed_0k":
        primary = (
            "Thermodynamic frame is closed_0k: a convex hull was assembled from the "
            "known phases of this chemical system using standard formation enthalpies "
            "at 298.15 K, used as a proxy for 0 K formation energies in a closed, "
            "defect-free, stoichiometric bulk system. For the verdict to be wrong it "
            "would be enough for the compound to be stabilised by configurational or "
            "vibrational entropy at synthesis temperature, by a phase this table does "
            "not contain, or by the difference between the 298 K enthalpy used here "
            "and a true 0 K energy -- each of which is comparable to the threshold "
            "applied."
        )
    else:
        primary = (
            "Thermodynamic frame is grand_potential: the composition was screened "
            "against an oxygen reservoir whose chemical potential is fixed by the "
            "stated atmosphere, on top of a closed-system hull built from standard "
            "formation enthalpies. For the verdict to be wrong it would be enough for "
            "an intermediate suboxide this table does not contain to be the real "
            "decomposition product, for the gas equilibrium to sit at a different "
            "water-to-hydrogen ratio than assumed, or for kinetics to prevent an "
            "equilibrium the numbers say is favourable."
        )

    notes = [
        primary,
        "Energies come from a hand-entered table of standard thermochemical values "
        "whose coverage note is in run_manifest.reference_data; it is small, it is "
        "not machine-verified against a primary source, and it cannot distinguish "
        "polymorphs, since it is indexed by composition alone.",
        "Prior-art tiering is relative to the pinned corpus versions listed in "
        "run_manifest.reference_data, whose coverage notes state what each corpus "
        "does and does not contain. A miss against them is absence from those lists, "
        "not absence from the literature.",
    ]

    stack: dict = {
        "thermodynamic_frame": frame,
        "temperature_k": None,
        "pressure_pa": None,
        "atmosphere": None,
        "chemical_potentials": None,
        "energy_model": (
            "standard formation enthalpies (298.15 K, 1 bar) as a 0 K proxy; convex "
            "hull by exact enumeration over known phases in the chemical system"
            if frame != "not_evaluated" else
            "none: composition-level screening only"
        ),
        "defect_free": True,
        "stoichiometric": True,
        "bulk": True,
        "notes": notes,
    }

    if frame != "not_evaluated":
        stack["temperature_k"] = thermo.STANDARD_TEMPERATURE_K

    # The oxygen chemical potential each screened atmosphere imposed. This is
    # the open-system half of the frame and belongs on the stack, not buried in
    # a rationale string.
    potentials = {
        a.atmosphere: round(a.mu_oxygen_ev, 6)
        for a in atmospheres if a.mu_oxygen_ev is not None
    }
    if potentials:
        stack["chemical_potentials"] = {f"mu_O__{k}": v for k, v in sorted(potentials.items())}

    if structure_evaluated:
        stack["symmetry_tolerance"] = validity_cfg["lattice_metric_tolerance_angstrom"]
        stack["structure_matcher_tolerances"] = {
            "min_interatomic_distance_angstrom":
                validity_cfg["min_interatomic_distance_angstrom"],
            "lattice_metric_tolerance_angstrom":
                validity_cfg["lattice_metric_tolerance_angstrom"],
            "lattice_metric_tolerance_degrees":
                validity_cfg["lattice_metric_tolerance_degrees"],
            "occupancy_tolerance": validity_cfg["occupancy_tolerance"],
        }

    hint = proposal.get("synthesis_hint")
    if isinstance(hint, dict):
        if isinstance(hint.get("atmosphere"), str):
            stack["atmosphere"] = hint["atmosphere"][:64]
        temperature = hint.get("temperature")
        unit = hint.get("temperature_unit")
        if isinstance(temperature, (int, float)) and not isinstance(temperature, bool):
            kelvin = (float(temperature) + 273.15 if unit == "C"
                      else (float(temperature) - 32.0) * 5.0 / 9.0 + 273.15 if unit == "F"
                      else float(temperature))
            if kelvin >= 0:
                notes.append(
                    f"The generator proposed {kelvin:.0f} K in "
                    f"{hint.get('atmosphere', 'an unstated atmosphere')}. Where an "
                    f"open-system screen ran, it ran at that temperature; the "
                    f"closed-system hull did not, and is a 298 K enthalpy result.")
        pressure = hint.get("pressure_gpa")
        if isinstance(pressure, (int, float)) and not isinstance(pressure, bool) and pressure >= 0:
            stack["pressure_pa"] = float(pressure) * 1e9
        elif frame != "not_evaluated":
            # The reference pressure the tabulated values are quoted at, not a
            # condition anyone proposed.
            stack["pressure_pa"] = 100000.0

    return stack


def feasibility_block(result: FeasibilityResult, charge_result, feas_cfg: dict) -> dict:
    """The verdict's feasibility block."""
    hull = result.hull
    block: dict = {
        "verdict": result.verdict,
        "rationale": result.rationale[:4000],
        "energy_above_hull_ev_per_atom": (
            round(hull.energy_above_hull, 6)
            if hull and hull.energy_above_hull is not None else None),
        "formation_energy_ev_per_atom": (
            round(hull.formation_energy_per_atom, 6)
            if hull and hull.formation_energy_per_atom is not None else None),
        "decomposition_energy_ev_per_atom": (
            round(hull.energy_above_hull, 6)
            if hull and hull.energy_above_hull is not None else None),
        "threshold_used_ev_per_atom": (
            feas_cfg["stability_threshold_ev_per_atom"]
            if hull and hull.energy_above_hull is not None else None),
        "threshold_basis": feas_cfg["threshold_basis"][:500],
        "charge_balanced": (charge_result.balanced if charge_result else None),
        "oxidation_states": (charge_result.oxidation_states if charge_result else None),
        "atmosphere_stability": None,
    }

    # A compound sitting ON the hull decomposes into itself, which is not a
    # decomposition and would read as one. Products are reported only when the
    # compound is genuinely above the hull, or when its own energy is unknown --
    # in which case the assemblage is the competing phases it would have to beat,
    # which is the useful thing to say.
    if hull and hull.decomposition:
        own_is_on_hull = (hull.energy_above_hull is not None
                          and hull.energy_above_hull <= 1e-9)
        single_self = (len(hull.decomposition) == 1
                       and hull.formation_energy_per_atom is not None)
        if not (own_is_on_hull or single_self):
            block["decomposition_products"] = [
                {"formula": d["formula"], "fraction": round(d["fraction"], 6)}
                for d in hull.decomposition
            ]

    # Likewise: a hull distance of exactly zero carries no information about how
    # comfortably a compound sits there, so it is not dressed up as a
    # decomposition energy.
    if hull and hull.energy_above_hull is not None and hull.energy_above_hull <= 1e-9:
        block["decomposition_energy_ev_per_atom"] = None

    screened = [a for a in result.atmospheres if a.stable is not None]
    if screened:
        block["atmosphere_stability"] = [{
            "atmosphere": a.atmosphere[:32],
            "stable": a.stable,
            "driving_force_ev_per_atom": (round(a.driving_force_ev_per_atom, 6)
                                          if a.driving_force_ev_per_atom is not None else None),
            "products": [p[:100] for p in a.products],
        } for a in sorted(screened, key=lambda x: x.atmosphere)]

    return block
