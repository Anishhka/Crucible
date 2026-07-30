"""
Scientific-correctness tests: named controls, metamorphic invariants, and
adversarial coverage parametrised across variants.

PROJECT.md §10 is explicit that a test asserting a function returns what the
function returns contributes nothing. These are written against invariants that
would survive a rewrite of the implementation.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from crucible import chemdata  # noqa: E402
from crucible.claims import audit_claims  # noqa: E402
from crucible.composition import check_charge_balance, check_composition  # noqa: E402
from crucible.config import DEFAULTS  # noqa: E402
from crucible.normalize import (composition_key, normalize_formula,  # noqa: E402
                                reduce_counts, spell)
from crucible.providers import novelty_block, query_all  # noqa: E402
from crucible.recordcheck import check_record, safe_slug  # noqa: E402
from crucible.routes import assess_hint, propose_routes  # noqa: E402
from crucible.structure import check_structure  # noqa: E402

VALIDITY = DEFAULTS["validity"]
ROUTES = DEFAULTS["routes"]


# --------------------------------------------------------------------------- #
# NAMED CONTROLS (PROJECT.md §5.10)
# --------------------------------------------------------------------------- #
#
# "How do you know this is right" needs a better answer than "the tests pass".
# Each control below names a material whose answer is known independently of
# this implementation.

POSITIVE_CONTROLS = [
    ("TiO2", "rutile titanium dioxide, reported since the 19th century"),
    ("Fe2O3", "hematite, one of the most-characterised oxides in existence"),
    ("LiFePO4", "olivine cathode, commercialised and in production"),
    ("MgB2", "magnesium diboride, superconductivity reported in 2001"),
]

NEGATIVE_CONTROL = ("Fe17O23", "an iron-to-oxygen ratio no reported phase has")


@pytest.mark.parametrize("formula,why", POSITIVE_CONTROLS)
def test_positive_control_is_never_reported_as_novel(formula, why):
    """If novelty logic reports any of these as having no prior art, that is not
    a threshold that needs tuning; it is a defect."""
    parsed = normalize_formula(formula)
    novelty = novelty_block(query_all(parsed.counts))
    assert novelty["tier"] == "experimentally_reported", (
        f"{formula} ({why}) came back as {novelty['tier']}")


def test_negative_control_is_not_reported_as_known():
    formula, why = NEGATIVE_CONTROL
    parsed = normalize_formula(formula)
    novelty = novelty_block(query_all(parsed.counts))
    assert novelty["tier"] in ("no_evidence", "unknown"), (
        f"{formula} ({why}) came back as {novelty['tier']}")


def test_mgb2_is_not_rejected_by_the_ionic_model():
    """The judgment named explicitly in PROJECT.md §5.4. A screen that treats a
    neutrality failure as fatal discards a famous superconductor."""
    result = check_charge_balance({"Mg": 1, "B": 2}, VALIDITY)
    assert result.applicable is False
    assert result.balanced is None


@pytest.mark.parametrize("formula", [
    "MgB2", "TiB2", "ZrB2", "LaB6", "CaB6",     # borides
    "Mg2Si", "SiC", "Fe3C", "TiC", "WC",        # silicides and carbides
    "NbTi", "Nb3Sn", "Ni3Al", "TiAl",           # intermetallics
    "Fe", "Cu", "Al",                           # elemental metals
])
def test_metallic_class_is_never_falsely_rejected(formula):
    """The exclusion must generalise. Special-casing MgB2 by name would pass the
    public fixture and fail on the unseen one, which holds other members of the
    same class.

    The invariant is *never falsely rejected*, not *always skipped*: a few
    members of this family (SiC being the obvious one) do balance cleanly under
    a formal ionic assignment, and reporting that is correct. What must never
    happen is balanced=False on a material that demonstrably exists.
    """
    parsed = normalize_formula(formula)
    result = check_charge_balance(parsed.counts, VALIDITY)
    assert result.balanced is not False, (
        f"{formula} is a real material and was rejected by the ionic screen")


@pytest.mark.parametrize("formula", ["Fe2O3", "TiO2", "NaCl", "MgO", "BaTiO3", "Al2O3"])
def test_ordinary_ionic_compounds_are_still_screened(formula):
    """The escape hatch must not swallow the compounds the screen is for."""
    parsed = normalize_formula(formula)
    result = check_charge_balance(parsed.counts, VALIDITY)
    assert result.applicable is True and result.balanced is True


# --------------------------------------------------------------------------- #
# METAMORPHIC INVARIANTS
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("formula", ["TiO2", "Fe2O3", "LiFePO4", "Ba2ScNbO6",
                                     "MgB2", "YBa2Cu3O7", "CaCO3"])
def test_reduction_is_idempotent(formula):
    once = normalize_formula(formula).reduced_formula
    twice = normalize_formula(once).reduced_formula
    assert once == twice


@pytest.mark.parametrize("formula,multiplier", [
    ("Fe2O3", 2), ("Fe2O3", 3), ("TiO2", 4), ("BaTiO3", 2), ("LiFePO4", 5),
])
def test_scaling_every_coefficient_does_not_change_the_reduced_formula(formula, multiplier):
    base = normalize_formula(formula)
    scaled_counts = {k: v * multiplier for k, v in base.counts.items()}
    scaled = reduce_counts(scaled_counts)[0]
    assert composition_key(scaled) == composition_key(base.counts)


@pytest.mark.parametrize("a,b", [
    ("Fe2O3", "Fe4O6"),      # supercell
    ("TiO2", "Ti2O4"),
    ("TiO₂", "TiO2"),        # typographic subscripts
    ("ＴｉＯ２", "TiO2"),        # fullwidth forms
    ("Ti​O2", "TiO2"),  # zero-width space
    ("Ca(OH)2", "CaO2H2"),   # parentheses
])
def test_two_spellings_that_mean_the_same_thing_share_a_candidate_key(a, b):
    """Two spellings that normalise to the same composition must produce the same
    key, or deduplication silently splits."""
    left, right = normalize_formula(a), normalize_formula(b)
    assert left.parsed_ok and right.parsed_ok
    assert composition_key(left.counts) == composition_key(right.counts)


def test_a_formula_that_is_already_reduced_is_not_flagged():
    """Reduction is by GCD, not by re-ordering. TiO2 is reduced; flagging it
    poisons the code-by-field table that is this system's main product."""
    for formula in ["TiO2", "MgB2", "WO3", "NaCl", "BaTiO3", "LiFePO4", "SiO2"]:
        parsed = normalize_formula(formula)
        assert not parsed.not_reduced, f"{formula} was wrongly flagged NOT_REDUCED"
        assert "CHEM.FORMULA.NOT_REDUCED" not in [c for c, _ in parsed.findings]


