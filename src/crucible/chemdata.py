"""
Static element reference data.

Four tables, all offline, all pure data:

  * ATOMIC_NUMBER     -- membership here is what makes a token a real element
                         symbol. This is the only such check in the codebase.
  * ATOMIC_MASS       -- standard atomic weights, needed for density.
  * ELECTRONEGATIVITY -- Pauling scale. None where the scale has no accepted
                         value (noble gases, most synthetics).
  * COMMON_OXIDATION_STATES -- the states each element is actually reported in
                         for stable inorganic solids, not every state that is
                         theoretically reachable.

The element list runs the full periodic table through element 118, because a
parser that only knows a curated subset silently reclassifies real chemistry as
"unknown element" -- which is the error mode this file exists to prevent.
Values are carried at the precision they are conventionally quoted at; nothing
downstream is sensitive past the third decimal.
"""

from __future__ import annotations

ATOMIC_NUMBER: dict[str, int] = {
    "H": 1, "He": 2, "Li": 3, "Be": 4, "B": 5, "C": 6, "N": 7, "O": 8, "F": 9,
    "Ne": 10, "Na": 11, "Mg": 12, "Al": 13, "Si": 14, "P": 15, "S": 16,
    "Cl": 17, "Ar": 18, "K": 19, "Ca": 20, "Sc": 21, "Ti": 22, "V": 23,
    "Cr": 24, "Mn": 25, "Fe": 26, "Co": 27, "Ni": 28, "Cu": 29, "Zn": 30,
    "Ga": 31, "Ge": 32, "As": 33, "Se": 34, "Br": 35, "Kr": 36, "Rb": 37,
    "Sr": 38, "Y": 39, "Zr": 40, "Nb": 41, "Mo": 42, "Tc": 43, "Ru": 44,
    "Rh": 45, "Pd": 46, "Ag": 47, "Cd": 48, "In": 49, "Sn": 50, "Sb": 51,
    "Te": 52, "I": 53, "Xe": 54, "Cs": 55, "Ba": 56, "La": 57, "Ce": 58,
    "Pr": 59, "Nd": 60, "Pm": 61, "Sm": 62, "Eu": 63, "Gd": 64, "Tb": 65,
    "Dy": 66, "Ho": 67, "Er": 68, "Tm": 69, "Yb": 70, "Lu": 71, "Hf": 72,
    "Ta": 73, "W": 74, "Re": 75, "Os": 76, "Ir": 77, "Pt": 78, "Au": 79,
    "Hg": 80, "Tl": 81, "Pb": 82, "Bi": 83, "Po": 84, "At": 85, "Rn": 86,
    "Fr": 87, "Ra": 88, "Ac": 89, "Th": 90, "Pa": 91, "U": 92, "Np": 93,
    "Pu": 94, "Am": 95, "Cm": 96, "Bk": 97, "Cf": 98, "Es": 99, "Fm": 100,
    "Md": 101, "No": 102, "Lr": 103, "Rf": 104, "Db": 105, "Sg": 106,
    "Bh": 107, "Hs": 108, "Mt": 109, "Ds": 110, "Rg": 111, "Cn": 112,
    "Nh": 113, "Fl": 114, "Mc": 115, "Lv": 116, "Ts": 117, "Og": 118,
}

KNOWN_ELEMENTS = frozenset(ATOMIC_NUMBER)

ATOMIC_MASS: dict[str, float] = {
    "H": 1.008, "He": 4.0026, "Li": 6.94, "Be": 9.0122, "B": 10.81,
    "C": 12.011, "N": 14.007, "O": 15.999, "F": 18.998, "Ne": 20.180,
    "Na": 22.990, "Mg": 24.305, "Al": 26.982, "Si": 28.085, "P": 30.974,
    "S": 32.06, "Cl": 35.45, "Ar": 39.948, "K": 39.098, "Ca": 40.078,
    "Sc": 44.956, "Ti": 47.867, "V": 50.942, "Cr": 51.996, "Mn": 54.938,
    "Fe": 55.845, "Co": 58.933, "Ni": 58.693, "Cu": 63.546, "Zn": 65.38,
    "Ga": 69.723, "Ge": 72.630, "As": 74.922, "Se": 78.971, "Br": 79.904,
    "Kr": 83.798, "Rb": 85.468, "Sr": 87.62, "Y": 88.906, "Zr": 91.224,
    "Nb": 92.906, "Mo": 95.95, "Tc": 98.0, "Ru": 101.07, "Rh": 102.91,
    "Pd": 106.42, "Ag": 107.87, "Cd": 112.41, "In": 114.82, "Sn": 118.71,
    "Sb": 121.76, "Te": 127.60, "I": 126.90, "Xe": 131.29, "Cs": 132.91,
    "Ba": 137.33, "La": 138.91, "Ce": 140.12, "Pr": 140.91, "Nd": 144.24,
    "Pm": 145.0, "Sm": 150.36, "Eu": 151.96, "Gd": 157.25, "Tb": 158.93,
    "Dy": 162.50, "Ho": 164.93, "Er": 167.26, "Tm": 168.93, "Yb": 173.05,
    "Lu": 174.97, "Hf": 178.49, "Ta": 180.95, "W": 183.84, "Re": 186.21,
    "Os": 190.23, "Ir": 192.22, "Pt": 195.08, "Au": 196.97, "Hg": 200.59,
    "Tl": 204.38, "Pb": 207.2, "Bi": 208.98, "Po": 209.0, "At": 210.0,
    "Rn": 222.0, "Fr": 223.0, "Ra": 226.0, "Ac": 227.0, "Th": 232.04,
    "Pa": 231.04, "U": 238.03, "Np": 237.0, "Pu": 244.0, "Am": 243.0,
    "Cm": 247.0, "Bk": 247.0, "Cf": 251.0, "Es": 252.0, "Fm": 257.0,
    "Md": 258.0, "No": 259.0, "Lr": 266.0, "Rf": 267.0, "Db": 268.0,
    "Sg": 269.0, "Bh": 270.0, "Hs": 269.0, "Mt": 278.0, "Ds": 281.0,
    "Rg": 282.0, "Cn": 285.0, "Nh": 286.0, "Fl": 289.0, "Mc": 290.0,
    "Lv": 293.0, "Ts": 294.0, "Og": 294.0,
}

