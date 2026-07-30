"""
`crucible aggregate --runs DIR... --out DIR` (PROJECT.md §9, Tier 3).

Cross-run aggregation over many batches. This exists because of the sentence
that opens the whole specification:

    "we learn that *this* batch was 40% junk, but we do not learn *which kind*
    of junk, attached to *which field*, at *what rate*, across batches -- which
    is the only form of that information that can actually be used to make the
    model better."

Every other artifact in this repository answers that question for one batch. This
answers it across batches, which is the form the sentence says is the only useful
one.

**It is a pure projection, like everything else.** It reads `verdicts.jsonl`,
`violations.jsonl` and `run_manifest.json` from each run directory and computes
nothing that is not already in them. It never re-verifies, never opens a corpus,
and never touches the input batches.

**Comparability is checked, not assumed.** Runs carrying different taxonomy
versions cannot be compared without an explicit mapping, and runs carrying
different corpus versions produce novelty tiers that mean different things.
Rather than silently pooling them, the report states the incompatibility and
still aggregates what remains comparable -- because refusing to say anything is
as unhelpful as averaging across a change nobody noticed.

**`fingerprint` is what makes new-versus-recurring answerable.** The violation
schema defines it as stable across runs for the same finding on the same input,
and this is the consumer it was designed for: a finding whose fingerprint is new
in the latest run is a regression, one that has disappeared is a fix, and one
that persists is the backlog.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from . import artifacts


@dataclass
class RunSummary:
    run_id: str
    directory: str
    logical_time: str
    status: str
    taxonomy_version: str
    reward_fn_id: str
    source_sha256: str | None
    config_sha256: str | None
    corpus_fingerprint: str
    counts: dict
    mean_reward: float | None
    code_by_field: dict[str, int]
    code_histogram: dict[str, int]
    novelty_histogram: dict[str, int]
    fingerprints: set[str] = field(default_factory=set)
    n_records: int = 0


def _read_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def load_run(directory: str) -> RunSummary:
    manifest_path = os.path.join(directory, "run_manifest.json")
    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"{directory!r} has no run_manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    verdicts = _read_jsonl(os.path.join(directory, "verdicts.jsonl"))
    violations = _read_jsonl(os.path.join(directory, "violations.jsonl"))

    aggregates = manifest.get("aggregates", {})
    reward = aggregates.get("reward") or {}

    # One value standing for the whole evidence basis. Two runs whose corpora
    # differ are not producing comparable novelty tiers, and this is how that is
    # detected without comparing five hashes by eye.
    corpora = sorted(f"{row['id']}:{row['sha256']}"
                     for row in manifest.get("reference_data", []))
    corpus_fingerprint = artifacts.sha256_bytes(
        "|".join(corpora).encode("utf-8"))[:16] if corpora else "none"

    return RunSummary(
        run_id=manifest.get("run_id", "<unknown>"),
        directory=os.path.basename(os.path.abspath(directory)),
        logical_time=(manifest.get("provenance") or {}).get("logical_time", ""),
        status=manifest.get("status", "-"),
        taxonomy_version=manifest.get("taxonomy_version", "-"),
        reward_fn_id=manifest.get("reward_fn_id", "-"),
        source_sha256=(manifest.get("tool_versions") or {}).get("crucible_source_sha256"),
        config_sha256=(manifest.get("config") or {}).get("sha256"),
        corpus_fingerprint=corpus_fingerprint,
        counts=manifest.get("counts", {}),
        mean_reward=reward.get("mean"),
        code_by_field=dict(aggregates.get("code_by_field") or {}),
        code_histogram=dict(aggregates.get("code_histogram") or {}),
        novelty_histogram=dict(aggregates.get("novelty_tier_histogram") or {}),
        fingerprints={row["fingerprint"] for row in violations if "fingerprint" in row},
        n_records=len(verdicts),
    )


def aggregate(directories: list[str]) -> dict:
    """Aggregate N runs into one report payload.

    Runs are ordered by logical time, then by run_id, so the sequence is
    deterministic and does not depend on the order the directories were named on
    the command line.
    """
    runs = sorted((load_run(d) for d in directories),
                  key=lambda r: (r.logical_time, r.run_id))
    if not runs:
        raise ValueError("no run directories supplied")

    total_records = sum(r.n_records for r in runs)

    # --- comparability ------------------------------------------------------
    incompatibilities = []
    taxonomies = sorted({r.taxonomy_version for r in runs})
    if len(taxonomies) > 1:
        incompatibilities.append(
            f"Runs carry {len(taxonomies)} different taxonomy versions "
            f"({', '.join(taxonomies)}). Rows from different taxonomy versions may "
            f"not be compared without an explicit mapping, so code rates below pool "
            f"vocabularies that may not mean the same thing.")
    rewards = sorted({r.reward_fn_id for r in runs})
    if len(rewards) > 1:
        incompatibilities.append(
            f"Runs carry {len(rewards)} different reward functions "
            f"({', '.join(rewards)}). Rewards are not comparable across them; the "
            f"trend below is reported per run and must not be read as one series.")
    corpora = sorted({r.corpus_fingerprint for r in runs})
    if len(corpora) > 1:
        incompatibilities.append(
            f"Runs were evaluated against {len(corpora)} different evidence bases. "
            f"A novelty tier is a statement relative to a corpus at a version, so "
            f"tier rates across this boundary describe the corpora as much as the "
            f"generator.")
    sources = sorted({r.source_sha256 for r in runs if r.source_sha256})
    if len(sources) > 1:
        incompatibilities.append(
            f"Runs were produced by {len(sources)} different versions of the "
            f"verifier source. A change in a rate may be a change in the checker "
            f"rather than in the generator.")

    # Config is the thing that most often differs between "two versions of the
    # pipeline" -- a threshold moved, a tolerance widened. Comparing runs across
    # that boundary is exactly the eval-harness use case, and it is only sound if
    # the report says which side of the boundary each run is on.
    configs = sorted({r.config_sha256 for r in runs if r.config_sha256})
    if len(configs) > 1:
        incompatibilities.append(
            f"Runs used {len(configs)} different resolved configurations. Thresholds "
            f"and tolerances differ between them, so a code whose rate moved may "
            f"reflect the changed setting rather than the batch. This is the "
            f"intended comparison when evaluating two versions of the pipeline on "
            f"the same input -- but it must be read as a statement about the "
            f"verifier, not about the generator.")

    # --- rate per code x field, across runs ---------------------------------
    per_pair_records: dict[str, int] = {}
    per_pair_runs: dict[str, int] = {}
    for run in runs:
        for pair, count in run.code_by_field.items():
            per_pair_records[pair] = per_pair_records.get(pair, 0) + count
            per_pair_runs[pair] = per_pair_runs.get(pair, 0) + 1

    code_by_field = []
    for pair in sorted(per_pair_records, key=lambda p: (-per_pair_records[p], p)):
        code, _, pointer = pair.partition("|")
        code_by_field.append({
            "code": code,
            "json_pointer": pointer,
            "total_findings": per_pair_records[pair],
            "runs_present_in": per_pair_runs[pair],
            "findings_per_100_records": (
                round(100.0 * per_pair_records[pair] / total_records, 3)
                if total_records else None),
        })

    # --- per-code trend, first run to last ----------------------------------
    first, last = runs[0], runs[-1]

    def rate(run: RunSummary, code: str) -> float | None:
        if not run.n_records:
            return None
        return round(100.0 * run.code_histogram.get(code, 0) / run.n_records, 3)

    trend = []
    for code in sorted({c for r in runs for c in r.code_histogram}):
        opening, closing = rate(first, code), rate(last, code)
        movement = None
        if opening is not None and closing is not None:
            movement = round(closing - opening, 3)
        trend.append({
            "code": code,
            "first_run_per_100": opening,
            "last_run_per_100": closing,
            "change_per_100": movement,
            "per_run": [rate(r, code) for r in runs],
        })
    trend.sort(key=lambda t: (t["change_per_100"] is None,
                              -(t["change_per_100"] or 0.0), t["code"]))

    # --- new / resolved / persistent, by fingerprint ------------------------
    seen_before: set[str] = set()
    for run in runs[:-1]:
        seen_before |= run.fingerprints
    new_in_last = sorted(last.fingerprints - seen_before)
    resolved = sorted(seen_before - last.fingerprints)
    persistent = sorted(last.fingerprints & seen_before)

    return {
        "aggregate_schema_version": "1-0-0",
        "n_runs": len(runs),
        "n_records": total_records,
        "generated_from": [r.directory for r in runs],
        "comparability": {
            "compatible": not incompatibilities,
            "warnings": incompatibilities,
            "taxonomy_versions": taxonomies,
            "reward_fn_ids": rewards,
            "evidence_bases": corpora,
            "config_hashes": configs,
        },
        "runs": [{
            "run_id": r.run_id,
            "directory": r.directory,
            "logical_time": r.logical_time,
            "status": r.status,
            "records": r.n_records,
            "proposals_in": r.counts.get("proposals_in"),
            "quarantined": r.counts.get("quarantined"),
            "accepted": r.counts.get("accepted"),
            "rejected": r.counts.get("rejected"),
            "unverifiable": r.counts.get("unverifiable"),
            "mean_reward": r.mean_reward,
            "distinct_findings": len(r.fingerprints),
            "novelty_tiers": r.novelty_histogram,
            "corpus_fingerprint": r.corpus_fingerprint,
            "source_sha256": r.source_sha256,
            "config_sha256": r.config_sha256,
        } for r in runs],
        "code_by_field": code_by_field,
        "code_trend": trend,
        "finding_lifecycle": {
            "new_in_latest": len(new_in_last),
            "resolved_since_earlier": len(resolved),
            "persistent": len(persistent),
            "new_fingerprints": new_in_last[:50],
            "resolved_fingerprints": resolved[:50],
            "note": (
                "Computed on violation fingerprints, which the schema defines as "
                "stable across runs for the same finding on the same input. A "
                "fingerprint that is new in the latest run is a regression only if "
                "the input batches overlap; across disjoint batches it simply means "
                "a defect not previously seen."
            ),
        },
        "caveats": [
            "This aggregate is a pure projection of the verdict and violation "
            "records in the named run directories. Nothing here was re-verified and "
            "no corpus was consulted.",
            "Rates are per 100 verdict records, not per proposal: quarantined "
            "records never reach a verdict and are counted only in the per-run "
            "table.",
            "A falling rate for a code is not by itself evidence the generator "
            "improved. It is equally consistent with a batch that happened to "
            "contain fewer of that defect class, or with a change to the verifier.",
        ],
    }


_CSS = """
body{margin:0;padding:28px 32px 64px;font:14px/1.55 -apple-system,BlinkMacSystemFont,
"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:#1a1a1a;background:#fbfbfa;max-width:1080px}
h1{font-size:21px;margin:0 0 4px}
h2{font-size:15px;margin:30px 0 10px;padding-bottom:5px;border-bottom:1px solid #e2e2e2}
.sub{color:#6b6b6b;font-size:12px;margin:0 0 18px}
table{border-collapse:collapse;width:100%;font-size:12.5px;margin:8px 0}
th,td{text-align:left;padding:6px 9px;border-bottom:1px solid #ececec;vertical-align:top}
th{font-weight:600;color:#444;background:#f4f4f2;white-space:nowrap}
td.num{text-align:right;font-variant-numeric:tabular-nums}
code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px}
.warn{background:#fff8e6;border-left:3px solid #b8860b;padding:9px 13px;margin:10px 0;font-size:12.5px}
.caveat{background:#f4f6f8;border-left:3px solid #2f6f9f;padding:9px 13px;margin:8px 0;font-size:12.5px}
.up{color:#a33;font-weight:600}
.down{color:#3a7d44;font-weight:600}
"""


def render_html(payload: dict) -> str:
    import html

    def esc(value):
        return html.escape("" if value is None else str(value), quote=True)

    out = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>Crucible cross-run aggregate</title>",
        f"<style>{_CSS}</style></head><body>",
        "<h1>Cross-run aggregate</h1>",
        f"<p class=\"sub\">{payload['n_runs']} runs &#183; "
        f"{payload['n_records']} verdict records</p>",
    ]

    if not payload["comparability"]["compatible"]:
        out.append("<h2>Before you read this</h2>")
        for warning in payload["comparability"]["warnings"]:
            out.append(f'<div class="warn">{esc(warning)}</div>')

    out.append("<h2>Runs</h2>")
    out.append("<table><thead><tr><th>Run</th><th>Logical time</th><th>Status</th>"
               "<th class=\"num\">In</th><th class=\"num\">Quar.</th>"
               "<th class=\"num\">Records</th><th class=\"num\">Mean reward</th>"
               "<th class=\"num\">Distinct findings</th></tr></thead><tbody>")
    for run in payload["runs"]:
        reward = "-" if run["mean_reward"] is None else f"{run['mean_reward']:.3f}"
        out.append(
            f"<tr><td><code>{esc(run['directory'])}</code></td>"
            f"<td>{esc(run['logical_time'])}</td><td>{esc(run['status'])}</td>"
            f"<td class=\"num\">{esc(run['proposals_in'])}</td>"
            f"<td class=\"num\">{esc(run['quarantined'])}</td>"
            f"<td class=\"num\">{esc(run['records'])}</td>"
            f"<td class=\"num\">{reward}</td>"
            f"<td class=\"num\">{esc(run['distinct_findings'])}</td></tr>")
    out.append("</tbody></table>")

    out.append("<h2>What the generator gets wrong, across every batch</h2>")
    out.append("<table><thead><tr><th>Code</th><th>Field</th>"
               "<th class=\"num\">Findings</th><th class=\"num\">Runs</th>"
               "<th class=\"num\">Per 100 records</th></tr></thead><tbody>")
    for row in payload["code_by_field"][:30]:
        out.append(
            f"<tr><td><code>{esc(row['code'])}</code></td>"
            f"<td><code>{esc(row['json_pointer'])}</code></td>"
            f"<td class=\"num\">{row['total_findings']}</td>"
            f"<td class=\"num\">{row['runs_present_in']}</td>"
            f"<td class=\"num\">{esc(row['findings_per_100_records'])}</td></tr>")
    out.append("</tbody></table>")

    if payload["n_runs"] > 1:
        out.append("<h2>Movement, first run to last</h2>")
        out.append("<table><thead><tr><th>Code</th><th class=\"num\">First</th>"
                   "<th class=\"num\">Last</th><th class=\"num\">Change</th>"
                   "</tr></thead><tbody>")
        for row in payload["code_trend"][:30]:
            change = row["change_per_100"]
            cls = "up" if (change or 0) > 0 else "down" if (change or 0) < 0 else ""
            arrow = "" if not change else ("&#9650;" if change > 0 else "&#9660;")
            out.append(
                f"<tr><td><code>{esc(row['code'])}</code></td>"
                f"<td class=\"num\">{esc(row['first_run_per_100'])}</td>"
                f"<td class=\"num\">{esc(row['last_run_per_100'])}</td>"
                f"<td class=\"num {cls}\">{arrow} {esc(change)}</td></tr>")
        out.append("</tbody></table>")

        lifecycle = payload["finding_lifecycle"]
        out.append("<h2>Finding lifecycle</h2>")
        out.append(
            f"<p>{lifecycle['new_in_latest']} new in the latest run &#183; "
            f"{lifecycle['resolved_since_earlier']} no longer present &#183; "
            f"{lifecycle['persistent']} persistent.</p>")
        out.append(f'<div class="caveat">{esc(lifecycle["note"])}</div>')

    out.append("<h2>What this cannot tell you</h2>")
    for caveat in payload["caveats"]:
        out.append(f'<div class="caveat">{esc(caveat)}</div>')

    out.append("</body></html>")
    return "".join(out) + "\n"