def test_a_genuinely_unreduced_formula_is_flagged():
    parsed = normalize_formula("Fe4O6")
    assert parsed.not_reduced
    assert "CHEM.FORMULA.NOT_REDUCED" in [c for c, _ in parsed.findings]


@pytest.mark.parametrize("convention", ["reduced_hill", "reduced_alphabetical",
                                        "reduced_spaced"])
def test_every_provider_convention_round_trips_to_the_same_composition(convention):
    """Formula conventions differ per source and a mismatch returns zero rows
    with a success status. Whatever the spelling, the composition behind it must
    be identical."""
    parsed = normalize_formula("LiFePO4")
    spelled = spell(parsed.counts, convention)
    reparsed = normalize_formula(spelled.replace(" ", ""))
    assert composition_key(reparsed.counts) == composition_key(parsed.counts)


# --------------------------------------------------------------------------- #
# ADVERSARIAL COVERAGE, parametrised across variants
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("formula,description", [
    ("Fe2О3", "Cyrillic capital O"),
    ("CuСl2", "Cyrillic capital S standing in for C"),
    ("BaРbO3", "Cyrillic capital R standing in for P"),
    ("Αl2O3", "Greek capital Alpha standing in for A"),
    ("TiΟ2", "Greek capital Omicron standing in for O"),
    ("ΒaTiO3", "Greek capital Beta standing in for B"),
])
def test_homoglyph_formulas_are_rejected_never_repaired(formula, description):
    """Guessing which element was meant produces a confident wrong answer, which
    is worse than no answer."""
    parsed = normalize_formula(formula)
    assert not parsed.parsed_ok, f"{description} was silently accepted"
    assert parsed.rejected_code == "FMT.ENCODING.NON_ASCII_SYMBOL"


