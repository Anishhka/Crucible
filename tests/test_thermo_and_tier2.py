"""
Tier 2 tests: thermodynamics, repairs, caching.

The thermodynamic tests are written against results that are known independently
of this implementation -- the wüstite disproportionation, the Ellingham
reduction ordering, the sign of a few textbook reactions. A test that asserted
"convex_hull returns what convex_hull returns" would have passed happily while
the oxygen-potential sign was inverted and while gas-phase water was being read
as a liquid, which are exactly the two bugs this file was written to catch.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO_ROOT, "src")
sys.path.insert(0, SRC)

from crucible import cache as cache_mod, repairs as repairs_mod, thermo  # noqa: E402
from crucible.config import DEFAULTS  # noqa: E402
from crucible.feasibility import screen  # noqa: E402
from crucible.normalize import normalize_formula  # noqa: E402

FIXTURES = os.environ.get(
    "CRUCIBLE_FIXTURES",
    os.path.abspath(os.path.join(REPO_ROOT, "tests", "fixtures", "public")))

FEAS = DEFAULTS["feasibility"]
ROUTES = DEFAULTS["routes"]


def run_cli(args):
    env = dict(os.environ, PYTHONPATH=SRC, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-m", "crucible", *args],
                          env=env, capture_output=True, text=True)


def fixture(name):
    path = os.path.join(FIXTURES, name)
    if not os.path.exists(path):
        pytest.skip(f"fixture not available: {path}")
    return path


def counts(formula):
    parsed = normalize_formula(formula)
    assert parsed.parsed_ok, formula
    return parsed.counts


# --------------------------------------------------------------------------- #
# convex hull, against results known independently of this code
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("formula", ["Fe2O3", "Fe3O4", "TiO2", "Al2O3", "MgO",
                                     "BaTiO3", "MgAl2O4", "CaCO3", "NaCl"])
def test_stable_compounds_sit_on_the_hull(formula):
    """Every one of these is a thermodynamically stable phase. If the hull put
    any of them above itself, the hull construction is wrong."""
    hull = thermo.convex_hull(counts(formula))
    assert hull.energy_above_hull is not None, f"{formula} should have an energy"
    assert hull.energy_above_hull == pytest.approx(0.0, abs=1e-6), (
        f"{formula} is a stable phase but came out {hull.energy_above_hull} above the hull")


def test_wustite_is_metastable_against_magnetite_and_iron():
    """FeO disproportionates to Fe3O4 + Fe below about 570 C. This is a real
    result the table was not tuned to produce, so reproducing it is evidence the
    hull code is right rather than merely running."""
    hull = thermo.convex_hull(counts("FeO"))
    assert hull.energy_above_hull is not None
    assert hull.energy_above_hull > 0.01, (
        "FeO should be above the hull, not on it")
    products = {d["formula"] for d in hull.decomposition}
    assert products == {"Fe3O4", "Fe"}, (
        f"expected disproportionation to Fe3O4 + Fe, got {products}")


def test_hull_is_computed_for_uncovered_compositions_but_own_energy_is_not():
    """The regime that matters: a novel composition gets the competing phases,
    and explicitly NOT a fabricated formation energy."""
    hull = thermo.convex_hull(counts("Ba2ScNbO6"))
    assert hull.hull_energy_per_atom is not None, "competing phases should be found"
    assert hull.formation_energy_per_atom is None, (
        "this composition is not in the table; inventing its energy would be the "
        "whole failure mode")
    assert hull.energy_above_hull is None
    assert hull.decomposition


def test_no_thermochemical_coverage_yields_nulls_not_guesses():
    hull = thermo.convex_hull(counts("XeF2") if False else counts("Kr"))
    assert hull.hull_energy_per_atom is None
    assert hull.reason


# --------------------------------------------------------------------------- #
# reaction energies
# --------------------------------------------------------------------------- #

def test_binary_oxide_route_to_barium_titanate_is_exothermic():
    energy = thermo.reaction_energy(
        [{"formula": "BaO", "coefficient": 1.0}, {"formula": "TiO2", "coefficient": 1.0}],
        [{"formula": "BaTiO3", "coefficient": 1.0}])
    assert energy.value_ev_per_atom is not None
    assert energy.value_ev_per_atom < 0, "BaO + TiO2 -> BaTiO3 is exothermic"


def test_carbonate_route_is_endothermic_at_room_temperature():
    """This is correct and counter-intuitive: the carbonate route is driven by
    the entropy of CO2 release at furnace temperature, which an enthalpy at
    298 K does not capture. The caveat is on the route's assumption list."""
    energy = thermo.reaction_energy(
        [{"formula": "BaCO3", "coefficient": 1.0}, {"formula": "TiO2", "coefficient": 1.0}],
        [{"formula": "BaTiO3", "coefficient": 1.0}, {"formula": "CO2", "coefficient": 1.0}])
    assert energy.value_ev_per_atom is not None
    assert energy.value_ev_per_atom > 0


