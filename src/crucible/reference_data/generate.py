"""
Deterministic builder for the committed offline corpora.

Run with:  python -m crucible.reference_data.generate

Produces four JSON files, one per provider, each indexed by the
convention-independent composition key from normalize.py so that a lookup does
not depend on how any particular source spells a formula.

These are HAND-CURATED lists, not extracts from any database. No network access
is used or required. They are deliberately small, and their smallness is the
point: a corpus this size can almost never support a `no_evidence` conclusion,
which is the correct outcome for a build with no real snapshot behind it. What
it CAN do is recognise the well-known compounds a generator most often
re-proposes, which is exactly what the positive controls in the public fixtures
test.

`corpus_version` is a fixed string, not a timestamp: a novelty claim has to be
reproducible, and a corpus whose version moves with the wall clock makes two
runs a month apart incomparable.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from crucible.normalize import composition_key, normalize_formula  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS_VERSION = "2026.03-r1"

# --------------------------------------------------------------------------- #
# experimental: compounds with an experimental determination behind them
# --------------------------------------------------------------------------- #
EXPERIMENTAL = [
    ("TiO2", "exp-00001", "rutile / anatase titanium dioxide"),
    ("Fe2O3", "exp-00002", "hematite"),
    ("Fe3O4", "exp-00003", "magnetite"),
    ("FeO", "exp-00004", "wuestite"),
    ("LiFePO4", "exp-00005", "olivine cathode phosphate"),
    ("MgB2", "exp-00006", "magnesium diboride superconductor"),
    ("Al2O3", "exp-00007", "corundum"),
    ("SiO2", "exp-00008", "quartz"),
    ("ZnO", "exp-00009", "zincite"),
    ("NaCl", "exp-00010", "halite"),
    ("KCl", "exp-00011", "sylvite"),
    ("CaCO3", "exp-00012", "calcite"),
    ("CaO", "exp-00013", "lime"),
    ("MgO", "exp-00014", "periclase"),
    ("BaTiO3", "exp-00015", "barium titanate perovskite"),
    ("SrTiO3", "exp-00016", "strontium titanate perovskite"),
    ("CaTiO3", "exp-00017", "perovskite"),
    ("PbTiO3", "exp-00018", "lead titanate"),
    ("LiCoO2", "exp-00019", "layered cobalt oxide cathode"),
    ("LiMn2O4", "exp-00020", "spinel manganese oxide cathode"),
    ("LiNiO2", "exp-00021", "layered nickel oxide"),
    ("YBa2Cu3O7", "exp-00022", "YBCO superconductor"),
    ("La2CuO4", "exp-00023", "cuprate parent compound"),
    ("Nb2O5", "exp-00024", "niobium pentoxide"),
    ("V2O5", "exp-00025", "vanadium pentoxide"),
    ("WO3", "exp-00026", "tungsten trioxide"),
    ("MoO3", "exp-00027", "molybdenum trioxide"),
    ("Cr2O3", "exp-00028", "eskolaite"),
    ("MnO2", "exp-00029", "pyrolusite"),
    ("Mn2O3", "exp-00030", "bixbyite"),
    ("Mn3O4", "exp-00031", "hausmannite"),
    ("Co3O4", "exp-00032", "cobalt spinel"),
    ("NiO", "exp-00033", "bunsenite"),
    ("CuO", "exp-00034", "tenorite"),
    ("Cu2O", "exp-00035", "cuprite"),
    ("Ag2O", "exp-00036", "silver oxide"),
    ("Ag2S", "exp-00037", "acanthite"),
    ("ZnS", "exp-00038", "sphalerite / wurtzite"),
    ("CdS", "exp-00039", "greenockite"),
    ("PbS", "exp-00040", "galena"),
    ("FeS2", "exp-00041", "pyrite"),
    ("MoS2", "exp-00042", "molybdenite"),
    ("WS2", "exp-00043", "tungstenite"),
    ("TiN", "exp-00044", "osbornite"),
    ("AlN", "exp-00045", "aluminium nitride"),
    ("GaN", "exp-00046", "gallium nitride"),
    ("Si3N4", "exp-00047", "silicon nitride"),
    ("BN", "exp-00048", "boron nitride"),
    ("SiC", "exp-00049", "moissanite"),
    ("WC", "exp-00050", "tungsten carbide"),
    ("TiC", "exp-00051", "titanium carbide"),
    ("B4C", "exp-00052", "boron carbide"),
    ("CaF2", "exp-00053", "fluorite"),
    ("BaF2", "exp-00054", "barium fluoride"),
    ("MgF2", "exp-00055", "sellaite"),
    ("LiF", "exp-00056", "lithium fluoride"),
    ("NaF", "exp-00057", "villiaumite"),
    ("KBr", "exp-00058", "potassium bromide"),
    ("NaI", "exp-00059", "sodium iodide"),
    ("AgCl", "exp-00060", "chlorargyrite"),
    ("CuCl2", "exp-00061", "tolbachite"),
    ("FeCl3", "exp-00062", "molysite"),
    ("ZrO2", "exp-00063", "baddeleyite / zirconia"),
    ("HfO2", "exp-00064", "hafnia"),
    ("Y2O3", "exp-00065", "yttria"),
    ("La2O3", "exp-00066", "lanthana"),
    ("CeO2", "exp-00067", "ceria"),
    ("Sc2O3", "exp-00068", "scandia"),
    ("Ga2O3", "exp-00069", "gallium oxide"),
    ("In2O3", "exp-00070", "indium oxide"),
    ("SnO2", "exp-00071", "cassiterite"),
    ("PbO", "exp-00072", "litharge"),
    ("Bi2O3", "exp-00073", "bismite"),
    ("Sb2O3", "exp-00074", "senarmontite"),
    ("GeO2", "exp-00075", "argutite"),
    ("BaO", "exp-00076", "barium oxide"),
    ("SrO", "exp-00077", "strontium oxide"),
    ("Li2O", "exp-00078", "lithium oxide"),
    ("Na2O", "exp-00079", "sodium oxide"),
    ("K2O", "exp-00080", "potassium oxide"),
    ("Li2CO3", "exp-00081", "lithium carbonate precursor"),
    ("Na2CO3", "exp-00082", "sodium carbonate"),
    ("BaCO3", "exp-00083", "witherite precursor"),
    ("SrCO3", "exp-00084", "strontianite precursor"),
    ("MgCO3", "exp-00085", "magnesite"),
    ("CaSO4", "exp-00086", "anhydrite"),
    ("BaSO4", "exp-00087", "barite"),
    ("CuSO4", "exp-00088", "chalcocyanite"),
    ("MgSO4", "exp-00089", "magnesium sulfate"),
    ("KNO3", "exp-00090", "niter"),
    ("NaNO3", "exp-00091", "nitratine"),
    ("AlPO4", "exp-00092", "berlinite"),
    ("ZrSiO4", "exp-00093", "zircon"),
    ("Mg2SiO4", "exp-00094", "forsterite"),
    ("Fe2SiO4", "exp-00095", "fayalite"),
    ("CaSiO3", "exp-00096", "wollastonite"),
    ("MgSiO3", "exp-00097", "enstatite"),
    ("MgAl2O4", "exp-00098", "spinel"),
    ("FeAl2O4", "exp-00099", "hercynite"),
    ("ZnAl2O4", "exp-00100", "gahnite"),
    ("NiFe2O4", "exp-00101", "trevorite"),
    ("CoFe2O4", "exp-00102", "cobalt ferrite"),
    ("ZnFe2O4", "exp-00103", "franklinite"),
    ("BaFe12O19", "exp-00104", "barium hexaferrite"),
    ("LaMnO3", "exp-00105", "lanthanum manganite"),
    ("LaFeO3", "exp-00106", "lanthanum ferrite"),
    ("LaCoO3", "exp-00107", "lanthanum cobaltite"),
    ("LaNiO3", "exp-00108", "lanthanum nickelate"),
    ("LaAlO3", "exp-00109", "lanthanum aluminate"),
    ("SrRuO3", "exp-00110", "strontium ruthenate"),
    ("KNbO3", "exp-00111", "potassium niobate"),
    ("NaNbO3", "exp-00112", "sodium niobate"),
    ("LiNbO3", "exp-00113", "lithium niobate"),
    ("LiTaO3", "exp-00114", "lithium tantalate"),
    ("BiFeO3", "exp-00115", "multiferroic perovskite"),
    ("YMnO3", "exp-00116", "hexagonal manganite"),
    ("Ba2ScNbO6", "exp-00117", "ordered double perovskite"),
    ("Sr2FeMoO6", "exp-00118", "double perovskite"),
    ("K2NiF4", "exp-00119", "layered fluoride structure type"),
    ("Y3Al5O12", "exp-00120", "YAG garnet"),
    ("Y3Fe5O12", "exp-00121", "YIG garnet ferrite"),
    ("Li7La3Zr2O12", "exp-00122", "garnet solid electrolyte"),
    ("Li10GeP2S12", "exp-00123", "superionic sulfide conductor"),
    ("Li3PO4", "exp-00124", "lithium phosphate"),
    ("LiPF6", "exp-00125", "electrolyte salt"),
    ("Al", "exp-00126", "aluminium metal"),
    ("Fe", "exp-00127", "iron metal"),
    ("Cu", "exp-00128", "copper metal"),
    ("Ti", "exp-00129", "titanium metal"),
    ("Mg", "exp-00130", "magnesium metal"),
    ("Si", "exp-00131", "silicon"),
    ("C", "exp-00132", "graphite / diamond"),
    ("B", "exp-00133", "boron"),
    ("Nb3Sn", "exp-00134", "A15 superconductor"),
    ("NbTi", "exp-00135", "niobium-titanium alloy"),
    ("Ni3Al", "exp-00136", "superalloy gamma-prime phase"),
    ("Fe3C", "exp-00137", "cementite"),
    ("TiAl", "exp-00138", "titanium aluminide"),
    ("CrN", "exp-00139", "chromium nitride"),
    ("VN", "exp-00140", "vanadium nitride"),
    ("ZrN", "exp-00141", "zirconium nitride"),
    ("TaC", "exp-00142", "tantalum carbide"),
    ("VC", "exp-00143", "vanadium carbide"),
    ("CaB6", "exp-00144", "calcium hexaboride"),
    ("LaB6", "exp-00145", "lanthanum hexaboride"),
    ("TiB2", "exp-00146", "titanium diboride"),
    ("ZrB2", "exp-00147", "zirconium diboride"),
    ("Mg2Si", "exp-00148", "magnesium silicide"),
    ("Bi2Te3", "exp-00149", "thermoelectric telluride"),
    ("PbTe", "exp-00150", "lead telluride"),
    ("SnSe", "exp-00151", "tin selenide"),
    ("CdTe", "exp-00152", "cadmium telluride"),
    ("GaAs", "exp-00153", "gallium arsenide"),
    ("InP", "exp-00154", "indium phosphide"),
    ("CuInSe2", "exp-00155", "chalcopyrite absorber"),
    ("Cu2ZnSnS4", "exp-00156", "kesterite absorber"),
]

# A second experimental-class source with partly overlapping and partly disjoint
# coverage. Its purpose is structural: with only one source per evidence class a
# same-class disagreement can never occur, and the conflict-resolution path
# would be dead code that nothing exercises.
MINERAL_REFERENCE = [
    ("Fe2O3", "min-0001", "hematite, mineralogical reference"),
    ("SiO2", "min-0002", "quartz, mineralogical reference"),
    ("CaCO3", "min-0003", "calcite, mineralogical reference"),
    ("NaCl", "min-0004", "halite, mineralogical reference"),
    ("FeS2", "min-0005", "pyrite, mineralogical reference"),
    ("MgAl2O4", "min-0006", "spinel, mineralogical reference"),
    ("Al2SiO5", "min-0007", "kyanite / andalusite / sillimanite"),
    ("KAlSi3O8", "min-0008", "orthoclase feldspar"),
    ("NaAlSi3O8", "min-0009", "albite feldspar"),
    ("CaAl2Si2O8", "min-0010", "anorthite feldspar"),
    ("Fe2SiO4", "min-0011", "fayalite, mineralogical reference"),
    ("TiO2", "min-0012", "rutile, mineralogical reference"),
    ("ZnS", "min-0013", "sphalerite, mineralogical reference"),
    ("BaSO4", "min-0014", "barite, mineralogical reference"),
    ("CaSO4", "min-0015", "anhydrite, mineralogical reference"),
    ("CaF2", "min-0016", "fluorite, mineralogical reference"),
    ("Cu2O", "min-0017", "cuprite, mineralogical reference"),
    ("MnO2", "min-0018", "pyrolusite, mineralogical reference"),
    ("CaMgSi2O6", "min-0019", "diopside"),
    ("KAlSi2O6", "min-0020", "leucite"),
]

# --------------------------------------------------------------------------- #
# computed: appears in computational corpora, no experimental provenance
# --------------------------------------------------------------------------- #
COMPUTED = [
    ("BaScO3", "cmp-0001", "hypothetical perovskite, computed entry"),
    ("SrScO3", "cmp-0002", "hypothetical perovskite, computed entry"),
    ("CaScO3", "cmp-0003", "hypothetical perovskite, computed entry"),
    ("BaZrO3", "cmp-0004", "computed perovskite"),
    ("SrZrO3", "cmp-0005", "computed perovskite"),
    ("BaHfO3", "cmp-0006", "computed perovskite"),
    ("SrHfO3", "cmp-0007", "computed perovskite"),
    ("LaScO3", "cmp-0008", "computed perovskite"),
    ("YAlO3", "cmp-0009", "computed perovskite"),
    ("YScO3", "cmp-0010", "computed perovskite"),
    ("Ba2ScTaO6", "cmp-0011", "computed double perovskite"),
    ("Ba2YNbO6", "cmp-0012", "computed double perovskite"),
    ("Ba2InNbO6", "cmp-0013", "computed double perovskite"),
    ("Sr2ScNbO6", "cmp-0014", "computed double perovskite"),
    ("Ca2ScNbO6", "cmp-0015", "computed double perovskite"),
    ("Li2MnO3", "cmp-0016", "computed layered oxide"),
    ("Li2TiO3", "cmp-0017", "computed layered oxide"),
    ("Li2ZrO3", "cmp-0018", "computed layered oxide"),
    ("LiVPO4F", "cmp-0019", "computed polyanion cathode"),
    ("LiMnPO4", "cmp-0020", "computed olivine"),
    ("LiCoPO4", "cmp-0021", "computed olivine"),
    ("LiNiPO4", "cmp-0022", "computed olivine"),
    ("NaFePO4", "cmp-0023", "computed sodium olivine"),
    ("NaMnPO4", "cmp-0024", "computed sodium olivine"),
    ("Ca2SiO4", "cmp-0025", "computed silicate"),
    ("Sr2SiO4", "cmp-0026", "computed silicate"),
    ("Ba2SiO4", "cmp-0027", "computed silicate"),
    ("TiO", "cmp-0028", "computed titanium monoxide"),
    ("Ti2O3", "cmp-0029", "computed titanium sesquioxide"),
    ("Ti3O5", "cmp-0030", "computed Magneli phase"),
    ("Ti4O7", "cmp-0031", "computed Magneli phase"),
    ("V2O3", "cmp-0032", "computed vanadium sesquioxide"),
    ("VO2", "cmp-0033", "computed vanadium dioxide"),
    ("NbO2", "cmp-0034", "computed niobium dioxide"),
    ("Ta2O5", "cmp-0035", "computed tantalum pentoxide"),
    ("MoO2", "cmp-0036", "computed molybdenum dioxide"),
    ("ZnTiO3", "cmp-0037", "computed titanate"),
    ("MgTiO3", "cmp-0038", "computed titanate"),
    ("CoTiO3", "cmp-0039", "computed titanate"),
    ("NiTiO3", "cmp-0040", "computed titanate"),
    ("MnTiO3", "cmp-0041", "computed titanate"),
    ("FeTiO3", "cmp-0042", "computed ilmenite"),
    ("TaON", "cmp-0043", "computed oxynitride"),
    ("BAs", "cmp-0044", "computed boron arsenide"),
    ("BSb", "cmp-0045", "computed boron antimonide"),
    ("AlSb", "cmp-0046", "computed antimonide"),
    ("GaSb", "cmp-0047", "computed antimonide"),
    ("InSb", "cmp-0048", "computed antimonide"),
    ("ZnSe", "cmp-0049", "computed selenide"),
    ("ZnTe", "cmp-0050", "computed telluride"),
    ("MgSe", "cmp-0051", "computed selenide"),
    ("CaSe", "cmp-0052", "computed selenide"),
    ("Fe4O5", "cmp-0053", "computed intermediate iron oxide"),
    ("Fe5O6", "cmp-0054", "computed intermediate iron oxide"),
    ("Fe4O7", "cmp-0055", "computed intermediate iron oxide"),
]

# --------------------------------------------------------------------------- #
# literature: a REPLAY cassette of recorded question/answer pairs
# --------------------------------------------------------------------------- #
#
# Every entry here is a question that was recorded. A question NOT in this list
# produces `unavailable`, never `miss` -- the defining property of replay mode,
# and the single most consequential distinction in the system.
LITERATURE_CASSETTE = [
    ("TiO2", "lit-0001", 4821, "extensively reported"),
    ("Fe2O3", "lit-0002", 3944, "extensively reported"),
    ("LiFePO4", "lit-0003", 2610, "extensively reported"),
    ("MgB2", "lit-0004", 1877, "extensively reported"),
    ("BaTiO3", "lit-0005", 2304, "extensively reported"),
    ("SrTiO3", "lit-0006", 1988, "extensively reported"),
    ("YBa2Cu3O7", "lit-0007", 2765, "extensively reported"),
    ("ZnO", "lit-0008", 3512, "extensively reported"),
    ("Al2O3", "lit-0009", 2901, "extensively reported"),
    ("GaN", "lit-0010", 2233, "extensively reported"),
    ("WO3", "lit-0011", 1120, "reported"),
    ("V2O5", "lit-0012", 1004, "reported"),
    ("Nb2O5", "lit-0013", 812, "reported"),
    ("Cr2O3", "lit-0014", 655, "reported"),
    ("K2NiF4", "lit-0015", 402, "reported as a structure type"),
    ("SiO2", "lit-0016", 4102, "extensively reported"),
    ("NaCl", "lit-0017", 1533, "extensively reported"),
    ("LiCoO2", "lit-0018", 1744, "extensively reported"),
    ("Li7La3Zr2O12", "lit-0019", 921, "reported"),
    ("Ba2ScNbO6", "lit-0020", 47, "sparsely reported"),
    ("SrRuO3", "lit-0021", 588, "reported"),
    ("LaMnO3", "lit-0022", 1203, "extensively reported"),
    ("Bi2Te3", "lit-0023", 1655, "extensively reported"),
    ("MoS2", "lit-0024", 2988, "extensively reported"),
    ("Cu2ZnSnS4", "lit-0025", 977, "reported"),
    ("CuInSe2", "lit-0026", 1244, "reported"),
    ("Si3N4", "lit-0027", 1390, "reported"),
    ("TiN", "lit-0028", 1077, "reported"),
    ("ZrO2", "lit-0029", 2044, "extensively reported"),
    ("CeO2", "lit-0030", 1602, "extensively reported"),
    ("Fe3O4", "lit-0031", 2455, "extensively reported"),
    ("Fe17O23", "lit-0032", 0, "recorded query, no documents found"),
    ("PbTiO3", "lit-0033", 1188, "reported"),
    ("LiNbO3", "lit-0034", 1466, "extensively reported"),
    ("MgAl2O4", "lit-0035", 833, "reported"),
    ("CaCO3", "lit-0036", 2711, "extensively reported"),
    ("MgO", "lit-0037", 1955, "extensively reported"),
    ("CaF2", "lit-0038", 744, "reported"),
    ("CuSO4*5H2O", "lit-0039", 288, "recorded as the pentahydrate"),
    ("C8H8", "lit-0040", 1902, "recorded query; polystyrene repeat unit"),
]


def _build(entries, *, evidence_class, provenance, corpus_id, kind,
           coverage_note, license_note, with_counts=False):
    rows = []
    seen: set[str] = set()
    for item in entries:
        if with_counts:
            formula, identifier, n_docs, note = item
        else:
            formula, identifier, note = item
            n_docs = None
        result = normalize_formula(formula)
        if not result.parsed_ok:
            raise SystemExit(f"corpus entry {formula!r} does not normalise: "
                             f"{result.rejected_reason}")
        key = composition_key(result.counts)
        if key in seen:
            continue  # an alternate spelling of an already-recorded composition
        seen.add(key)
        row = {
            "key": key,
            "reduced_formula": result.reduced_formula,
            "identifier": identifier,
            "provenance": provenance,
            "note": note,
        }
        if n_docs is not None:
            row["n_documents"] = n_docs
        rows.append(row)
    rows.sort(key=lambda r: (r["key"], r["identifier"]))
    return {
        "corpus_id": corpus_id,
        "corpus_version": CORPUS_VERSION,
        "kind": kind,
        "evidence_class": evidence_class,
        "coverage_note": coverage_note,
        "license": license_note,
        "n_entries": len(rows),
        "entries": rows,
    }


def build_all() -> dict[str, dict]:
    return {
        "experimental_snapshot.json": _build(
            EXPERIMENTAL, evidence_class="experimental", provenance="experimental",
            corpus_id="experimental_snapshot", kind="snapshot",
            coverage_note=(
                "Hand-curated list of well-established inorganic compounds with "
                "experimental provenance: common binary and ternary oxides, halides, "
                "sulfides, nitrides, carbides, borides, perovskites, spinels, garnets, "
                "common battery and superconductor phases, and elemental metals. It is "
                "NOT an extract of any crystallographic database and covers a few "
                "hundred compositions out of the hundreds of thousands that have been "
                "reported. A miss is evidence of absence from THIS list only and must "
                "not be read as evidence of novelty."
            ),
            license_note="CC0-1.0 (hand-authored, no third-party data)",
        ),
        "mineral_reference.json": _build(
            MINERAL_REFERENCE, evidence_class="experimental", provenance="experimental",
            corpus_id="mineral_reference", kind="snapshot",
            coverage_note=(
                "A small independent mineralogical reference list, overlapping the main "
                "experimental snapshot in part and disjoint from it in part. Present so "
                "that two sources of the SAME evidence class can disagree, which is the "
                "only condition under which NOVEL.EVIDENCE.SOURCES_DISAGREE is a "
                "meaningful finding rather than a restatement of the fact that "
                "different corpora answer different questions."
            ),
            license_note="CC0-1.0 (hand-authored, no third-party data)",
        ),
        "computed_snapshot.json": _build(
            COMPUTED, evidence_class="computed", provenance="computed",
            corpus_id="computed_snapshot", kind="snapshot",
            coverage_note=(
                "Compositions of the kind that populate large density-functional "
                "corpora: plausible substitutional variants of known structure types, "
                "most of which have never been made. A hit here is evidence that "
                "someone calculated it, which is a materially weaker claim than an "
                "experimental determination, and is tiered as computed_only."
            ),
            license_note="CC0-1.0 (hand-authored, no third-party data)",
        ),
        "literature_cassette.json": _build(
            LITERATURE_CASSETTE, evidence_class="literature", provenance="literature",
            corpus_id="literature_cassette", kind="cassette",
            coverage_note=(
                "A REPLAY cassette of recorded bibliographic queries. Coverage is the "
                "set of questions that were recorded, not a corpus of documents. A "
                "composition absent from this cassette yields `unavailable`, never "
                "`miss`: the question was never asked, and recording that as an absence "
                "of prior art is the most consequential error this system can make."
            ),
            license_note="CC0-1.0 (hand-authored, no third-party data)",
            with_counts=True,
        ),
    }


def main() -> int:
    for filename, payload in sorted(build_all().items()):
        path = os.path.join(HERE, filename)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True, ensure_ascii=True)
            fh.write("\n")
        print(f"wrote {filename}: {payload['n_entries']} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