@pytest.mark.parametrize("formula,description", [
    ("Ti​O2", "zero width space"),
    ("Ti‌O2", "zero width non-joiner"),
    ("Ti‍O2", "zero width joiner"),
    ("Zr⁠O2", "word joiner"),
    ("﻿TiO2", "byte order mark"),
    ("TiO2⁠", "trailing word joiner"),
])
def test_invisible_characters_are_recovered_but_never_silently(formula, description):
    """The control is a category check over format characters, not a list of the
    two or three that were thought of first. Recovery is correct; silent
    recovery is not."""
    parsed = normalize_formula(formula)
    assert parsed.parsed_ok, f"{description} should be recoverable"
    codes = [code for code, _ in parsed.findings]
    assert "FMT.ENCODING.INVISIBLE_CHARACTER" in codes, (
        f"{description} was stripped without recording that it happened")
    assert parsed.steps, "the transformation must appear in the audit trail"


@pytest.mark.parametrize("formula", ["Xx2O3", "Qq1O2", "Zz4", "Jj2O", "Ee3N2"])
def test_wellformed_but_unreal_element_symbols_are_rejected(formula):
    """A parser built on a capital-then-lowercase regex accepts these without
    complaint. Being well-formed in shape is not being real."""
    parsed = normalize_formula(formula)
    assert not parsed.parsed_ok
    assert parsed.rejected_code == "CHEM.FORMULA.UNKNOWN_ELEMENT"


@pytest.mark.parametrize("bad_id", [
    "../../etc/passwd", "../../../root/.ssh/id_rsa", "/etc/shadow",
    "..\\..\\windows\\system32", "a/../../b", "....//....//etc",
])
def test_path_traversal_identifiers_never_reach_a_filesystem_path(bad_id):
    """The declared pattern exists so this value never reaches the code that
    decides where to write. safe_slug is the second, independent barrier."""
    proposal = {"proposal_id": bad_id, "composition": {"formula": "SiO2"}}
    assert not check_record(proposal).conforms

    slug = safe_slug(bad_id)
    assert "/" not in slug and "\\" not in slug and ".." not in slug
    assert os.path.basename(slug) == slug


@pytest.mark.parametrize("extra_field", [
    "unexpected_field", "system_prompt", "__proto__", "constructor", "eval",
])
def test_undeclared_fields_are_surfaced_not_tolerated(extra_field):
    """The input contract is closed: an unrecognised field means the generator
    and the verifier disagree about the contract."""
    proposal = {"proposal_id": "A-1", "composition": {"formula": "GaN"},
                extra_field: "anything"}
    result = check_record(proposal)
    assert not result.conforms
    assert "FMT.SCHEMA.UNKNOWN_FIELD" in result.codes


@pytest.mark.parametrize("value", [-999, 999, 9999, -9999, -99, -1, 99999, -999999])
def test_placeholder_values_are_handled_as_a_family(value):
    """-999 is finite, correctly typed, and passes every range check not written
    with placeholders in mind -- and so are its relatives.

    Positive 99 is deliberately absent from this list: it is a perfectly
    ordinary dielectric constant, and flagging it would trade a real
    false-negative for a worse false-positive. The line is drawn at negative
    repdigits and magnitudes from 999 upward.
    """
    proposal = {
        "proposal_id": "S-1", "composition": {"formula": "SrTiO3"},
        "claimed_properties": [
            {"name": "dielectric_constant", "value": value, "unit": "1"}],
    }
    result = audit_claims(proposal)
    flagged = ("CLAIM.SENTINEL.SUSPECTED" in [f.code for f in result.findings]
               or "CLAIM.VALUE.PHYSICALLY_IMPOSSIBLE" in [f.code for f in result.findings])
    assert flagged, f"{value} passed the claim audit unremarked"