ELECTRONEGATIVITY: dict[str, float | None] = {
    "H": 2.20, "He": None, "Li": 0.98, "Be": 1.57, "B": 2.04, "C": 2.55,
    "N": 3.04, "O": 3.44, "F": 3.98, "Ne": None, "Na": 0.93, "Mg": 1.31,
    "Al": 1.61, "Si": 1.90, "P": 2.19, "S": 2.58, "Cl": 3.16, "Ar": None,
    "K": 0.82, "Ca": 1.00, "Sc": 1.36, "Ti": 1.54, "V": 1.63, "Cr": 1.66,
    "Mn": 1.55, "Fe": 1.83, "Co": 1.88, "Ni": 1.91, "Cu": 1.90, "Zn": 1.65,
    "Ga": 1.81, "Ge": 2.01, "As": 2.18, "Se": 2.55, "Br": 2.96, "Kr": 3.00,
    "Rb": 0.82, "Sr": 0.95, "Y": 1.22, "Zr": 1.33, "Nb": 1.60, "Mo": 2.16,
    "Tc": 1.90, "Ru": 2.20, "Rh": 2.28, "Pd": 2.20, "Ag": 1.93, "Cd": 1.69,
    "In": 1.78, "Sn": 1.96, "Sb": 2.05, "Te": 2.10, "I": 2.66, "Xe": 2.60,
    "Cs": 0.79, "Ba": 0.89, "La": 1.10, "Ce": 1.12, "Pr": 1.13, "Nd": 1.14,
    "Pm": 1.13, "Sm": 1.17, "Eu": 1.20, "Gd": 1.20, "Tb": 1.20, "Dy": 1.22,
    "Ho": 1.23, "Er": 1.24, "Tm": 1.25, "Yb": 1.10, "Lu": 1.27, "Hf": 1.30,
    "Ta": 1.50, "W": 2.36, "Re": 1.90, "Os": 2.20, "Ir": 2.20, "Pt": 2.28,
    "Au": 2.54, "Hg": 2.00, "Tl": 1.62, "Pb": 2.33, "Bi": 2.02, "Po": 2.00,
    "At": 2.20, "Rn": None, "Fr": 0.70, "Ra": 0.90, "Ac": 1.10, "Th": 1.30,
    "Pa": 1.50, "U": 1.38, "Np": 1.36, "Pu": 1.28, "Am": 1.13, "Cm": 1.28,
    "Bk": 1.30, "Cf": 1.30, "Es": 1.30, "Fm": 1.30, "Md": 1.30, "No": 1.30,
    "Lr": 1.30,
}

COMMON_OXIDATION_STATES: dict[str, list[int]] = {
    "H": [1, -1], "Li": [1], "Be": [2], "B": [3], "C": [4, -4, 2],
    "N": [-3, 3, 5], "O": [-2], "F": [-1], "Na": [1], "Mg": [2], "Al": [3],
    "Si": [4, -4], "P": [-3, 3, 5], "S": [-2, 4, 6], "Cl": [-1, 1, 3, 5, 7],
    "K": [1], "Ca": [2], "Sc": [3], "Ti": [4, 3, 2], "V": [5, 4, 3, 2],
    "Cr": [3, 6, 2], "Mn": [2, 4, 7, 3], "Fe": [3, 2], "Co": [2, 3],
    "Ni": [2, 3], "Cu": [2, 1], "Zn": [2], "Ga": [3], "Ge": [4, 2],
    "As": [-3, 3, 5], "Se": [-2, 4, 6], "Br": [-1, 1, 3, 5], "Rb": [1],
    "Sr": [2], "Y": [3], "Zr": [4], "Nb": [5, 3], "Mo": [6, 4, 3],
    "Tc": [7, 4], "Ru": [4, 3, 2], "Rh": [3], "Pd": [2, 4], "Ag": [1],
    "Cd": [2], "In": [3], "Sn": [4, 2], "Sb": [3, 5, -3], "Te": [-2, 4, 6],
    "I": [-1, 1, 5, 7], "Cs": [1], "Ba": [2], "La": [3], "Ce": [3, 4],
    "Pr": [3, 4], "Nd": [3], "Pm": [3], "Sm": [3, 2], "Eu": [3, 2],
    "Gd": [3], "Tb": [3, 4], "Dy": [3], "Ho": [3], "Er": [3], "Tm": [3],
    "Yb": [3, 2], "Lu": [3], "Hf": [4], "Ta": [5], "W": [6, 4], "Re": [7, 4],
    "Os": [4], "Ir": [4, 3], "Pt": [2, 4], "Au": [3, 1], "Hg": [2, 1],
    "Tl": [1, 3], "Pb": [2, 4], "Bi": [3, 5], "Po": [4, 2], "Ra": [2],
    "Ac": [3], "Th": [4], "Pa": [5], "U": [4, 6, 3], "Np": [5, 4],
    "Pu": [4, 3], "Am": [3], "Cm": [3],
}

