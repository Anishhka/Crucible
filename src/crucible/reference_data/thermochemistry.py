"""
Builder for the committed thermochemical dataset.

Run with:  python -m crucible.reference_data.thermochemistry

**Provenance, stated plainly.** These are standard-state thermochemical values
(ΔH°f and S° at 298.15 K, 1 bar) of the kind tabulated in every physical
chemistry reference. They were entered by hand and spot-checked, NOT extracted
from a machine-readable primary source and NOT verified entry by entry. That
bound is repeated in the corpus `coverage_note`, in `LIMITATIONS.md`, and in the
assumption stack of every record that uses them.

The table is deliberately small and conservative. A compound whose formation
enthalpy is not confidently known is **omitted**, not estimated: coverage gaps
produce a `null` driving force with a stated reason, which `PROJECT.md` §5.6
explicitly endorses, whereas a guessed value produces a number that looks
computed and is not.

Units in this file are the ones the sources use -- kJ/mol for enthalpy, J/mol/K
for entropy. Conversion to eV/atom happens in `thermo.py`, once, so the
conversion factor lives in exactly one place.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from crucible.normalize import composition_key, normalize_formula  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DATASET_VERSION = "2026.03-r1"

# (formula, delta_H_f kJ/mol at 298.15 K, S° J/mol/K or None, note)
#
# Elements in their standard state have ΔH°f = 0 by definition; they are listed
# explicitly so that a reaction from the elements is computable rather than
# looking like a coverage gap.
THERMO = [
    # --- elements in their reference state ------------------------------
    ("H2", 0.0, 130.7, "reference state, gas"),
    ("O2", 0.0, 205.2, "reference state, gas"),
    ("N2", 0.0, 191.6, "reference state, gas"),
    ("Cl2", 0.0, 223.1, "reference state, gas"),
    ("C", 0.0, 5.7, "graphite, reference state"),
    ("S", 0.0, 32.1, "rhombic, reference state"),
    ("P", 0.0, 41.1, "white, reference state"),
    ("B", 0.0, 5.9, "reference state"),
    ("Si", 0.0, 18.8, "reference state"),
    ("Al", 0.0, 28.3, "reference state"),
    ("Mg", 0.0, 32.7, "reference state"),
    ("Ca", 0.0, 41.6, "reference state"),
    ("Sr", 0.0, 55.0, "reference state"),
    ("Ba", 0.0, 62.5, "reference state"),
    ("Li", 0.0, 29.1, "reference state"),
    ("Na", 0.0, 51.3, "reference state"),
    ("K", 0.0, 64.7, "reference state"),
    ("Ti", 0.0, 30.7, "reference state"),
    ("Zr", 0.0, 39.0, "reference state"),
    ("V", 0.0, 28.9, "reference state"),
    ("Nb", 0.0, 36.4, "reference state"),
    ("Ta", 0.0, 41.5, "reference state"),
    ("Cr", 0.0, 23.8, "reference state"),
    ("Mo", 0.0, 28.7, "reference state"),
    ("W", 0.0, 32.6, "reference state"),
    ("Mn", 0.0, 32.0, "reference state"),
    ("Fe", 0.0, 27.3, "reference state"),
    ("Co", 0.0, 30.0, "reference state"),
    ("Ni", 0.0, 29.9, "reference state"),
    ("Cu", 0.0, 33.2, "reference state"),
    ("Zn", 0.0, 41.6, "reference state"),
    ("Ag", 0.0, 42.6, "reference state"),
    ("Pb", 0.0, 64.8, "reference state"),
    ("Sn", 0.0, 51.2, "reference state"),
    ("Y", 0.0, 44.4, "reference state"),
    ("La", 0.0, 56.9, "reference state"),
    ("Ce", 0.0, 72.0, "reference state"),
    ("Sc", 0.0, 34.6, "reference state"),
    ("Ga", 0.0, 40.9, "reference state"),
    ("In", 0.0, 57.8, "reference state"),
    ("Bi", 0.0, 56.7, "reference state"),
    ("Ge", 0.0, 31.1, "reference state"),
    ("Hf", 0.0, 43.6, "reference state"),

    # --- simple gases and water -----------------------------------------
    ("H2O", -285.83, 69.9, "liquid water at 298 K"),
    ("CO2", -393.51, 213.8, "gas"),
    ("CO", -110.53, 197.7, "gas"),

    # --- binary oxides ---------------------------------------------------
    ("Li2O", -597.9, 37.6, None),
    ("Na2O", -414.2, 75.1, None),
    ("K2O", -363.2, 94.1, None),
    ("MgO", -601.6, 27.0, "periclase"),
    ("CaO", -634.9, 38.1, "lime"),
    ("SrO", -592.0, 54.4, None),
    ("BaO", -553.5, 70.4, None),
    ("Al2O3", -1675.7, 50.9, "corundum"),
    ("SiO2", -910.7, 41.5, "quartz"),
    ("TiO2", -944.0, 50.6, "rutile"),
    ("ZrO2", -1100.6, 50.4, "baddeleyite"),
    ("HfO2", -1144.7, 59.3, None),
    ("V2O5", -1550.6, 131.0, None),
    ("Nb2O5", -1899.5, 137.2, None),
    ("Ta2O5", -2046.0, 143.1, None),
    ("Cr2O3", -1139.7, 81.2, "eskolaite"),
    ("MoO3", -745.1, 77.7, None),
    ("WO3", -842.9, 75.9, None),
    ("MnO", -385.2, 59.7, None),
    ("MnO2", -520.0, 53.1, "pyrolusite"),
    ("Mn2O3", -959.0, 110.5, None),
    ("Mn3O4", -1387.8, 155.6, "hausmannite"),
    ("FeO", -272.0, 60.8, "wuestite"),
    ("Fe2O3", -824.2, 87.4, "hematite"),
    ("Fe3O4", -1118.4, 146.1, "magnetite"),
    ("CoO", -237.9, 53.0, None),
    ("Co3O4", -891.0, 102.5, None),
    ("NiO", -239.7, 38.0, "bunsenite"),
    ("CuO", -157.3, 42.6, "tenorite"),
    ("Cu2O", -168.6, 93.1, "cuprite"),
    ("ZnO", -350.5, 43.7, "zincite"),
    ("Ga2O3", -1089.1, 85.0, None),
    ("In2O3", -925.8, 104.2, None),
    ("SnO2", -580.7, 52.3, "cassiterite"),
    ("PbO", -219.0, 66.5, "litharge"),
    ("Bi2O3", -573.9, 151.5, None),
    ("Y2O3", -1905.3, 99.1, "yttria"),
    ("La2O3", -1793.7, 127.3, None),
    ("CeO2", -1088.7, 62.3, "ceria"),
    ("Sc2O3", -1908.8, 77.0, "scandia"),
    ("GeO2", -580.0, 39.7, None),
    ("B2O3", -1273.5, 54.0, None),
    ("P2O5", -1504.9, 114.5, None),
    ("Sb2O3", -708.8, 123.0, None),

    # --- halides ----------------------------------------------------------
    ("LiF", -616.0, 35.7, None),
    ("NaF", -576.6, 51.1, None),
    ("NaCl", -411.2, 72.1, "halite"),
    ("KCl", -436.5, 82.6, "sylvite"),
    ("KBr", -393.8, 95.9, None),
    ("NaI", -287.8, 98.5, None),
    ("AgCl", -127.0, 96.3, None),
    ("CaF2", -1228.0, 68.5, "fluorite"),
    ("MgF2", -1124.2, 57.2, None),
    ("BaF2", -1207.1, 96.4, None),
    ("CuCl2", -220.1, 108.1, None),
    ("FeCl3", -399.5, 142.3, None),

    # --- sulfides ---------------------------------------------------------
    ("ZnS", -206.0, 57.7, "sphalerite"),
    ("PbS", -100.4, 91.2, "galena"),
    ("CdS", -161.9, 64.9, None),
    ("FeS2", -178.2, 52.9, "pyrite"),
    ("Ag2S", -32.6, 144.0, "acanthite"),
    ("MoS2", -235.1, 62.6, "molybdenite"),

    # --- nitrides, carbides, borides, silicides ---------------------------
    ("AlN", -318.0, 20.2, None),
    ("GaN", -156.8, 29.7, None),
    ("Si3N4", -744.0, 113.0, None),
    ("BN", -254.4, 14.8, None),
    ("TiN", -337.7, 30.3, "osbornite"),
    ("ZrN", -365.3, 38.9, None),
    ("VN", -217.2, 37.3, None),
    ("SiC", -73.2, 16.6, "moissanite"),
    ("TiC", -184.1, 24.2, None),
    ("WC", -38.0, 35.5, None),
    ("Fe3C", 25.1, 104.6, "cementite; endothermic formation"),
    ("TiB2", -323.8, 28.5, None),
    ("ZrB2", -322.6, 35.9, None),
    ("MgB2", -92.0, 36.0, "magnesium diboride"),
    ("Mg2Si", -77.8, 75.9, None),

    # --- carbonates, sulfates, nitrates (common precursors) ---------------
    ("Li2CO3", -1215.9, 90.4, None),
    ("Na2CO3", -1130.7, 135.0, None),
    ("K2CO3", -1151.0, 155.5, None),
    ("MgCO3", -1095.8, 65.7, "magnesite"),
    ("CaCO3", -1207.6, 91.7, "calcite"),
    ("SrCO3", -1220.1, 97.1, "strontianite"),
    ("BaCO3", -1213.0, 112.1, "witherite"),
    ("CaSO4", -1434.5, 106.5, "anhydrite"),
    ("BaSO4", -1473.2, 132.2, "barite"),
    ("CuSO4", -771.4, 109.2, None),
    ("MgSO4", -1284.9, 91.6, None),
    ("KNO3", -494.6, 133.1, "niter"),
    ("NaNO3", -467.9, 116.5, "nitratine"),

    # --- ternary oxides ---------------------------------------------------
    ("BaTiO3", -1659.8, 107.9, "barium titanate"),
    ("SrTiO3", -1672.4, 108.8, "strontium titanate"),
    ("CaTiO3", -1660.6, 93.6, "perovskite"),
    ("MgAl2O4", -2299.1, 80.6, "spinel"),
    ("Mg2SiO4", -2174.0, 95.1, "forsterite"),
    ("Fe2SiO4", -1479.9, 145.2, "fayalite"),
    ("CaSiO3", -1634.9, 81.9, "wollastonite"),
    ("MgSiO3", -1548.5, 67.8, "enstatite"),
    ("ZrSiO4", -2033.4, 84.0, "zircon"),
    ("AlPO4", -1733.8, 90.8, "berlinite"),
]


def build() -> dict:
    rows = []
    seen: set[str] = set()
    for formula, enthalpy, entropy, note in THERMO:
        parsed = normalize_formula(formula)
        if not parsed.parsed_ok:
            raise SystemExit(f"thermo entry {formula!r} does not normalise")
        key = composition_key(parsed.counts)
        if key in seen:
            raise SystemExit(f"duplicate composition key for {formula!r}")
        seen.add(key)
        n_atoms = sum(parsed.counts.values())
        rows.append({
            "key": key,
            "formula": parsed.reduced_formula,
            "delta_hf_kj_per_mol": enthalpy,
            "standard_entropy_j_per_mol_k": entropy,
            "n_atoms_per_formula_unit": n_atoms,
            "note": note,
        })
    rows.sort(key=lambda r: r["key"])
    return {
        "dataset_id": "thermochemistry",
        "dataset_version": DATASET_VERSION,
        "temperature_k": 298.15,
        "pressure_pa": 100000.0,
        "n_entries": len(rows),
        "license": "CC0-1.0 (hand-entered standard values, no third-party data file)",
        "coverage_note": (
            "Standard-state formation enthalpies and entropies at 298.15 K and 1 bar "
            "for ~140 common inorganic compounds and reference-state elements. These "
            "are textbook thermochemical values entered BY HAND and spot-checked; "
            "they were not extracted from a machine-readable primary source and have "
            "not been verified entry by entry. Coverage is limited to well-known "
            "binary and simple ternary compounds: a reaction or hull involving any "
            "species outside this table is reported with a null energy and a stated "
            "reason rather than estimated. Enthalpies are used as a 0 K proxy for "
            "formation energy, which neglects the difference between ΔH(298 K) and "
            "ΔE(0 K) -- typically tens of meV per atom and not negligible at the "
            "thresholds this system applies."
        ),
        "entries": rows,
    }


def main() -> int:
    payload = build()
    path = os.path.join(HERE, "thermochemistry.json")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, ensure_ascii=True)
        fh.write("\n")
    print(f"wrote thermochemistry.json: {payload['n_entries']} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