@pytest.mark.parametrize("value,unit,expected_ev", [
    (2100, "meV", 2.1), (2.1, "eV", 2.1), (0.0021, "keV", 2.1),
])
def test_unit_conversion_is_applied_and_recorded(value, unit, expected_ev):
    """Silent unit drift produces numbers wrong by exactly three orders of
    magnitude and completely plausible."""
    proposal = {"proposal_id": "U-1", "composition": {"formula": "Fe2O3"},
                "claimed_properties": [
                    {"name": "band_gap", "value": value, "unit": unit}]}
    result = audit_claims(proposal)
    assert result.entries[0]["normalized_value"] == pytest.approx(expected_ev, rel=1e-6)
    if unit != "eV":
        assert result.unit_normalizations, "the conversion factor must be recorded"


def test_negative_band_gap_is_physically_impossible_not_merely_small():
    proposal = {"proposal_id": "N-1", "composition": {"formula": "K2NiF4"},
                "claimed_properties": [
                    {"name": "band_gap", "value": -1.2, "unit": "eV"}]}
    result = audit_claims(proposal)
    assert "CLAIM.VALUE.PHYSICALLY_IMPOSSIBLE" in [f.code for f in result.findings]


def test_finite_but_overflow_scale_values_are_caught():
    """1e308 survives a finiteness check and overflows any arithmetic done on it."""
    proposal = {"proposal_id": "O-1", "composition": {"formula": "Nb2O5"},
                "claimed_properties": [
                    {"name": "melting_point", "value": 1e308, "unit": "K"}]}
    result = audit_claims(proposal)
    assert result.entries[0]["assessment"] == "implausible"


def test_non_ascii_unit_string_is_reported():
    """A unit differing from the expected one by a single non-ASCII character
    will not match a table of known units by string comparison."""
    proposal = {"proposal_id": "M-1", "composition": {"formula": "Cr2O3"},
                "claimed_properties": [
                    {"name": "band_gap", "value": 3.4, "unit": "−eV"}]}
    result = audit_claims(proposal)
    assert "CLAIM.UNITS.INCONSISTENT" in [f.code for f in result.findings]


# --------------------------------------------------------------------------- #
# STRUCTURE
# --------------------------------------------------------------------------- #

def _cubic(a, element="Na", second="Cl", z2=0.5, unit="angstrom"):
    return {
        "proposal_id": "X", "composition": {"formula": f"{element}{second}"},
        "structure": {
            "lattice": {"a": a, "b": a, "c": a, "alpha": 90.0, "beta": 90.0,
                        "gamma": 90.0, "length_unit": unit},
            "sites": [
                {"element": element, "frac_coords": [0.0, 0.0, 0.0]},
                {"element": second, "frac_coords": [z2, z2, z2]},
            ],
        },
    }


def test_overlapping_atoms_are_detected_across_the_periodic_boundary():
    """Two sites can be far apart inside the cell and adjacent across its edge."""
    proposal = _cubic(5.64, z2=0.001)
    result = check_structure(proposal, {"Na": 1, "Cl": 1}, VALIDITY)
    assert "STRUCT.GEOM.ATOM_OVERLAP" in [f.code for f in result.findings]


def test_a_reasonable_structure_raises_nothing():
    proposal = _cubic(5.64, z2=0.5)
    result = check_structure(proposal, {"Na": 1, "Cl": 1}, VALIDITY)
    assert not [f for f in result.findings if f.level == "error"]


@pytest.mark.parametrize("unit,factor", [("nm", 10.0), ("pm", 0.01)])
def test_lattice_units_are_converted_before_anything_is_measured(unit, factor):
    """A pipeline that ignores the unit field builds a cell a thousand times too
    large and finds that nothing is bonded to anything."""
    proposal = _cubic(5.64, unit=unit)
    result = check_structure(proposal, {"Na": 1, "Cl": 1}, VALIDITY)
    assert result.measured["a_angstrom"] == pytest.approx(5.64 * factor)