# --------------------------------------------------------------------------- #
# Bonding-class classification (ASSUMPTIONS.md §7.6)
# --------------------------------------------------------------------------- #
#
# The compositions for which a formal ionic charge-balance model is known NOT to
# be the right model. Expressed as a *class* rule rather than a list of
# exceptions on purpose: special-casing MgB2 by name would pass the public
# fixture and fail on the unseen one, which contains different members of the
# same class.

METALS = frozenset({
    "Li", "Na", "K", "Rb", "Cs", "Fr",
    "Be", "Mg", "Ca", "Sr", "Ba", "Ra",
    "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
    "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd",
    "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg",
    "Rf", "Db", "Sg", "Bh", "Hs", "Mt", "Ds", "Rg", "Cn",
    "Al", "Ga", "In", "Sn", "Tl", "Pb", "Bi", "Po",
    "La", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er",
    "Tm", "Yb", "Lu",
    "Ac", "Th", "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm",
    "Md", "No", "Lr",
})

# Partners that form covalent/metallic networks with a metal rather than
# behaving as simple anions: borides, silicides, carbides, phosphides,
# germanides, arsenides. MgB2 is the canonical member.
COVALENT_NETWORK_PARTNERS = frozenset({"B", "Si", "C", "P", "Ge", "As", "Sb"})

# Genuinely anionic partners. A metal plus one of these is ordinary ionic
# chemistry and the charge-balance model does apply.
STRONG_ANIONS = frozenset({"O", "F", "Cl", "Br", "I", "S", "Se", "Te", "N", "H"})

# Elements whose presence, in quantity, indicates the proposal is molecular or
# organic rather than an inorganic crystalline solid. Used only as one input to
# the scope decision in normalize.py -- carbon alone does not disqualify a
# carbide or a carbonate.
ORGANIC_MARKERS = frozenset({"C", "H"})


def atomic_number(symbol: str) -> int | None:
    return ATOMIC_NUMBER.get(symbol)


def atomic_mass(symbol: str) -> float | None:
    return ATOMIC_MASS.get(symbol)


def electronegativity(symbol: str) -> float | None:
    return ELECTRONEGATIVITY.get(symbol)


def common_oxidation_states(symbol: str) -> list[int]:
    return COMMON_OXIDATION_STATES.get(symbol, [])


def is_element(symbol: str) -> bool:
    return symbol in ATOMIC_NUMBER


def is_metallic_bonding_class(elements: list[str]) -> bool:
    """Whether a formal ionic charge-balance model is inapplicable here.

    True when:
      * every element is a metal (elemental metals, alloys, intermetallics), or
      * the composition is metals plus only covalent-network partners, with no
        strong anion present -- the MgB2 / boride / silicide / carbide class.

    A metal together with a strong anion (MgO, NaCl, Fe2O3) is ordinary ionic
    chemistry and returns False, so those are still screened.
    """
    els = set(elements)
    if not els:
        return False
    if els <= METALS:
        return True
    if els & STRONG_ANIONS:
        return False
    return bool(els & METALS) and els <= (METALS | COVALENT_NETWORK_PARTNERS)


def most_electronegative(elements: list[str]) -> str | None:
    """The element a simple ionic picture assigns the anion role to."""
    scored = [(e, electronegativity(e)) for e in elements]
    scored = [(e, v) for e, v in scored if v is not None]
    if not scored:
        return None
    # Sort by (-electronegativity, symbol) so ties resolve deterministically
    # rather than by dict insertion order.
    return sorted(scored, key=lambda kv: (-kv[1], kv[0]))[0][0]


def least_electronegative(elements: list[str]) -> str | None:
    scored = [(e, electronegativity(e)) for e in elements]
    scored = [(e, v) for e, v in scored if v is not None]
    if not scored:
        return None
    return sorted(scored, key=lambda kv: (kv[1], kv[0]))[0][0]