def test_an_uncovered_species_gives_a_null_driving_force_and_names_it():
    energy = thermo.reaction_energy(
        [{"formula": "Y2O3", "coefficient": 0.5}, {"formula": "BaCO3", "coefficient": 2.0}],
        [{"formula": "YBa2Cu3O7", "coefficient": 1.0}])
    assert energy.value_ev_per_atom is None
    assert energy.missing, "the missing species must be named, not just omitted"
    assert "null" in energy.reason or "not" in energy.reason.lower()


# --------------------------------------------------------------------------- #
# open system: the Ellingham ordering
# --------------------------------------------------------------------------- #

REDUCED_BY_HYDROGEN = ["CuO", "Cu2O", "NiO", "Fe2O3"]
NOT_REDUCED_BY_HYDROGEN = ["TiO2", "Al2O3", "MgO", "SiO2", "Cr2O3", "ZrO2"]


@pytest.mark.parametrize("formula", REDUCED_BY_HYDROGEN)
def test_oxides_that_hydrogen_reduces(formula):
    result = thermo.atmosphere_stability(counts(formula), "H2", 1223.15)
    assert result.stable is False, (
        f"{formula} is reduced by hydrogen at 950 C; the screen says it survives")


@pytest.mark.parametrize("formula", NOT_REDUCED_BY_HYDROGEN)
def test_oxides_that_hydrogen_does_not_reduce(formula):
    result = thermo.atmosphere_stability(counts(formula), "H2", 1223.15)
    assert result.stable is True, (
        f"{formula} is not reduced by hydrogen at 950 C; the screen says it is")


@pytest.mark.parametrize("formula", REDUCED_BY_HYDROGEN + NOT_REDUCED_BY_HYDROGEN)
def test_every_oxide_survives_air(formula):
    result = thermo.atmosphere_stability(counts(formula), "air", 1223.15)
    assert result.stable is True, f"{formula} should be stable in air"


def test_oxygen_potential_ordering_is_monotonic_in_how_reducing_the_gas_is():
    order = ["O2", "air", "CO2", "Ar", "vacuum", "H2"]
    potentials = [thermo.oxygen_chemical_potential(a, 1223.15)[0] for a in order]
    assert potentials == sorted(potentials, reverse=True), (
        f"mu_O should fall monotonically from O2 to H2, got {potentials}")


def test_hydrogen_oxygen_potential_is_near_the_textbook_value():
    """Around -2.9 eV at 1223 K. Getting this wrong by ~1 eV -- which is what
    reading liquid-water data instead of gas does -- flips several oxides."""
    mu, _ = thermo.oxygen_chemical_potential("H2", 1223.15)
    assert -3.4 < mu < -2.4, mu


def test_a_non_oxide_gets_no_atmosphere_screen():
    result = thermo.atmosphere_stability(counts("MgB2"), "H2", 1223.15)
    assert result.stable is None
    assert "no oxygen" in result.reason.lower()


# --------------------------------------------------------------------------- #
# feasibility integration
# --------------------------------------------------------------------------- #

def test_frame_is_grand_potential_when_an_open_system_screen_ran():
    result = screen(counts("TiO2"), None, FEAS, {})
    assert result.frame == "grand_potential"
    assert any(a.stable is not None for a in result.atmospheres)