def test_density_of_a_known_structure_is_recovered():
    """Rutile TiO2 is 4.23 g/cm3. Getting this wrong means the geometry maths is
    wrong in a way no schema check would reveal."""
    proposal = {
        "proposal_id": "R", "composition": {"formula": "TiO2"},
        "structure": {
            "lattice": {"a": 4.5937, "b": 4.5937, "c": 2.9587,
                        "alpha": 90.0, "beta": 90.0, "gamma": 90.0},
            "sites": [
                {"element": "Ti", "frac_coords": [0.0, 0.0, 0.0]},
                {"element": "Ti", "frac_coords": [0.5, 0.5, 0.5]},
                {"element": "O", "frac_coords": [0.3053, 0.3053, 0.0]},
                {"element": "O", "frac_coords": [0.6947, 0.6947, 0.0]},
                {"element": "O", "frac_coords": [0.8053, 0.1947, 0.5]},
                {"element": "O", "frac_coords": [0.1947, 0.8053, 0.5]},
            ],
        },
    }
    result = check_structure(proposal, {"Ti": 1, "O": 2}, VALIDITY)
    assert result.measured["density_g_per_cm3"] == pytest.approx(4.23, abs=0.05)


def test_asserted_symmetry_is_compared_against_the_lattice_metric():
    """A cubic cell asserted as P1 is a disagreement worth reporting -- and the
    tolerance it depends on has to be recorded, which is why it is a parameter."""
    proposal = _cubic(5.64)
    proposal["structure"]["space_group"] = {"symbol": "P1", "number": 1}
    result = check_structure(proposal, {"Na": 1, "Cl": 1}, VALIDITY)
    assert "STRUCT.SYMMETRY.SPACEGROUP_INCONSISTENT" in [f.code for f in result.findings]


def test_occupancies_are_compared_as_ratios_not_raw_totals():
    """A cell holding Z formula units is correct and extremely common; comparing
    raw totals would flag every well-formed structure."""
    proposal = _cubic(5.64)
    proposal["structure"]["sites"] = [
        {"element": "Na", "frac_coords": [0.0, 0.0, 0.0]},
        {"element": "Na", "frac_coords": [0.5, 0.5, 0.0]},
        {"element": "Cl", "frac_coords": [0.5, 0.5, 0.5]},
        {"element": "Cl", "frac_coords": [0.0, 0.0, 0.5]},
    ]
    result = check_structure(proposal, {"Na": 1, "Cl": 1}, VALIDITY)
    assert "STRUCT.SITES.OCCUPANCY_INCONSISTENT" not in [f.code for f in result.findings]


# --------------------------------------------------------------------------- #
# ROUTES
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("formula", ["BaTiO3", "LiFePO4", "SrTiO3", "MgAl2O4",
                                     "Ba2ScNbO6", "LaMnO3", "MgB2"])
def test_every_proposed_route_actually_balances(formula):
    """Asserting `balanced: true` without checking is the kind of claim this
    system exists to catch, so the assertion is re-derived here independently."""
    parsed = normalize_formula(formula)
    for route in propose_routes(parsed.counts, ROUTES, parsed.reduced_formula):
        assert route["reaction"]["balanced"] is True
        assert route["reaction"]["normalization"], (
            "a balanced equation has an arbitrary scale; the convention must be stated")
        assert route["assumptions"], "every route must state what it rests on"


def test_reducing_atmosphere_on_an_oxide_is_incoherent():
    """YBCO under hydrogen: the composition is right, the temperature is
    reasonable, and the atmosphere destroys the material. A verifier that checks
    only the composition misses it entirely."""
    proposal = {
        "proposal_id": "Y", "composition": {"formula": "YBa2Cu3O7"},
        "synthesis_hint": {"route": "solid_state", "atmosphere": "H2",
                           "temperature": 950, "temperature_unit": "C"},
    }
    parsed = normalize_formula("YBa2Cu3O7")
    result = assess_hint(proposal, parsed.counts)
    assert result.hint_assessment["consistent"] is False
    assert "FEAS.SYNTH.HINT_INCOHERENT" in [f.code for f in result.findings]


