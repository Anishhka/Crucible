"""
Structure-level coherence (PROJECT.md §5.4).

Does the geometry describe a physical arrangement? Checks implemented here:

  * unit conversion of lattice parameters before anything is measured;
  * minimum interatomic distance, accounting for periodicity;
  * lattice parameters within a plausible range;
  * implied mass density within a plausible range;
  * site occupancies reproducing the declared composition;
  * consistency between the asserted space group and the lattice metric.

Every tolerance used is a parameter, appears in the run configuration, and is
echoed into the record's assumption stack. A disagreement at one tolerance is an
agreement at another, which is why none of these numbers is allowed to be
implicit.

The crystallography is done here in plain Python rather than through a library.
That is a deliberate trade recorded in DECISIONS.md: the arithmetic below is a
few dozen lines and is exactly reproducible, where the alternative is a
multi-hundred-megabyte dependency whose symmetry defaults differ from the ones
the public corpora use anyway.

What this module does NOT do is determine a space group from coordinates. That
needs a real symmetry finder. Instead it compares the *crystal family implied by
the lattice metric* against the family the asserted space-group number belongs
to, which catches the common and consequential errors -- asserting cubic
symmetry on an unequal-axis cell, or asserting P1 for a cell that is plainly
cubic -- without pretending to a determination it did not make. The limitation
is stated in LIMITATIONS.md rather than papered over.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import chemdata

# Angstroms per unit of each length the input schema permits.
_LENGTH_TO_ANGSTROM = {
    "angstrom": 1.0,
    "nm": 10.0,
    "pm": 0.01,
    "bohr": 0.529177210903,
}

# Amu/A^3 -> g/cm^3.
_AMU_PER_A3_TO_G_PER_CM3 = 1.66053906660

# International Tables space-group number -> crystal family.
_SG_FAMILY = [
    (2, "triclinic"), (15, "monoclinic"), (74, "orthorhombic"),
    (142, "tetragonal"), (167, "trigonal"), (194, "hexagonal"), (230, "cubic"),
]

# A trigonal group is routinely given in hexagonal axes, so a hexagonal metric
# is not evidence against it. Nothing else is treated as interchangeable.
_METRIC_COMPATIBLE = {
    "trigonal": {"trigonal", "hexagonal", "rhombohedral"},
    "hexagonal": {"hexagonal", "trigonal"},
    "rhombohedral": {"rhombohedral", "trigonal", "hexagonal"},
}

# Ranked most symmetric first, for "the metric is more symmetric than you said".
_FAMILY_RANK = {
    "cubic": 7, "hexagonal": 6, "trigonal": 5, "rhombohedral": 5,
    "tetragonal": 4, "orthorhombic": 3, "monoclinic": 2, "triclinic": 1,
}


@dataclass
class StructureFinding:
    code: str
    json_pointer: str
    message: str
    observed: object | None = None
    expected: object | None = None
    level: str = "warning"


@dataclass
class StructureResult:
    evaluated: bool
    findings: list[StructureFinding] = field(default_factory=list)
    measured: dict = field(default_factory=dict)
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.evaluated and not self.findings


def space_group_family(number: int | None) -> str | None:
    if not isinstance(number, int) or not 1 <= number <= 230:
        return None
    for upper, family in _SG_FAMILY:
        if number <= upper:
            return family
    return None


def lattice_family(a: float, b: float, c: float, alpha: float, beta: float,
                   gamma: float, len_tol: float, ang_tol: float) -> str:
    """Crystal family implied by the cell metric alone, at a stated tolerance."""
    def same(x: float, y: float) -> bool:
        return abs(x - y) <= len_tol

    def ang_is(x: float, target: float) -> bool:
        return abs(x - target) <= ang_tol

    ab, bc, ac = same(a, b), same(b, c), same(a, c)
    all_90 = all(ang_is(x, 90.0) for x in (alpha, beta, gamma))

    if all_90:
        if ab and bc:
            return "cubic"
        if ab or bc or ac:
            return "tetragonal"
        return "orthorhombic"
    if ab and ang_is(alpha, 90.0) and ang_is(beta, 90.0) and ang_is(gamma, 120.0):
        return "hexagonal"
    if ab and bc and same(alpha, beta) and same(beta, gamma) and not ang_is(alpha, 90.0):
        return "rhombohedral"
    if ang_is(alpha, 90.0) and ang_is(gamma, 90.0) and not ang_is(beta, 90.0):
        return "monoclinic"
    if ang_is(alpha, 90.0) and ang_is(beta, 90.0) and not ang_is(gamma, 90.0):
        return "monoclinic"
    return "triclinic"


def cell_volume(a: float, b: float, c: float, alpha: float, beta: float,
                gamma: float) -> float | None:
    ca, cb, cg = (math.cos(math.radians(x)) for x in (alpha, beta, gamma))
    factor = 1.0 - ca * ca - cb * cb - cg * cg + 2.0 * ca * cb * cg
    if factor <= 0.0:
        return None
    return a * b * c * math.sqrt(factor)


def lattice_vectors(a: float, b: float, c: float, alpha: float, beta: float,
                    gamma: float) -> list[list[float]] | None:
    """Standard crystallographic-to-Cartesian construction: a along x, b in the
    xy plane, c fixed by the remaining angles."""
    ca, cb, cg = (math.cos(math.radians(x)) for x in (alpha, beta, gamma))
    sg = math.sin(math.radians(gamma))
    if abs(sg) < 1e-12:
        return None
    cx = c * cb
    cy = c * (ca - cb * cg) / sg
    under = c * c - cx * cx - cy * cy
    if under <= 0.0:
        return None
    return [[a, 0.0, 0.0], [b * cg, b * sg, 0.0], [cx, cy, math.sqrt(under)]]


def min_periodic_distance(frac_coords: list[list[float]],
                          vectors: list[list[float]]) -> tuple[float, int, int]:
    """Smallest distance between any two sites, including periodic images.

    The search covers the 3x3x3 shell of neighbouring cells, which is what
    bounds the cost: for the 500-site maximum the input schema allows this is
    ~3.4M distance evaluations worst case, and for real cells it is far less.
    A full minimum-image convention would need the reduced cell; the shell is
    correct for any cell whose angles are not pathologically oblique, and the
    lattice-angle bound check refuses those separately.
    """
    n = len(frac_coords)
    best = math.inf
    best_pair = (0, 0)
    shifts = [(i, j, k) for i in (-1, 0, 1) for j in (-1, 0, 1) for k in (-1, 0, 1)]

    for i in range(n):
        fi = frac_coords[i]
        for j in range(i, n):
            fj = frac_coords[j]
            for si, sj, sk in shifts:
                if i == j and (si, sj, sk) == (0, 0, 0):
                    continue
                df = (fj[0] + si - fi[0], fj[1] + sj - fi[1], fj[2] + sk - fi[2])
                x = df[0] * vectors[0][0] + df[1] * vectors[1][0] + df[2] * vectors[2][0]
                y = df[0] * vectors[0][1] + df[1] * vectors[1][1] + df[2] * vectors[2][1]
                z = df[0] * vectors[0][2] + df[1] * vectors[1][2] + df[2] * vectors[2][2]
                d2 = x * x + y * y + z * z
                if d2 < best:
                    best = d2
                    best_pair = (i, j)
    return (math.sqrt(best) if best < math.inf else math.inf), best_pair[0], best_pair[1]


def check_structure(proposal: dict, counts: dict[str, float] | None,
                    validity_cfg: dict) -> StructureResult:
    """Run every structure-level check. Absent structure is not applicable, not
    a failure -- the input schema makes it optional and a proposal without
    coordinates is a legitimate proposal."""
    struct = proposal.get("structure")
    if not isinstance(struct, dict):
        return StructureResult(evaluated=False, detail="No structure block supplied.")

    findings: list[StructureFinding] = []
    measured: dict = {}

    lat = struct.get("lattice") or {}
    length_unit = lat.get("length_unit", "angstrom")
    angle_unit = lat.get("angle_unit", "degree")
    scale = _LENGTH_TO_ANGSTROM.get(length_unit)
    if scale is None:
        return StructureResult(evaluated=False,
                               detail=f"Unrecognised lattice length unit {length_unit!r}.")

    try:
        a, b, c = (float(lat[k]) * scale for k in ("a", "b", "c"))
        angles = [float(lat[k]) for k in ("alpha", "beta", "gamma")]
    except (KeyError, TypeError, ValueError):
        return StructureResult(evaluated=False, detail="Lattice parameters are unreadable.")

    if angle_unit == "radian":
        angles = [math.degrees(x) for x in angles]
    alpha, beta, gamma = angles

    measured.update({
        "a_angstrom": a, "b_angstrom": b, "c_angstrom": c,
        "alpha_deg": alpha, "beta_deg": beta, "gamma_deg": gamma,
        "length_unit_supplied": length_unit,
    })
    if scale != 1.0:
        measured["length_unit_conversion_factor"] = scale

    lo, hi = validity_cfg["lattice_bounds_angstrom"]
    ang_lo, ang_hi = validity_cfg["lattice_angle_bounds_degrees"]

    for name, value in (("a", a), ("b", b), ("c", c)):
        if not (lo <= value <= hi):
            findings.append(StructureFinding(
                "STRUCT.GEOM.IMPLAUSIBLE_LATTICE", f"/structure/lattice/{name}",
                f"Lattice parameter {name} is {value:.4g} A after unit conversion "
                f"from {length_unit!r}, outside the plausible range "
                f"[{lo}, {hi}] A for a crystalline solid.",
                observed=value, expected=f"[{lo}, {hi}] angstrom", level="error",
            ))
    for name, value in (("alpha", alpha), ("beta", beta), ("gamma", gamma)):
        if not (ang_lo <= value <= ang_hi):
            findings.append(StructureFinding(
                "STRUCT.GEOM.IMPLAUSIBLE_LATTICE", f"/structure/lattice/{name}",
                f"Lattice angle {name} is {value:.4g} degrees, outside the "
                f"plausible range [{ang_lo}, {ang_hi}].",
                observed=value, expected=f"[{ang_lo}, {ang_hi}] degree", level="error",
            ))

    volume = cell_volume(a, b, c, alpha, beta, gamma)
    if volume is None or volume <= 0:
        findings.append(StructureFinding(
            "STRUCT.GEOM.IMPLAUSIBLE_LATTICE", "/structure/lattice",
            "Lattice parameters do not describe a cell with positive volume; the "
            "angle combination is geometrically impossible.",
            level="error",
        ))
        return StructureResult(True, findings, measured,
                               "Lattice is geometrically impossible; downstream "
                               "geometry checks were not attempted.")
    measured["cell_volume_a3"] = volume

    sites = struct.get("sites") or []
    frac = []
    site_elements = []
    occupancies = []
    for idx, site in enumerate(sites):
        coords = site.get("frac_coords")
        if not isinstance(coords, list) or len(coords) != 3:
            continue
        try:
            frac.append([float(x) for x in coords])
        except (TypeError, ValueError):
            continue
        site_elements.append(site.get("element"))
        occupancies.append(float(site.get("occupancy", 1) or 0))

    # --- minimum interatomic distance -------------------------------------
    min_d = validity_cfg["min_interatomic_distance_angstrom"]
    if len(frac) >= 1:
        vectors = lattice_vectors(a, b, c, alpha, beta, gamma)
        if vectors is not None:
            d, i, j = min_periodic_distance(frac, vectors)
            measured["min_interatomic_distance_angstrom"] = d
            measured["min_distance_site_indices"] = [i, j]
            if d < min_d:
                findings.append(StructureFinding(
                    "STRUCT.GEOM.ATOM_OVERLAP", f"/structure/sites/{j}/frac_coords",
                    f"Sites {i} ({site_elements[i]}) and {j} ({site_elements[j]}) are "
                    f"{d:.4g} A apart including periodic images, below the configured "
                    f"minimum of {min_d} A. That separation is not a bond length.",
                    observed=d, expected=f">= {min_d} angstrom", level="error",
                ))

    # --- density -----------------------------------------------------------
    if site_elements and all(e in chemdata.ATOMIC_MASS for e in site_elements if e):
        mass = sum(chemdata.ATOMIC_MASS[e] * occ
                   for e, occ in zip(site_elements, occupancies) if e)
        density = mass * _AMU_PER_A3_TO_G_PER_CM3 / volume
        measured["density_g_per_cm3"] = density
        d_lo, d_hi = validity_cfg["density_bounds_g_per_cm3"]
        if not (d_lo <= density <= d_hi):
            findings.append(StructureFinding(
                "STRUCT.GEOM.IMPLAUSIBLE_DENSITY", "/structure/lattice",
                f"Implied mass density is {density:.4g} g/cm3, outside the plausible "
                f"range [{d_lo}, {d_hi}] for a crystalline solid. This is usually a "
                f"symptom of a lattice quoted in the wrong unit or a site list that "
                f"does not fill the cell.",
                observed=density, expected=f"[{d_lo}, {d_hi}] g/cm3", level="warning",
            ))

    # --- occupancy vs declared composition ---------------------------------
    if counts and site_elements:
        site_totals: dict[str, float] = {}
        for e, occ in zip(site_elements, occupancies):
            if e:
                site_totals[e] = site_totals.get(e, 0.0) + occ
        if site_totals:
            mismatch = _composition_ratio_mismatch(site_totals, counts,
                                                   validity_cfg["occupancy_tolerance"])
            measured["site_element_totals"] = {k: round(v, 6) for k, v in sorted(site_totals.items())}
            if mismatch:
                findings.append(StructureFinding(
                    "STRUCT.SITES.OCCUPANCY_INCONSISTENT", "/structure/sites",
                    f"Site occupancies sum to a composition that is not proportional "
                    f"to the declared formula: {mismatch}.",
                    observed=_fmt_counts(site_totals), expected=_fmt_counts(counts),
                    level="error",
                ))

    # --- asserted symmetry vs lattice metric -------------------------------
    sg = struct.get("space_group") or {}
    asserted_number = sg.get("number")
    asserted_family = space_group_family(asserted_number)
    metric = lattice_family(a, b, c, alpha, beta, gamma,
                            validity_cfg["lattice_metric_tolerance_angstrom"],
                            validity_cfg["lattice_metric_tolerance_degrees"])
    measured["lattice_metric_family"] = metric
    if asserted_family:
        measured["asserted_space_group_family"] = asserted_family
        compatible = _METRIC_COMPATIBLE.get(asserted_family, {asserted_family})
        if metric not in compatible and asserted_family not in _METRIC_COMPATIBLE.get(metric, {metric}):
            higher = _FAMILY_RANK.get(metric, 0) > _FAMILY_RANK.get(asserted_family, 0)
            findings.append(StructureFinding(
                "STRUCT.SYMMETRY.SPACEGROUP_INCONSISTENT", "/structure/space_group/number",
                (f"Asserted space group {asserted_number} "
                 f"({sg.get('symbol') or 'no symbol'}) belongs to the {asserted_family} "
                 f"family, but the cell metric is {metric} at the configured "
                 f"tolerances ({validity_cfg['lattice_metric_tolerance_angstrom']} A, "
                 f"{validity_cfg['lattice_metric_tolerance_degrees']} deg). ")
                + ("The metric is more symmetric than the asserted group requires, "
                   "which is legal but usually means the symmetry was not determined."
                   if higher else
                   "The asserted group requires a cell metric this lattice does not have."),
                observed=metric, expected=asserted_family, level="warning",
            ))

    detail = (f"Structure evaluated: {len(frac)} sites, V={volume:.4g} A^3, "
              f"metric family {metric}.")
    return StructureResult(True, findings, measured, detail)


def _fmt_counts(counts: dict[str, float]) -> str:
    return " ".join(f"{k}{counts[k]:g}" for k in sorted(counts))


def _composition_ratio_mismatch(site_totals: dict[str, float],
                                declared: dict[str, float],
                                tolerance: float) -> str | None:
    """Whether site occupancies are proportional to the declared composition.

    Proportional, not equal: a cell holding Z formula units is correct and
    extremely common, so comparing raw totals would flag every well-formed
    structure. Only the ratios have to agree.
    """
    if set(site_totals) != set(declared):
        only_sites = sorted(set(site_totals) - set(declared))
        only_formula = sorted(set(declared) - set(site_totals))
        parts = []
        if only_sites:
            parts.append(f"sites contain {', '.join(only_sites)} absent from the formula")
        if only_formula:
            parts.append(f"formula contains {', '.join(only_formula)} absent from the sites")
        return "; ".join(parts)

    reference = sorted(declared)[0]
    if declared[reference] == 0 or site_totals[reference] == 0:
        return None
    z = site_totals[reference] / declared[reference]
    for element in sorted(declared):
        expected = declared[element] * z
        if abs(site_totals[element] - expected) > max(tolerance, tolerance * expected):
            return (f"{element} occupancy totals {site_totals[element]:g} where the "
                    f"formula implies {expected:g} at Z={z:g}")
    return None
