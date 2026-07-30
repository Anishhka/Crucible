"""
Thermochemistry: formation energies, convex hulls, and open-system stability.

Everything here is driven by the committed table in
`reference_data/thermochemistry.json` -- standard formation enthalpies and
entropies, hand-entered, with the bound on that stated in the dataset's own
coverage note and in `LIMITATIONS.md`.

**The coverage rule is the important part.** A quantity is computed only when
every species it depends on is in the table. Otherwise the function returns
`None` together with a reason, and the caller writes `null` plus that reason
into the record. `PROJECT.md` §5.6 says this explicitly for driving forces --
"a route with no computable driving force is still a route" -- and the same
discipline is applied to hull distances and atmosphere screens. A number that
was estimated to fill a field is worse than an absent one, because nothing
downstream can tell the two apart.

Three things this module can do, and one it cannot:

  * **Reaction energy.** ΔH of any balanced reaction whose species are all
    covered. This is what gives routes a driving force.
  * **Convex hull.** The lowest-energy combination of known phases at a given
    composition, by exact enumeration over subsets. This gives the
    decomposition products and, for a compound that is itself in the table, its
    distance above the hull.
  * **Open-system oxygen stability.** Whether an oxide survives a given
    atmosphere, via the oxygen chemical potential that atmosphere imposes.

  * **What it cannot do: predict the formation energy of a compound that is not
    in the table.** There is no DFT and no learned model here. For a genuinely
    novel proposal -- the interesting case -- `formation_energy_ev_per_atom` is
    `null`. What is still computable, and still useful, is the hull energy *at
    that composition*: the competing phases and what they are worth. So the
    record can say "the known phases at this composition give -3.1 eV/atom, and
    whether your compound is below that is unknown", which is a true and
    informative statement rather than a fabricated one.
"""

from __future__ import annotations

import itertools
import json
import math
import os
from dataclasses import dataclass, field

from .normalize import composition_key, normalize_formula

DATASET_PATH = os.path.join(os.path.dirname(__file__), "reference_data",
                            "thermochemistry.json")

# 1 eV = 96.485 kJ/mol. Defined once so no call site can disagree.
KJ_PER_MOL_PER_EV = 96.485
# Boltzmann constant in eV/K.
K_B_EV = 8.617333262e-5
STANDARD_TEMPERATURE_K = 298.15

# Partial pressures used to fix the oxygen chemical potential of each
# atmosphere. Order-of-magnitude values, and the conclusions are logarithmically
# insensitive to them -- but they are configuration, not constants of nature, so
# they are stated here rather than buried in an expression.
_ATMOSPHERE_PO2_BAR = {
    "O2": 1.0,
    "air": 0.21,
    "CO2": 1e-4,
    "Ar": 1e-6,
    "N2": 1e-6,
    "sealed_ampoule": 1e-8,
    "vacuum": 1e-10,
}
# Reducing atmospheres set μ_O through an equilibrium rather than a partial
# pressure. The ratio is the residual oxidised fraction in a nominally dry gas.
_REDUCING_RATIO = {
    "H2": 1e-4,          # pH2O / pH2 in dry hydrogen
    "forming_gas": 1e-3,  # N2/H2 mixture, wetter in practice
    "NH3": 1e-4,
}

# GAS-PHASE water, carried here rather than in the composition-keyed table.
#
# The table is indexed by composition, which by construction cannot distinguish
# phases or polymorphs: H2O liquid and H2O gas share a key, as do quartz and
# cristobalite. That is a real limitation of the whole dataset and is stated in
# LIMITATIONS.md. It matters acutely here, because the H2/H2O equilibrium that
# fixes μ_O in a reducing furnace involves water as a GAS at 1200 K, and using
# the liquid value understates how reducing hydrogen is by roughly 0.9 eV --
# enough to flip the predicted survival of NiO, CuO and Fe2O3, all of which are
# in fact reduced by hydrogen at those temperatures.
_H2O_GAS_ENTHALPY_KJ = -241.83
_H2O_GAS_ENTROPY_J = 188.8
_H2_GAS_ENTROPY_J = 130.7
_O2_GAS_ENTROPY_J = 205.2


@dataclass
class Phase:
    key: str
    formula: str
    counts: dict[str, float]
    enthalpy_ev: float           # ΔH°f per formula unit, eV
    entropy_ev_per_k: float | None
    n_atoms: float

    @property
    def enthalpy_per_atom(self) -> float:
        return self.enthalpy_ev / self.n_atoms