def test_a_coherent_hint_is_not_flagged():
    proposal = {
        "proposal_id": "B", "composition": {"formula": "BaTiO3"},
        "synthesis_hint": {"route": "solid_state", "atmosphere": "air",
                           "precursors": ["BaCO3", "TiO2 (anatase)"],
                           "temperature": 1200, "temperature_unit": "C"},
    }
    parsed = normalize_formula("BaTiO3")
    result = assess_hint(proposal, parsed.counts)
    assert result.hint_assessment["consistent"] is True, result.hint_assessment["issues"]


# --------------------------------------------------------------------------- #
# SCOPE
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("formula", ["C8H8", "C2H4", "C6H6", "C2H6O"])
def test_molecular_and_polymeric_species_are_reported_out_of_scope(formula):
    """Reporting one as out of scope, with the reason, is a correct answer.
    Running a bulk-crystal argument on a polymer is not."""
    parsed = normalize_formula(formula)
    assert parsed.parsed_ok
    assert parsed.in_scope is False
    assert parsed.scope_reason, "a conclusion without its reason cannot be argued with"


@pytest.mark.parametrize("formula", ["TiO2", "MgB2", "LiFePO4", "NaCl", "Fe3C", "CaCO3"])
def test_inorganic_solids_are_in_scope(formula):
    assert normalize_formula(formula).in_scope is True


# --------------------------------------------------------------------------- #
# EVIDENCE
# --------------------------------------------------------------------------- #

def test_a_replay_miss_is_unavailable_and_never_a_miss():
    """Recording a replay miss as an absence of prior art converts 'we didn't
    look' into 'it's novel'. This is the most consequential error in the system."""
    parsed = normalize_formula("Ba2ScTaO6")  # in the computed corpus, not the cassette
    queries = query_all(parsed.counts)
    cassette = next(q for q in queries if q.mode == "replay")
    assert cassette.status == "unavailable"
    assert cassette.error


def test_no_evidence_requires_every_provider_to_have_answered():
    """`no_evidence` and `unknown` must not be collapsed."""
    parsed = normalize_formula("Fe17O23")
    queries = query_all(parsed.counts)
    assert all(q.status in ("hit", "miss") for q in queries)
    assert novelty_block(queries)["tier"] == "no_evidence"


def test_every_provider_appears_including_ones_that_could_not_answer():
    """Omitting an unreachable provider is a silent-failure bug, not a tidy
    output."""
    parsed = normalize_formula("Ba2ScTaO6")
    queries = query_all(parsed.counts)
    from crucible.providers import PROVIDERS
    assert {q.provider for q in queries} == {p.name for p in PROVIDERS}


def test_each_query_records_the_exact_string_sent_to_that_provider():
    """This is what makes a null result auditable rather than merely reported."""
    parsed = normalize_formula("Fe4O6")
    for query in query_all(parsed.counts):
        assert query.query, f"{query.provider} recorded no query string"
        assert query.normalization_note


def test_same_class_disagreement_is_recorded_and_resolved():
    parsed = normalize_formula("MgB2")  # experimental snapshot has it, mineral list does not
    novelty = novelty_block(query_all(parsed.counts))
    assert novelty["conflicts"], "a same-class disagreement must be recorded"
    for conflict in novelty["conflicts"]:
        assert conflict["resolution"], "a conflict must be resolved by a stated rule"


def test_periodic_table_is_complete_enough_to_not_misreport_real_elements():
    """A curated subset silently reclassifies real chemistry as 'unknown element'."""
    for symbol in ["H", "He", "Og", "U", "Am", "Lu", "Yb", "Tc", "Po", "At", "Fr"]:
        assert chemdata.is_element(symbol)
    assert len(chemdata.KNOWN_ELEMENTS) == 118