def test_frame_is_not_evaluated_when_nothing_is_covered():
    result = screen(counts("Kr"), None, FEAS, {})
    assert result.frame == "not_evaluated"


def test_plausible_requires_a_real_hull_distance():
    """`plausible` must be unreachable without a computed energy above hull --
    that is the guarantee that separates a real verdict from a guess."""
    covered = screen(counts("TiO2"), None, FEAS, {})
    assert covered.verdict == "plausible"
    assert covered.hull.energy_above_hull is not None

    uncovered = screen(counts("Ba2ScNbO6"), None, FEAS, {})
    assert uncovered.verdict != "plausible"
    assert uncovered.hull.energy_above_hull is None


def test_threshold_is_reported_only_where_it_was_applied():
    from crucible.feasibility import feasibility_block
    covered = screen(counts("TiO2"), None, FEAS, {})
    block = feasibility_block(covered, None, FEAS)
    assert block["threshold_used_ev_per_atom"] == FEAS["stability_threshold_ev_per_atom"]

    uncovered = screen(counts("Ba2ScNbO6"), None, FEAS, {})
    block = feasibility_block(uncovered, None, FEAS)
    assert block["threshold_used_ev_per_atom"] is None, (
        "a threshold reported against a quantity that was never computed is a lie")


def test_a_compound_on_the_hull_does_not_report_decomposing_into_itself():
    from crucible.feasibility import feasibility_block
    result = screen(counts("TiO2"), None, FEAS, {})
    block = feasibility_block(result, None, FEAS)
    assert "decomposition_products" not in block or not block["decomposition_products"]
    assert block["decomposition_energy_ev_per_atom"] is None


def test_chemical_potentials_reach_the_assumption_stack():
    from crucible.feasibility import assumption_stack
    result = screen(counts("Fe2O3"), None, FEAS, {})
    stack = assumption_stack({}, DEFAULTS["validity"], FEAS, False, result)
    assert stack["chemical_potentials"], (
        "the mu_O values the screen used are part of the frame and must be recorded")
    assert any(k.startswith("mu_O__") for k in stack["chemical_potentials"])


# --------------------------------------------------------------------------- #
# repairs
# --------------------------------------------------------------------------- #

def test_patch_application_is_correct():
    proposal = {"proposal_id": "P", "composition": {"formula": "Fe4O6"},
                "extra": "remove me"}
    patched = repairs_mod.apply_patch(proposal, [
        {"op": "replace", "path": "/composition/formula", "value": "Fe2O3"},
        {"op": "remove", "path": "/extra"},
    ])
    assert patched["composition"]["formula"] == "Fe2O3"
    assert "extra" not in patched
    assert proposal["composition"]["formula"] == "Fe4O6", "the original must not be mutated"


def test_no_repair_is_offered_for_a_homoglyph_formula():
    """Guessing which element was meant is exactly the failure this system
    exists to catch, so declining to repair is the correct output."""
    proposal = {"proposal_id": "H", "composition": {"formula": "Fe2О3"}}
    violations = [{"code": "FMT.ENCODING.NON_ASCII_SYMBOL",
                   "json_pointer": "/composition/formula", "level": "error"}]
    assert repairs_mod.propose_repairs(proposal, violations, None) == []


def test_reverified_is_never_set_without_actually_re_running():
    """The flag is set by reverify() and nowhere else. A repair whose patch will
    not apply must come back unverified rather than optimistic."""
    proposal = {"proposal_id": "P", "composition": {"formula": "Fe4O6"}}
    repair = repairs_mod.Repair(
        repair_id="bogus", targets=["X.TEST"], kind="minimal_edit",
        patch=[{"op": "replace", "path": "/nonexistent/deeply/nested", "value": 1}],
        rationale="a patch that cannot apply")
    assert repair.reverified is False

    calls = []

    def verify_fn(candidate):
        calls.append(candidate)
        return {"verdict": {"status": "accepted", "reward": 1.0}}

    repairs_mod.reverify(proposal, [repair], verify_fn)
    assert repair.reverified is False
    assert repair.post_repair_status == "not_evaluated"