@dataclass
class HullResult:
    """The lowest-energy combination of known phases at one composition."""
    hull_energy_per_atom: float | None
    decomposition: list[dict] = field(default_factory=list)
    formation_energy_per_atom: float | None = None
    energy_above_hull: float | None = None
    reason: str = ""
    competing_phases: list[str] = field(default_factory=list)


@dataclass
class ReactionEnergy:
    value_ev_per_atom: float | None
    reason: str
    missing: list[str] = field(default_factory=list)


@dataclass
class AtmosphereResult:
    atmosphere: str
    stable: bool | None
    driving_force_ev_per_atom: float | None
    products: list[str]
    mu_oxygen_ev: float | None
    reason: str


_DATASET: dict | None = None
_PHASES: dict[str, Phase] | None = None


def _load() -> tuple[dict, dict[str, Phase]]:
    global _DATASET, _PHASES
    if _PHASES is not None and _DATASET is not None:
        return _DATASET, _PHASES
    phases: dict[str, Phase] = {}
    dataset: dict = {}
    if os.path.exists(DATASET_PATH):
        with open(DATASET_PATH, "r", encoding="utf-8") as fh:
            dataset = json.load(fh)
        for row in dataset.get("entries", []):
            parsed = normalize_formula(row["formula"])
            if not parsed.parsed_ok:
                continue
            entropy = row.get("standard_entropy_j_per_mol_k")
            phases[row["key"]] = Phase(
                key=row["key"],
                formula=row["formula"],
                counts=parsed.counts,
                enthalpy_ev=row["delta_hf_kj_per_mol"] / KJ_PER_MOL_PER_EV,
                entropy_ev_per_k=(entropy / 1000.0 / KJ_PER_MOL_PER_EV
                                  if entropy is not None else None),
                n_atoms=row["n_atoms_per_formula_unit"],
            )
    _DATASET, _PHASES = dataset, phases
    return dataset, phases


