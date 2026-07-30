"""
Contract-level tests: exit codes, determinism, count identity, hardening.

These run the program, not a mock of it. The determinism test in particular MUST
actually execute the pipeline twice and diff the bytes -- a test that asserts
the code *should* be deterministic tests nothing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO_ROOT, "src")

# The spec packet sits beside the repo in the normal layout; override for CI.
FIXTURES = os.environ.get(
    "CRUCIBLE_FIXTURES",
    os.path.abspath(os.path.join(REPO_ROOT, "..", "fixtures", "public")),
)

GRADED = ["verdicts.jsonl", "violations.jsonl", "data_quality.json"]


def run_cli(args, **kwargs):
    env = dict(os.environ, PYTHONPATH=SRC, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-m", "crucible", *args],
                          env=env, capture_output=True, text=True, **kwargs)


def fixture(name):
    path = os.path.join(FIXTURES, name)
    if not os.path.exists(path):
        pytest.skip(f"fixture not available: {path}")
    return path


def verify_run(tmp_path, name, extra=()):
    out = tmp_path / name.replace(".", "_")
    result = run_cli(["run", "--input", fixture(name), "--out", str(out),
                      "--offline", "--seed", "12345",
                      "--now", "2026-01-01T00:00:00Z", *extra])
    return result, out


# --------------------------------------------------------------------------- #
# exit codes
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name,accepted", [
    ("01_baseline.proposals.json", (0, 4)),
    ("02_messy.proposals.json", (4,)),
    ("03_adversarial.proposals.json", (4,)),
    ("04_malformed_nonfinite.proposals.json", (3,)),
    ("05_malformed_duplicate_key.proposals.json", (3,)),
    ("06_malformed_depth.proposals.json", (3,)),
    ("07_unsupported_version.proposals.json", (3,)),
])
def test_fixture_exit_codes(name, accepted, tmp_path):
    result, _ = verify_run(tmp_path, name)
    assert result.returncode in accepted, (
        f"{name}: expected exit in {accepted}, got {result.returncode}\n"
        f"stderr:\n{result.stderr[-2000:]}")


@pytest.mark.parametrize("name", [
    "04_malformed_nonfinite.proposals.json",
    "05_malformed_duplicate_key.proposals.json",
    "06_malformed_depth.proposals.json",
    "07_unsupported_version.proposals.json",
])
def test_envelope_rejection_still_records_what_it_knows(name, tmp_path):
    """Exit 3 must still write a manifest and a data-quality report. A rejection
    nobody can read is indistinguishable from a crash."""
    _, out = verify_run(tmp_path, name)
    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    quality = json.loads((out / "data_quality.json").read_text(encoding="utf-8"))

    assert manifest["status"] == "failed"
    assert manifest["counts"]["proposals_in"] == 0
    assert manifest["reference_data"] == [], (
        "a run that read no proposal consulted no corpus; inventing an entry "
        "would be a claim about a lookup that never happened")
    assert "reward" not in manifest["aggregates"], (
        "a mean over zero verdicts is not a number and must be omitted")
    assert manifest["input"]["files"], "the file that was read must still be named"
    assert manifest["notes"], "the reason for rejection must be recorded"
    assert any(rule["n_triggered"] > 0 for rule in quality["rules"]), (
        "the rule that fired must be visible in data_quality.json")
    assert not (out / "verdicts.jsonl").exists(), (
        "an envelope rejection must not imply work that was not done")


def test_one_bad_record_is_never_an_envelope_failure(tmp_path):
    """A single defective proposal inside a well-formed envelope is exit 4, not
    exit 3. Returning 3 there throws away every good verdict in the batch."""
    result, out = verify_run(tmp_path, "03_adversarial.proposals.json")
    assert result.returncode == 4
    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["verdicts_out"] > 0, (
        "the well-formed records in a hostile batch must still be verified")


# --------------------------------------------------------------------------- #
# accounting
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name", [
    "01_baseline.proposals.json",
    "02_messy.proposals.json",
    "03_adversarial.proposals.json",
])
def test_every_record_is_accounted_for(name, tmp_path):
    """proposals_in == verdicts_out + quarantined, always. A run that silently
    loses a record fails this identity."""
    _, out = verify_run(tmp_path, name)
    counts = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))["counts"]
    assert counts["proposals_in"] == counts["verdicts_out"] + counts["quarantined"]

    quality = json.loads((out / "data_quality.json").read_text(encoding="utf-8"))
    assert len(quality["quarantined"]) == quality["totals"]["records_quarantined"]


@pytest.mark.parametrize("name", [
    "01_baseline.proposals.json",
    "02_messy.proposals.json",
    "03_adversarial.proposals.json",
])
def test_data_quality_lists_rules_that_never_fired(name, tmp_path):
    """A rule that only appears in the output when it triggers cannot be used to
    demonstrate that a clean batch was actually checked."""
    _, out = verify_run(tmp_path, name)
    quality = json.loads((out / "data_quality.json").read_text(encoding="utf-8"))
    rule_ids = {rule["rule_id"] for rule in quality["rules"]}

    required = {
        "input.byte_budget", "input.nesting_depth", "input.node_count",
        "input.string_length", "input.array_length", "input.duplicate_keys",
        "input.non_finite_numbers", "input.timezone_naive_timestamps",
    }
    assert required <= rule_ids
    assert any(rule["n_triggered"] == 0 for rule in quality["rules"]), (
        "at least one rule should report zero fires on these fixtures, proving "
        "the report is not filtered to only what triggered")


def test_naive_timestamp_is_detected(tmp_path):
    """02_messy carries `created_at: 2026-03-02T11:40:00` with no offset. A rule
    that reports zero fires on that input is a false clean bill of health."""
    _, out = verify_run(tmp_path, "02_messy.proposals.json")
    quality = json.loads((out / "data_quality.json").read_text(encoding="utf-8"))
    rule = next(r for r in quality["rules"]
                if r["rule_id"] == "input.timezone_naive_timestamps")
    assert rule["n_triggered"] >= 1


def test_duplicate_proposal_id_is_quarantined_not_merged(tmp_path):
    _, out = verify_run(tmp_path, "02_messy.proposals.json")
    quality = json.loads((out / "data_quality.json").read_text(encoding="utf-8"))
    codes = {row["violation_code"] for row in quality["quarantined"]}
    assert "FMT.ID.DUPLICATE_PROPOSAL_ID" in codes

    verdicts = [json.loads(line) for line in
                (out / "verdicts.jsonl").read_text(encoding="utf-8").splitlines() if line]
    ids = [v["proposal_id"] for v in verdicts]
    assert len(ids) == len(set(ids)), "a duplicate id must not produce two verdicts"


# --------------------------------------------------------------------------- #
# determinism
# --------------------------------------------------------------------------- #

def _graded_digest(out):
    import hashlib
    digests = {}
    for name in GRADED:
        path = out / name
        if path.exists():
            digests[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    figures = out / "figures"
    if figures.is_dir():
        for companion in sorted(figures.rglob("*.json")):
            digests[str(companion.relative_to(out))] = hashlib.sha256(
                companion.read_bytes()).hexdigest()
    return digests


def test_two_identical_runs_are_byte_identical(tmp_path):
    """Runs the thing twice and diffs. This is the test CONTRACT.md §6 asks for."""
    _, a = verify_run(tmp_path, "02_messy.proposals.json")
    out_b = tmp_path / "second"
    run_cli(["run", "--input", fixture("02_messy.proposals.json"),
             "--out", str(out_b), "--offline", "--seed", "12345",
             "--now", "2026-01-01T00:00:00Z"])
    assert _graded_digest(a) == _graded_digest(out_b)


def test_max_workers_does_not_change_output(tmp_path):
    """The guarantee must hold at --max-workers 1 and at 8."""
    _, a = verify_run(tmp_path, "02_messy.proposals.json", extra=["--max-workers", "1"])
    # verify_run derives its directory from the fixture name, so the second run
    # is issued explicitly to keep the two output directories apart.
    out_b = tmp_path / "workers8"
    run_cli(["run", "--input", fixture("02_messy.proposals.json"), "--out", str(out_b),
             "--offline", "--seed", "12345", "--now", "2026-01-01T00:00:00Z",
             "--max-workers", "8"])
    assert _graded_digest(a) == _graded_digest(out_b)


def test_seed_does_not_change_any_conclusion(tmp_path):
    """--seed fixes arbitrary choices only. It must not move a status, a novelty
    tier, a feasibility verdict, or any violation code raised."""
    def conclusions(out):
        rows = [json.loads(line) for line in
                (out / "verdicts.jsonl").read_text(encoding="utf-8").splitlines() if line]
        return {
            r["proposal_id"]: (
                r["verdict"]["status"],
                (r.get("novelty") or {}).get("tier"),
                (r.get("feasibility") or {}).get("verdict"),
                tuple(sorted(v["code"] for v in r.get("violations", []))),
            ) for r in rows
        }

    _, a = verify_run(tmp_path, "02_messy.proposals.json")
    out_b = tmp_path / "otherseed"
    run_cli(["run", "--input", fixture("02_messy.proposals.json"), "--out", str(out_b),
             "--offline", "--seed", "98765", "--now", "2026-01-01T00:00:00Z"])
    assert conclusions(a) == conclusions(out_b)


def test_output_directory_is_overwritten_never_merged(tmp_path):
    """A merged output directory is not reproducible."""
    _, out = verify_run(tmp_path, "01_baseline.proposals.json")
    stale = out / "stale_artifact.json"
    stale.write_text("{}", encoding="utf-8")
    run_cli(["run", "--input", fixture("01_baseline.proposals.json"), "--out", str(out),
             "--offline", "--seed", "12345", "--now", "2026-01-01T00:00:00Z"])
    assert not stale.exists(), "a stale file from a previous run must not survive"


# --------------------------------------------------------------------------- #
# CLI contract
# --------------------------------------------------------------------------- #

def test_version_prints_exactly_one_line():
    result = run_cli(["--version"])
    assert result.returncode == 0
    assert len([ln for ln in result.stdout.strip().splitlines() if ln.strip()]) == 1


def test_help_exits_zero_without_other_arguments():
    for args in (["--help"], ["run", "--help"], ["verify", "--help"],
                 ["schema", "--help"], ["cache", "warm", "--help"]):
        assert run_cli(args).returncode == 0, args


@pytest.mark.parametrize("args", [
    ["run", "--nonexistent-flag"],
    ["run"],
    ["run", "--input", "x"],
    ["schema", "--emit", "not_a_schema"],
])
def test_usage_errors_exit_two(args):
    assert run_cli(args).returncode == 2


def test_schema_emit_matches_the_schema_the_code_validates_against():
    """A schema that lives in a docs folder and diverges from the code is worse
    than no schema, so the emitted bytes must be the ones actually loaded."""
    for name in ("proposals", "verdict", "violation", "run_manifest",
                 "data_quality", "feedback_bundle"):
        result = run_cli(["schema", "--emit", name])
        assert result.returncode == 0
        emitted = json.loads(result.stdout)
        on_disk = json.loads(
            (open(os.path.join(SRC, "crucible", "schemas", f"{name}.schema.json"),
                  encoding="utf-8")).read())
        assert emitted == on_disk


def test_cache_warm_reports_nothing_to_do_and_exits_zero(tmp_path):
    """An implementation that ships a committed corpus has nothing to warm. The
    command must still exist, say why, and exit 0."""
    result = run_cli(["cache", "warm", "--input", fixture("01_baseline.proposals.json")])
    assert result.returncode == 0
    assert "nothing to warm" in result.stdout.lower()


def test_verify_passes_on_a_fresh_run_and_fails_on_a_tampered_one(tmp_path):
    _, out = verify_run(tmp_path, "01_baseline.proposals.json")
    assert run_cli(["verify", "--out", str(out)]).returncode == 0

    # Tamper with one reward so it no longer matches its own components.
    path = out / "verdicts.jsonl"
    rows = [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line]
    rows[0]["verdict"]["reward"] = 0.987654
    path.write_text("".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n"
                            for r in rows), encoding="utf-8")
    assert run_cli(["verify", "--out", str(out)]).returncode == 1, (
        "verify must notice a reward that disagrees with its own components")


# --------------------------------------------------------------------------- #
# artifact-shape invariants
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name", [
    "01_baseline.proposals.json",
    "02_messy.proposals.json",
    "03_adversarial.proposals.json",
])
def test_jsonl_formatting_rules(name, tmp_path):
    _, out = verify_run(tmp_path, name)
    for filename in ("verdicts.jsonl", "violations.jsonl"):
        raw = (out / filename).read_bytes()
        if not raw:
            continue
        assert raw.endswith(b"\n")
        assert b"\n\n" not in raw, f"{filename} contains a blank line"
        text = raw.decode("ascii")  # non-ASCII must be escaped
        for line in text.split("\n")[:-1]:
            assert line == line.strip(), "no leading or trailing whitespace per line"


@pytest.mark.parametrize("name", [
    "01_baseline.proposals.json",
    "02_messy.proposals.json",
    "03_adversarial.proposals.json",
])
def test_report_has_no_network_reference(name, tmp_path):
    """report.html must render with no network access whatsoever. A checker
    cannot tell a fetched asset from a hyperlink, so the rule is the strict one."""
    _, out = verify_run(tmp_path, name)
    text = (out / "report.html").read_text(encoding="utf-8")
    assert "http://" not in text and "https://" not in text
    assert "<script src" not in text.lower()


def test_every_graded_figure_has_a_json_companion(tmp_path):
    _, out = verify_run(tmp_path, "01_baseline.proposals.json")
    verdicts = [json.loads(line) for line in
                (out / "verdicts.jsonl").read_text(encoding="utf-8").splitlines() if line]
    attachments = [a for v in verdicts for a in v.get("artifacts", [])]
    assert attachments, "at least one figure must be attached to a verdict"
    for attachment in attachments:
        assert "data_companion" in attachment
        assert (out / attachment["data_companion"]).exists()
        assert (out / attachment["path"]).exists()


def test_figures_include_a_thermodynamic_and_a_route_figure(tmp_path):
    _, out = verify_run(tmp_path, "01_baseline.proposals.json")
    kinds = {a["kind"] for line in
             (out / "verdicts.jsonl").read_text(encoding="utf-8").splitlines() if line
             for a in json.loads(line).get("artifacts", [])}
    assert "phase_diagram" in kinds
    assert "reaction_network" in kinds


def test_unverifiable_records_are_excluded_from_training_projections(tmp_path):
    _, out = verify_run(tmp_path, "02_messy.proposals.json")
    verdicts = {json.loads(line)["record_id"]: json.loads(line)
                for line in (out / "verdicts.jsonl").read_text(encoding="utf-8").splitlines()
                if line}
    index = json.loads((out / "feedback" / "index.json").read_text(encoding="utf-8"))
    scalar = next(p for p in index["projections"] if p["name"] == "scalar_reward")
    assert "unverifiable" in scalar["eligibility"]["excluded_statuses"]

    rows = [json.loads(line) for line in
            (out / scalar["path"]).read_text(encoding="utf-8").splitlines() if line]
    for row in rows:
        assert verdicts[row["record_id"]]["verdict"]["status"] != "unverifiable"


def test_preference_pairs_projection_exists_even_when_empty(tmp_path):
    """An empty projection is an honest one; a missing file is not."""
    _, out = verify_run(tmp_path, "01_baseline.proposals.json")
    index = json.loads((out / "feedback" / "index.json").read_text(encoding="utf-8"))
    names = {p["name"] for p in index["projections"]}
    assert "preference_pairs" in names and "scalar_reward" in names
    pairs = next(p for p in index["projections"] if p["name"] == "preference_pairs")
    assert (out / pairs["path"]).exists()
    assert "record_id" in pairs["row_spec"]


def test_caveats_are_never_empty(tmp_path):
    _, out = verify_run(tmp_path, "01_baseline.proposals.json")
    index = json.loads((out / "feedback" / "index.json").read_text(encoding="utf-8"))
    assert index["caveats"]
    for caveat in index["caveats"]:
        assert len(caveat) >= 41, "a caveat must name a condition and a consequence"


def test_recommendations_always_cite_their_evidence(tmp_path):
    """A recommendation with no supporting count is an opinion and does not
    belong in a machine-generated artifact."""
    _, out = verify_run(tmp_path, "02_messy.proposals.json")
    index = json.loads((out / "feedback" / "index.json").read_text(encoding="utf-8"))
    assert index["recommendations"]
    for rec in index["recommendations"]:
        assert rec["evidence"]["n_affected"] >= 1
        assert rec["evidence"]["codes"]


# --------------------------------------------------------------------------- #
# offline / online distinction (CONTRACT.md §7)
# --------------------------------------------------------------------------- #

def test_offline_is_fail_closed_at_the_chokepoint():
    """--offline means not attempted, not attempted-and-caught. The guard must
    raise before a socket is created rather than catching a failure after."""
    from crucible import net

    net.set_offline(True)
    try:
        with pytest.raises(net.OfflineViolation):
            net.reachable(["example.invalid:443"], 1.0, 1.0)
    finally:
        net.set_offline(False)


def test_online_mode_records_a_reachability_verdict():
    """Whether or not a network exists here, the probe must return a decision
    rather than raise -- an unreachable network is a runtime condition the run
    records, not an error that stops it."""
    from crucible import net

    net.set_offline(False)
    result = net.reachable(["127.0.0.1:9"], 0.25, 0.25)
    assert result.reachable in (True, False)
    assert result.detail


def test_an_offline_run_never_probes_the_network(tmp_path):
    """The graded path passes --offline, so it must complete with no socket
    attempted at all."""
    _, out = verify_run(tmp_path, "01_baseline.proposals.json")
    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["config"]["offline"] is True
    reasons = " ".join(p["reason"] for p in manifest["config"]["providers_unavailable"])
    assert "live_literature" not in reasons, (
        "an offline run must not report a live provider as unreachable; not "
        "consulting it is a planned degradation, not a failure")