def test_preference_pairs_only_come_from_repairs_that_helped(tmp_path):
    out = tmp_path / "out"
    result = run_cli(["run", "--input", fixture("02_messy.proposals.json"),
                      "--out", str(out), "--offline", "--seed", "12345",
                      "--now", "2026-01-01T00:00:00Z"])
    assert result.returncode in (0, 4)

    verdicts = {}
    for line in (out / "verdicts.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            verdicts[record["record_id"]] = record

    rows = [json.loads(line) for line in
            (out / "feedback" / "preference_pairs.jsonl").read_text(
                encoding="utf-8").splitlines() if line.strip()]
    assert rows, "the messy fixture should yield at least one usable repair"

    for row in rows:
        record = verdicts[row["record_id"]]
        repair = next(r for r in record["repairs"] if r["repair_id"] == row["repair_id"])
        assert repair["reverified"] is True, (
            "a preference pair may only come from a re-verified repair")
        assert repair["post_repair_reward"] > record["verdict"]["reward"], (
            "a repair that did not improve the record must not become a pair")


def test_repairs_that_did_not_help_are_still_reported(tmp_path):
    """A repair that was tried and did not work is information, not noise."""
    out = tmp_path / "out"
    run_cli(["run", "--input", fixture("02_messy.proposals.json"), "--out", str(out),
             "--offline", "--seed", "12345", "--now", "2026-01-01T00:00:00Z"])
    all_repairs = [r for line in (out / "verdicts.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip()
        for r in json.loads(line).get("repairs", [])]
    assert all_repairs
    assert all(r["reverified"] for r in all_repairs), (
        "every proposed repair should have been genuinely re-verified")
    pairs = [json.loads(line) for line in
             (out / "feedback" / "preference_pairs.jsonl").read_text(
                 encoding="utf-8").splitlines() if line.strip()]
    assert len(all_repairs) > len(pairs), (
        "some repairs should not have helped; if all of them did, the filter is "
        "not being exercised")


# --------------------------------------------------------------------------- #
# content-addressed cache
# --------------------------------------------------------------------------- #

def test_cache_key_moves_when_anything_that_could_change_the_verdict_moves():
    base = dict(candidate_key="sha256:" + "a" * 64, config_sha256="c" * 64,
                reference_rows=[{"id": "x@1", "sha256": "d" * 64}],
                crucible_version="1.0.0", taxonomy_version="1.0.0",
                record_schema_version="1-0-0")
    original = cache_mod.compute_key(**base)

    for field, value in [
        ("candidate_key", "sha256:" + "b" * 64),
        ("config_sha256", "e" * 64),
        ("crucible_version", "1.0.1"),
        ("taxonomy_version", "1.1.0"),
        ("record_schema_version", "2-0-0"),
        ("reference_rows", [{"id": "x@2", "sha256": "d" * 64}]),
        ("reference_rows", [{"id": "x@1", "sha256": "f" * 64}]),
    ]:
        moved = dict(base)
        moved[field] = value
        assert cache_mod.compute_key(**moved) != original, (
            f"changing {field} must invalidate the cache entry")


def test_a_cache_hit_produces_byte_identical_output_to_a_miss(tmp_path):
    """The only property that makes the cache safe."""
    cache_dir = tmp_path / "cache"
    cold, warm = tmp_path / "cold", tmp_path / "warm"
    common = ["run", "--input", fixture("01_baseline.proposals.json"),
              "--offline", "--seed", "12345", "--now", "2026-01-01T00:00:00Z",
              "--cache-dir", str(cache_dir)]

    assert run_cli([*common, "--out", str(cold)]).returncode in (0, 4)
    assert run_cli([*common, "--out", str(warm)]).returncode in (0, 4)

    for name in ("verdicts.jsonl", "violations.jsonl", "data_quality.json"):
        assert (cold / name).read_bytes() == (warm / name).read_bytes(), name

    for companion in sorted((cold / "figures").glob("*.json")):
        twin = warm / "figures" / companion.name
        assert companion.read_bytes() == twin.read_bytes(), companion.name

    stats = json.loads((warm / "run_manifest.json").read_text(
        encoding="utf-8"))["provenance"]["environment"]["cache"]
    assert stats["hits"] > 0 and stats["misses"] == 0


def test_a_superset_rerun_only_processes_what_is_new(tmp_path):
    cache_dir = tmp_path / "cache"
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()

    import shutil
    shutil.copy2(fixture("01_baseline.proposals.json"), batch_dir)
    first = tmp_path / "first"
    run_cli(["run", "--input", str(batch_dir), "--out", str(first), "--offline",
             "--seed", "1", "--now", "2026-01-01T00:00:00Z",
             "--cache-dir", str(cache_dir)])
    stats = json.loads((first / "run_manifest.json").read_text(
        encoding="utf-8"))["provenance"]["environment"]["cache"]
    assert stats["hits"] == 0 and stats["misses"] == 6

    shutil.copy2(fixture("02_messy.proposals.json"), batch_dir)
    second = tmp_path / "second"
    run_cli(["run", "--input", str(batch_dir), "--out", str(second), "--offline",
             "--seed", "1", "--now", "2026-01-01T00:00:00Z",
             "--cache-dir", str(cache_dir)])
    stats = json.loads((second / "run_manifest.json").read_text(
        encoding="utf-8"))["provenance"]["environment"]["cache"]
    assert stats["hits"] == 6, (
        "every record from the first batch should have been served from cache")
    assert stats["misses"] > 0, "the new records should have been processed"


def test_no_cache_directory_is_written_when_none_is_requested(tmp_path):
    """The contract forbids writing outside --out, so a graded run must not
    create a cache anywhere."""
    out = tmp_path / "out"
    before = set(os.listdir(tmp_path))
    run_cli(["run", "--input", fixture("01_baseline.proposals.json"), "--out", str(out),
             "--offline", "--seed", "12345", "--now", "2026-01-01T00:00:00Z"])
    after = set(os.listdir(tmp_path))
    assert after - before == {"out"}

    stats = json.loads((out / "run_manifest.json").read_text(
        encoding="utf-8"))["provenance"]["environment"]["cache"]
    assert stats["enabled"] is False


# --------------------------------------------------------------------------- #
# figures
# --------------------------------------------------------------------------- #

def test_a_real_hull_figure_is_emitted_with_its_energies(tmp_path):
    out = tmp_path / "out"
    run_cli(["run", "--input", fixture("01_baseline.proposals.json"), "--out", str(out),
             "--offline", "--seed", "12345", "--now", "2026-01-01T00:00:00Z"])

    companions = list((out / "figures").glob("*.hull.json"))
    assert companions, "a binary system with energies should produce a hull figure"
    payload = json.loads(companions[0].read_text(encoding="utf-8"))
    assert payload["figure_kind"] == "convex_hull"
    assert payload["phases"], "the figure companion must carry the data it draws"
    assert all("formation_energy_ev_per_atom" in p for p in payload["phases"])
    assert payload["hull_vertices"]
    assert payload["caveat"]


def test_a_chemical_potential_figure_is_emitted_with_its_boundary(tmp_path):
    out = tmp_path / "out"
    run_cli(["run", "--input", fixture("01_baseline.proposals.json"), "--out", str(out),
             "--offline", "--seed", "12345", "--now", "2026-01-01T00:00:00Z"])

    companions = list((out / "figures").glob("*.chempot.json"))
    assert companions
    payload = json.loads(companions[0].read_text(encoding="utf-8"))
    assert payload["figure_kind"] == "oxygen_chemical_potential"
    assert payload["atmospheres"]
    assert all("mu_oxygen_ev" in a for a in payload["atmospheres"])


def test_figure_kinds_include_both_required_thermodynamic_and_route(tmp_path):
    out = tmp_path / "out"
    run_cli(["run", "--input", fixture("01_baseline.proposals.json"), "--out", str(out),
             "--offline", "--seed", "12345", "--now", "2026-01-01T00:00:00Z"])
    kinds = {a["kind"] for line in (out / "verdicts.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip()
        for a in json.loads(line).get("artifacts", [])}
    assert "phase_diagram" in kinds
    assert "chempot_diagram" in kinds
    assert "reaction_network" in kinds