def dataset_manifest_entry() -> dict | None:
    """The run_manifest.reference_data row for the thermochemical table.

    Energies are as much a corpus-relative claim as novelty is: a hull distance
    without the dataset it was computed against is not reproducible.
    """
    dataset, phases = _load()
    if not phases:
        return None
    import hashlib
    with open(DATASET_PATH, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    return {
        "id": f"thermochemistry@{dataset['dataset_version']}",
        "provider": "thermochemistry",
        "kind": "snapshot",
        "snapshot": dataset["dataset_version"],
        "sha256": digest,
        "n_entries": dataset.get("n_entries", len(phases)),
        "coverage_note": dataset.get("coverage_note"),
        "license": dataset.get("license"),
        "retrieved_at": None,
    }


def lookup(counts: dict[str, float]) -> Phase | None:
    _, phases = _load()
    return phases.get(composition_key(counts))


def has_data(formula: str) -> bool:
    parsed = normalize_formula(formula)
    return parsed.parsed_ok and lookup(parsed.counts) is not None


# --------------------------------------------------------------------------- #
# reaction energies
# --------------------------------------------------------------------------- #

def reaction_energy(reactants: list[dict], products: list[dict]) -> ReactionEnergy:
    """ΔH of a balanced reaction, in eV per atom of the products.

    Normalised per product atom rather than per formula unit so that the number
    is comparable across reactions with different overall scales -- the same
    reason the reaction itself carries its normalisation convention.
    """
    _, phases = _load()
    missing: list[str] = []
    total = 0.0
    product_atoms = 0.0

    def side(terms: list[dict], sign: float) -> float:
        nonlocal product_atoms
        accumulated = 0.0
        for term in terms:
            parsed = normalize_formula(term["formula"])
            if not parsed.parsed_ok:
                missing.append(term["formula"])
                continue
            phase = lookup(parsed.counts)
            if phase is None:
                missing.append(term["formula"])
                continue
            accumulated += sign * phase.enthalpy_ev * term["coefficient"]
            if sign > 0:
                product_atoms += phase.n_atoms * term["coefficient"]
        return accumulated

    total += side(products, 1.0)
    total += side(reactants, -1.0)

    if missing:
        return ReactionEnergy(
            None,
            "No formation enthalpy is available for "
            + ", ".join(sorted(set(missing)))
            + ". The driving force is reported as null rather than estimated: a "
              "guessed energy is indistinguishable downstream from a computed one.",
            sorted(set(missing)),
        )
    if product_atoms <= 0:
        return ReactionEnergy(None, "Reaction has no products with known composition.")

    return ReactionEnergy(
        total / product_atoms,
        f"ΔH of reaction at {STANDARD_TEMPERATURE_K:.2f} K from standard formation "
        f"enthalpies, normalised per atom of product. Negative is exothermic.",
    )


# --------------------------------------------------------------------------- #
# convex hull
# --------------------------------------------------------------------------- #

def phases_in_system(elements: set[str]) -> list[Phase]:
    """Every known phase whose elements are a subset of this chemical system."""
    _, phases = _load()
    found = [p for p in phases.values() if set(p.counts) <= elements]
    return sorted(found, key=lambda p: (p.formula, p.key))


def _solve(matrix: list[list[float]], rhs: list[float]) -> list[float] | None:
    """Gauss-Jordan with partial pivoting. Returns None for a singular system.

    Small and exact enough: the systems here are at most 5x5, one per candidate
    decomposition.
    """
    n = len(matrix)
    if n == 0 or any(len(row) != n for row in matrix):
        return None
    augmented = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for column in range(n):
        pivot = max(range(column, n), key=lambda r: abs(augmented[r][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            return None
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [v / divisor for v in augmented[column]]
        for row in range(n):
            if row == column:
                continue
            factor = augmented[row][column]
            if factor:
                augmented[row] = [v - factor * w
                                  for v, w in zip(augmented[row], augmented[column])]
    return [augmented[i][n] for i in range(n)]


def convex_hull(counts: dict[str, float]) -> HullResult:
    """Lowest-energy decomposition of one composition into known phases.

    Exact enumeration rather than a hull algorithm: a composition in an
    n-element system decomposes into at most n phases, the number of candidate
    phases per system here is small (typically under 20), and enumerating
    subsets of size <= n and solving each small linear system is both exact and
    obviously correct. A geometric hull would be faster and harder to trust.
    """
    elements = set(counts)
    if not elements:
        return HullResult(None, reason="No composition supplied.")

    candidates = phases_in_system(elements)
    if not candidates:
        return HullResult(
            None,
            reason=f"No phase in the thermochemical table lies inside the "
                   f"{'-'.join(sorted(elements))} system, so no competing phases "
                   f"could be assembled and no hull was constructed.")

    element_order = sorted(elements)
    total_atoms = sum(counts.values())
    target = [counts[e] / total_atoms for e in element_order]

    best_energy: float | None = None
    best_combo: list[tuple[Phase, float]] = []

    for size in range(1, len(element_order) + 1):
        for combo in itertools.combinations(candidates, size):
            # Solve for phase amounts x such that the mixture matches the target
            # composition. Over-determined systems are handled by padding with
            # the normalisation row, which is what makes fractions comparable.
            rows = []
            for element in element_order:
                rows.append([p.counts.get(element, 0.0) / p.n_atoms for p in combo])
            rows.append([1.0] * size)
            targets = target + [1.0]

            square = rows[:size] if size <= len(element_order) else rows
            square_targets = targets[:size] if size <= len(element_order) else targets
            if len(square) != size:
                square = rows[-size:]
                square_targets = targets[-size:]

            solution = _solve([row[:] for row in square], list(square_targets))
            if solution is None or any(x < -1e-9 for x in solution):
                continue

            # Confirm the candidate actually reproduces the target composition;
            # the square subsystem may be satisfied while the full one is not.
            reproduced = []
            for index, element in enumerate(element_order):
                reproduced.append(sum(
                    solution[j] * combo[j].counts.get(element, 0.0) / combo[j].n_atoms
                    for j in range(size)))
            scale = sum(reproduced)
            if scale <= 1e-9:
                continue
            if any(abs(reproduced[i] / scale - target[i]) > 1e-6
                   for i in range(len(element_order))):
                continue

            energy = sum(solution[j] * combo[j].enthalpy_per_atom for j in range(size))
            energy /= scale
            if best_energy is None or energy < best_energy - 1e-12:
                best_energy = energy
                best_combo = [(combo[j], solution[j] / scale) for j in range(size)
                              if solution[j] > 1e-9]

    if best_energy is None:
        return HullResult(
            None,
            reason="No non-negative combination of the known phases reproduces this "
                   "composition, so the hull could not be evaluated here.",
            competing_phases=[p.formula for p in candidates])

    decomposition = sorted(
        ({"formula": phase.formula, "fraction": fraction}
         for phase, fraction in best_combo),
        key=lambda d: (-d["fraction"], d["formula"]))

    own = lookup(counts)
    formation = own.enthalpy_per_atom if own else None
    above = (formation - best_energy) if formation is not None else None
    # A compound that is itself a hull vertex can come out microscopically
    # negative through floating point. Clamp, because a negative distance above
    # the hull is not a physical statement.
    if above is not None and -1e-9 < above < 0:
        above = 0.0

    if own is not None:
        reason = (f"Hull assembled from {len(candidates)} known phase(s) in the "
                  f"{'-'.join(element_order)} system. This composition is itself in "
                  f"the thermochemical table, so its distance above the hull is a "
                  f"computed difference rather than an estimate.")
    else:
        reason = (f"Hull assembled from {len(candidates)} known phase(s) in the "
                  f"{'-'.join(element_order)} system. This composition is NOT in the "
                  f"thermochemical table, so its own formation energy is unknown and "
                  f"no distance above the hull can be computed. What is reported is "
                  f"the energy of the competing phases at this composition: the "
                  f"decomposition a compound here would have to beat.")

    return HullResult(
        hull_energy_per_atom=best_energy,
        decomposition=decomposition,
        formation_energy_per_atom=formation,
        energy_above_hull=above,
        reason=reason,
        competing_phases=[p.formula for p in candidates],
    )


# --------------------------------------------------------------------------- #
# open system: oxygen chemical potential
# --------------------------------------------------------------------------- #

def gibbs_formation(phase: Phase, temperature_k: float) -> float | None:
    """ΔG°f(T) ≈ ΔH°f(298) − T·ΔS°f, with ΔS°f from tabulated standard entropies.

    A first-order extrapolation that ignores the temperature dependence of the
    heat capacities. Good to a few tens of kJ/mol over a few hundred kelvin,
    which is stated wherever the result is used.
    """
    if phase.entropy_ev_per_k is None:
        return None
    _, phases = _load()
    entropy_of_formation = phase.entropy_ev_per_k
    for element, amount in phase.counts.items():
        reference = _reference_phase(element)
        if reference is None or reference.entropy_ev_per_k is None:
            return None
        per_atom = reference.entropy_ev_per_k / reference.n_atoms
        entropy_of_formation -= per_atom * amount
    return phase.enthalpy_ev - temperature_k * entropy_of_formation


def _reference_phase(element: str) -> Phase | None:
    """The reference-state phase of an element: the diatomic gas where that is
    the standard state, the monatomic solid otherwise."""
    _, phases = _load()
    for candidate in (f"{element}2", element):
        parsed = normalize_formula(candidate)
        if not parsed.parsed_ok:
            continue
        phase = phases.get(composition_key(parsed.counts))
        if phase is not None and abs(phase.enthalpy_ev) < 1e-9:
            return phase
    return None


def oxygen_chemical_potential(atmosphere: str, temperature_k: float) -> tuple[float | None, str]:
    """Δμ_O imposed by an atmosphere, in eV relative to ½O₂ at 1 bar.

    Two regimes:

      * An oxidising or inert gas fixes μ_O through its oxygen partial pressure:
        Δμ_O = ½kT ln(pO₂).
      * A reducing gas fixes it through an equilibrium instead. For hydrogen,
        H₂ + ½O₂ ⇌ H₂O gives Δμ_O = ΔG°f(H₂O) + kT ln(pH₂O/pH₂), which lands
        near −2.6 eV at room temperature — the reason hydrogen reduces copper
        oxide and does not reduce titania.
    """
    kt = K_B_EV * temperature_k

    if atmosphere in _REDUCING_RATIO:
        # H2(g) + ½O2(g) ⇌ H2O(g). At equilibrium ½μ_O2 = μ_H2O − μ_H2, so
        #     Δμ_O = ΔG°f(H2O, gas) + kT·ln(pH2O / pH2)
        # Gas-phase water throughout: see the note on _H2O_GAS_ENTHALPY_KJ.
        entropy_of_formation_j = (_H2O_GAS_ENTROPY_J - _H2_GAS_ENTROPY_J
                                  - 0.5 * _O2_GAS_ENTROPY_J)
        enthalpy_ev = _H2O_GAS_ENTHALPY_KJ / KJ_PER_MOL_PER_EV
        entropy_ev_per_k = entropy_of_formation_j / 1000.0 / KJ_PER_MOL_PER_EV
        gibbs = enthalpy_ev - temperature_k * entropy_ev_per_k
        ratio = _REDUCING_RATIO[atmosphere]
        mu = gibbs + kt * math.log(ratio)
        return mu, (
            f"μ_O fixed by the H2/H2O(gas) equilibrium at {temperature_k:.0f} K "
            f"with pH2O/pH2 = {ratio:g}: ΔG°f(H2O,g) = {gibbs:.2f} eV gives "
            f"Δμ_O = {mu:.2f} eV relative to ½O2 at 1 bar. Strongly reducing.")

    partial_pressure = _ATMOSPHERE_PO2_BAR.get(atmosphere)
    if partial_pressure is None:
        return None, (f"No oxygen chemical potential is defined for atmosphere "
                      f"{atmosphere!r} in this build, so no open-system screen was run.")
    mu = 0.5 * kt * math.log(partial_pressure)
    return mu, (
        f"μ_O fixed by pO2 = {partial_pressure:g} bar at {temperature_k:.0f} K, "
        f"giving Δμ_O = {mu:.3f} eV relative to ½O2 at 1 bar.")


def atmosphere_stability(counts: dict[str, float], atmosphere: str,
                         temperature_k: float) -> AtmosphereResult:
    """Does this oxide survive the stated atmosphere?

    The comparison is a grand-potential one. Decomposing MO_x into its elements
    returns x oxygens to the reservoir at Δμ_O each, against the ΔH°f that had
    to be paid to form it:

        ΔE_decomposition  =  n_O · Δμ_O  −  ΔH°f(MO_x)

    Decomposition is favourable when that is **negative**, so the oxide survives
    while it is positive. Both sides are per formula unit; the reported driving
    force is per atom and keeps the usual sign convention -- negative means the
    reaction goes, i.e. the oxide is reduced.

    Sanity anchors, all reproduced by this function at 1223 K under H2: CuO, NiO
    and Fe2O3 are reduced; TiO2, Al2O3, MgO and SiO2 are not. That split is the
    classic Ellingham ordering, and reproducing it is what makes the screen
    worth trusting.
    """
    if "O" not in counts:
        return AtmosphereResult(
            atmosphere, None, None, [], None,
            "Composition contains no oxygen, so the oxygen chemical potential of "
            "the atmosphere does not act on it and no open-system screen applies.")

    phase = lookup(counts)
    if phase is None:
        return AtmosphereResult(
            atmosphere, None, None, [], None,
            "No formation enthalpy is available for this composition, so its "
            "stability against the atmosphere cannot be computed. Reported as "
            "unknown rather than assumed stable.")

    mu, mu_reason = oxygen_chemical_potential(atmosphere, temperature_k)
    if mu is None:
        return AtmosphereResult(atmosphere, None, None, [], None, mu_reason)

    n_oxygen = counts["O"]
    # Negative => decomposition is favourable => the oxide does not survive.
    driving = (n_oxygen * mu - phase.enthalpy_ev) / phase.n_atoms
    stable = driving >= 0.0

    products: list[str] = []
    if not stable:
        metals = sorted(e for e in counts if e != "O")
        products = metals + (["H2O"] if atmosphere in _REDUCING_RATIO else ["O2"])

    return AtmosphereResult(
        atmosphere=atmosphere,
        stable=stable,
        driving_force_ev_per_atom=driving,
        products=products,
        mu_oxygen_ev=mu,
        reason=(
            f"{mu_reason} Reduction of {phase.formula} to its elements is "
            f"{'favourable' if not stable else 'unfavourable'} at "
            f"{driving:+.3f} eV/atom under this reservoir. "
            + ("The oxide is predicted NOT to survive this atmosphere."
               if not stable else
               "The oxide is predicted to survive this atmosphere.")
            + " First-order treatment: ΔH°f is used as the 0 K formation energy and "
              "only the H2/H2O equilibrium sets μ_O in reducing gas; no defect "
              "chemistry, no intermediate suboxides, no kinetics."
        ),
    )
