"""
Tier 3 tests: lineage reconstruction and cross-run aggregation.

Both commands are read-only projections over artifacts that already exist, so
the invariants worth protecting are about *honesty of the answer* rather than
about computation: an UNKNOWN must never be reported as a MATCH, a tampered
artifact must never pass, and an aggregate must refuse to silently pool runs
that are not comparable.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO_ROOT, "src")
sys.path.insert(0, SRC)

from crucible import artifacts  # noqa: E402
from crucible.aggregate import aggregate  # noqa: E402
from crucible.lineage import DIVERGED, MATCH, UNKNOWN, reconstruct  # noqa: E402

FIXTURES = os.environ.get(
    "CRUCIBLE_FIXTURES",
    os.path.abspath(os.path.join(REPO_ROOT, "tests", "fixtures", "public")))


def run_cli(args):
    env = dict(os.environ, PYTHONPATH=SRC, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-m", "crucible", *args],
                          env=env, capture_output=True, text=True)


def fixture(name):
    path = os.path.join(FIXTURES, name)
    if not os.path.exists(path):
        pytest.skip(f"fixture not available: {path}")
    return path


def make_run(out, name="01_baseline.proposals.json", now="2026-01-01T00:00:00Z"):
    result = run_cli(["run", "--input", fixture(name), "--out", str(out),
                      "--offline", "--seed", "1", "--now", now])
    assert result.returncode in (0, 4), result.stderr[-1500:]
    return out


def status_of(report, component_prefix):
    for finding in report.findings:
        if finding.component.startswith(component_prefix):
            return finding.status
    return None


# --------------------------------------------------------------------------- #
# the source hash
# --------------------------------------------------------------------------- #

def test_source_hash_is_stable_and_full_length():
    first = artifacts.source_tree_sha256()
    assert len(first) == 64, "must fit tool_versions' 64-character limit"
    assert first == artifacts.source_tree_sha256(), "must be deterministic"


def test_source_hash_ignores_compiled_artifacts(tmp_path):
    """A __pycache__ directory appearing must not change the identity of the
    code, or every run after the first import would look like a different build."""
    before = artifacts.source_tree_sha256()
    import crucible.normalize  # noqa: F401  - ensure bytecode exists
    assert artifacts.source_tree_sha256() == before


def test_source_hash_is_recorded_in_the_manifest(tmp_path):
    out = make_run(tmp_path / "run")
    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    recorded = manifest["tool_versions"]["crucible_source_sha256"]
    assert recorded == artifacts.source_tree_sha256()


def test_an_envelope_rejection_also_records_lineage(tmp_path):
    """A rejected run is still a run somebody may need to trace."""
    out = tmp_path / "rejected"
    run_cli(["run", "--input", fixture("07_unsupported_version.proposals.json"),
             "--out", str(out), "--offline", "--seed", "1",
             "--now", "2026-01-01T00:00:00Z"])
    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["tool_versions"]["crucible_source_sha256"]


# --------------------------------------------------------------------------- #
# lineage
# --------------------------------------------------------------------------- #

def test_a_fresh_run_is_fully_reproducible(tmp_path):
    out = make_run(tmp_path / "run")
    report = reconstruct(str(out), fixture("01_baseline.proposals.json"))
    assert report.ok, [f.detail for f in report.diverged]
    assert not report.unknown, [f.component for f in report.unknown]
    assert status_of(report, "code") == MATCH
    assert status_of(report, "config") == MATCH
    assert status_of(report, "artifacts") == MATCH
    assert status_of(report, "corpus experimental_snapshot") == MATCH


def test_lineage_exits_zero_on_a_clean_run_and_one_when_diverged(tmp_path):
    out = make_run(tmp_path / "run")
    assert run_cli(["lineage", "--out", str(out)]).returncode == 0

    (out / "verdicts.jsonl").write_text("{}\n", encoding="utf-8")
    assert run_cli(["lineage", "--out", str(out)]).returncode == 1


def test_tampering_with_an_artifact_is_detected(tmp_path):
    out = make_run(tmp_path / "run")
    with (out / "verdicts.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"tampered": true}\n')

    report = reconstruct(str(out))
    assert status_of(report, "artifacts") == DIVERGED
    assert not report.ok


def test_a_changed_corpus_is_detected_and_named(tmp_path, monkeypatch):
    """A novelty tier is a claim relative to a corpus version. If the corpus
    moved, every tier in that run is relative to the old contents."""
    out = make_run(tmp_path / "run")
    manifest_path = out / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    target = next(r for r in manifest["reference_data"]
                  if r["provider"] == "experimental_snapshot")
    target["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest, sort_keys=True,
                                        separators=(",", ":")) + "\n", encoding="utf-8")

    report = reconstruct(str(out))
    assert status_of(report, "corpus experimental_snapshot") == DIVERGED


def test_an_unverifiable_input_is_unknown_not_diverged(tmp_path):
    """UNKNOWN must never be folded into DIVERGED: a file that is not present is
    not a file that changed, and reporting it as one makes the tool cry wolf."""
    out = make_run(tmp_path / "run")
    report = reconstruct(str(out))  # deliberately no --input
    assert status_of(report, "input ") == UNKNOWN
    assert report.ok, "an unverifiable input alone must not fail the lineage"


def test_a_modified_input_file_is_detected(tmp_path):
    batch = tmp_path / "batch.json"
    shutil.copy2(fixture("01_baseline.proposals.json"), batch)
    out = make_run(tmp_path / "run")

    # The run read the fixture; point lineage at a different file of the same name.
    altered = tmp_path / "altered"
    altered.mkdir()
    payload = json.loads(batch.read_text(encoding="utf-8"))
    payload["batch_id"] = "tampered-batch"
    (altered / "01_baseline.proposals.json").write_text(
        json.dumps(payload), encoding="utf-8")

    report = reconstruct(str(out), str(altered))
    assert status_of(report, "input ") == DIVERGED


def test_a_changed_config_names_what_moved(tmp_path):
    """Reporting that a hash differs is useless; reporting which setting moved is
    the point of comparing structurally."""
    out = make_run(tmp_path / "run")
    manifest_path = out / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["config"]["resolved"]["feasibility"]["stability_threshold_ev_per_atom"] = 0.5
    manifest_path.write_text(json.dumps(manifest, sort_keys=True,
                                        separators=(",", ":")) + "\n", encoding="utf-8")

    report = reconstruct(str(out))
    finding = next(f for f in report.findings if f.component == "config")
    assert finding.status == DIVERGED
    assert "stability_threshold_ev_per_atom" in finding.detail


def test_lineage_on_a_missing_directory_is_a_usage_error(tmp_path):
    assert run_cli(["lineage", "--out", str(tmp_path / "nope")]).returncode == 2


# --------------------------------------------------------------------------- #
# cross-run aggregation
# --------------------------------------------------------------------------- #

def _three_runs(tmp_path):
    directories = []
    for index, name in enumerate(["01_baseline.proposals.json",
                                  "02_messy.proposals.json",
                                  "03_adversarial.proposals.json"], start=1):
        out = tmp_path / f"run{index}"
        make_run(out, name, now=f"2026-0{index}-01T00:00:00Z")
        directories.append(str(out))
    return directories


def test_aggregate_pools_every_run(tmp_path):
    payload = aggregate(_three_runs(tmp_path))
    assert payload["n_runs"] == 3
    assert payload["n_records"] > 0
    assert len(payload["runs"]) == 3


def test_aggregate_is_ordered_by_logical_time_not_argument_order(tmp_path):
    """Determinism: the same runs named in any order produce the same report."""
    directories = _three_runs(tmp_path)
    forward = aggregate(directories)
    backward = aggregate(list(reversed(directories)))
    assert forward == backward


def test_code_by_field_is_the_actionable_table(tmp_path):
    """The whole point of PROJECT.md §1: which kind of junk, on which field, at
    what rate, across batches."""
    payload = aggregate(_three_runs(tmp_path))
    assert payload["code_by_field"]
    for row in payload["code_by_field"]:
        assert row["code"] and row["json_pointer"]
        assert row["total_findings"] >= 1
        assert 1 <= row["runs_present_in"] <= 3
        assert row["findings_per_100_records"] is not None


def test_trend_reports_movement_between_first_and_last_run(tmp_path):
    payload = aggregate(_three_runs(tmp_path))
    assert payload["code_trend"]
    for row in payload["code_trend"]:
        assert len(row["per_run"]) == 3
        if row["first_run_per_100"] is not None and row["last_run_per_100"] is not None:
            assert row["change_per_100"] == pytest.approx(
                row["last_run_per_100"] - row["first_run_per_100"], abs=1e-6)


def test_finding_lifecycle_uses_fingerprints(tmp_path):
    """`fingerprint` is defined as stable across runs for the same finding on the
    same input; this is the consumer it exists for."""
    payload = aggregate(_three_runs(tmp_path))
    lifecycle = payload["finding_lifecycle"]
    assert lifecycle["new_in_latest"] >= 0
    assert lifecycle["resolved_since_earlier"] >= 0
    assert all(f.startswith("sha256:") for f in lifecycle["new_fingerprints"])


def test_the_same_run_twice_has_no_new_or_resolved_findings(tmp_path):
    """A metamorphic check: aggregating a run against a copy of itself must show
    every finding as persistent, nothing new and nothing resolved."""
    first = tmp_path / "a"
    make_run(first, "02_messy.proposals.json")
    second = tmp_path / "b"
    shutil.copytree(first, second)

    payload = aggregate([str(first), str(second)])
    lifecycle = payload["finding_lifecycle"]
    assert lifecycle["new_in_latest"] == 0
    assert lifecycle["resolved_since_earlier"] == 0
    assert lifecycle["persistent"] > 0


def test_incomparable_runs_are_flagged_not_silently_pooled(tmp_path):
    """Runs from different taxonomy versions may not be compared without an
    explicit mapping. Pooling them quietly would be the failure."""
    directories = _three_runs(tmp_path)
    manifest_path = os.path.join(directories[1], "run_manifest.json")
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    manifest["taxonomy_version"] = "2.0.0"
    manifest["reward_fn_id"] = "reward.other@2.0.0"
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, sort_keys=True, separators=(",", ":"))
        fh.write("\n")

    payload = aggregate(directories)
    assert payload["comparability"]["compatible"] is False
    warnings = " ".join(payload["comparability"]["warnings"])
    assert "taxonomy" in warnings
    assert "reward" in warnings
    # Still aggregates, rather than refusing outright.
    assert payload["n_runs"] == 3


def test_a_different_corpus_makes_novelty_incomparable(tmp_path):
    directories = _three_runs(tmp_path)
    manifest_path = os.path.join(directories[0], "run_manifest.json")
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    manifest["reference_data"][0]["sha256"] = "1" * 64
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, sort_keys=True, separators=(",", ":"))
        fh.write("\n")

    payload = aggregate(directories)
    assert payload["comparability"]["compatible"] is False
    assert any("evidence base" in w or "novelty" in w
               for w in payload["comparability"]["warnings"])


def test_aggregate_writes_both_artifacts_and_carries_caveats(tmp_path):
    directories = _three_runs(tmp_path)
    out = tmp_path / "agg"
    result = run_cli(["aggregate", "--runs", *directories, "--out", str(out)])
    assert result.returncode == 0, result.stderr[-1000:]

    payload = json.loads((out / "aggregate.json").read_text(encoding="utf-8"))
    assert payload["caveats"], "an aggregate that names no limits is overclaiming"

    html = (out / "aggregate.html").read_text(encoding="utf-8")
    assert "http://" not in html and "https://" not in html
    assert "Cross-run aggregate" in html


def test_aggregate_on_a_non_run_directory_is_a_usage_error(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert run_cli(["aggregate", "--runs", str(empty),
                    "--out", str(tmp_path / "agg")]).returncode == 2


# --------------------------------------------------------------------------- #
# the evaluation-harness case: two pipeline versions, same batch
# --------------------------------------------------------------------------- #

def test_two_pipeline_configs_on_one_batch_isolate_what_moved(tmp_path):
    """PROJECT.md §9's "small evaluation harness comparing two versions of the
    pipeline on the same batch" is this: aggregate two runs of one input that
    differ only in configuration.

    The thing worth protecting is that the report says the two are not
    comparable-as-generator-evidence. A rate that moved because a tolerance
    moved is a fact about the verifier, and presenting it as a fact about the
    generator would be the whole failure.
    """
    config = tmp_path / "widened.json"
    config.write_text(
        json.dumps({"validity": {"min_interatomic_distance_angstrom": 0.005}}),
        encoding="utf-8")

    before, after = tmp_path / "before", tmp_path / "after"
    make_run(before, "02_messy.proposals.json", now="2026-01-01T00:00:00Z")
    result = run_cli(["run", "--input", fixture("02_messy.proposals.json"),
                      "--out", str(after), "--offline", "--seed", "1",
                      "--now", "2026-01-02T00:00:00Z", "--config", str(config)])
    assert result.returncode in (0, 4), result.stderr[-1000:]

    payload = aggregate([str(before), str(after)])

    assert payload["comparability"]["compatible"] is False
    assert any("configuration" in w for w in payload["comparability"]["warnings"]), (
        "a config difference between two runs must be surfaced; it is the most "
        "likely reason a rate moved when the batch did not")
    assert len(payload["comparability"]["config_hashes"]) == 2

    # Widening the minimum-distance tolerance must retire the overlap finding
    # and nothing else, which is precisely the signal an eval harness exists for.
    moved = {row["code"]: row["change_per_100"] for row in payload["code_trend"]
             if row["change_per_100"]}
    assert "STRUCT.GEOM.ATOM_OVERLAP" in moved
    assert moved["STRUCT.GEOM.ATOM_OVERLAP"] < 0
    assert payload["finding_lifecycle"]["resolved_since_earlier"] >= 1
    assert payload["finding_lifecycle"]["new_in_latest"] == 0


def test_identical_configs_are_not_flagged_as_incomparable(tmp_path):
    """The converse: the warning must not fire when nothing differs, or it
    becomes noise nobody reads."""
    first, second = tmp_path / "a", tmp_path / "b"
    make_run(first, "01_baseline.proposals.json", now="2026-01-01T00:00:00Z")
    make_run(second, "01_baseline.proposals.json", now="2026-02-01T00:00:00Z")

    payload = aggregate([str(first), str(second)])
    assert payload["comparability"]["compatible"] is True
    assert len(payload["comparability"]["config_hashes"]) == 1
