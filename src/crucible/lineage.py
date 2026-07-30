"""
`crucible lineage --out DIR` (PROJECT.md §9, Tier 3).

Full lineage reconstruction: from an output directory back to the exact code,
config, inputs and corpus versions that produced it -- and, where it can be
checked, whether any of those have moved since.

This is the difference between "the manifest records some versions" and "I can
prove this output came from this code against these corpora". The manifest has
carried the ingredients all along; what was missing was something that reads
them back and *checks* them, which is the part a reviewer can actually use.

Three answers are possible per component, and the distinction matters:

    MATCH      the current environment agrees with what the run recorded
    DIVERGED   it disagrees, and the difference is named
    UNKNOWN    it cannot be checked from here (the input file is not present,
               the run predates a field, no repository is available)

`UNKNOWN` is deliberately not folded into either of the others. A corpus that
cannot be located is not a corpus that changed, and reporting it as one would
make the tool cry wolf; reporting it as a match would make it useless.

Exit code is 0 when nothing DIVERGED, 1 when something did. UNKNOWN alone does
not fail: it is a statement about this machine, not about the run.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from . import artifacts, providers, thermo
from .config import load_config

MATCH = "MATCH"
DIVERGED = "DIVERGED"
UNKNOWN = "UNKNOWN"


@dataclass
class Finding:
    component: str
    status: str
    recorded: str | None
    current: str | None
    detail: str


@dataclass
class LineageReport:
    run_id: str
    findings: list[Finding] = field(default_factory=list)
    facts: list[tuple[str, str]] = field(default_factory=list)

    @property
    def diverged(self) -> list[Finding]:
        return [f for f in self.findings if f.status == DIVERGED]

    @property
    def unknown(self) -> list[Finding]:
        return [f for f in self.findings if f.status == UNKNOWN]

    @property
    def ok(self) -> bool:
        return not self.diverged


def _diff(recorded, current, ignore: set[tuple[str, ...]] | None = None,
          path: tuple[str, ...] = ()) -> list[str]:
    """Structural difference between two resolved configurations.

    Returns dotted paths with both values, so the caller can say what moved
    rather than that something did. Paths in `ignore` are skipped along with
    everything under them.
    """
    ignore = ignore or set()
    if path in ignore:
        return []

    if isinstance(recorded, dict) and isinstance(current, dict):
        out: list[str] = []
        for key in sorted(set(recorded) | set(current)):
            child = path + (key,)
            if child in ignore:
                continue
            if key not in recorded:
                out.append(f"{'.'.join(child)} added ({current[key]!r})")
            elif key not in current:
                out.append(f"{'.'.join(child)} removed (was {recorded[key]!r})")
            else:
                out.extend(_diff(recorded[key], current[key], ignore, child))
        return out

    if recorded != current:
        return [f"{'.'.join(path) or '<root>'}: {recorded!r} -> {current!r}"]
    return []


def _short(value: str | None, width: int = 16) -> str:
    if value is None:
        return "-"
    return value if len(value) <= width else value[:width] + "..."


def reconstruct(out_dir: str, input_path: str | None = None) -> LineageReport:
    manifest_path = os.path.join(out_dir, "run_manifest.json")
    if not os.path.exists(manifest_path):
        raise FileNotFoundError(
            f"no run_manifest.json in {out_dir!r}; lineage is reconstructed from the "
            f"manifest and cannot be recovered without it")

    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    report = LineageReport(run_id=manifest.get("run_id", "<unknown>"))

    # --- the facts, stated before anything is checked ----------------------
    provenance = manifest.get("provenance", {})
    tools = manifest.get("tool_versions", {})
    report.facts = [
        ("run_id", manifest.get("run_id", "-")),
        ("logical time", provenance.get("logical_time", "-")),
        ("crucible version", manifest.get("crucible_version", "-")),
        ("taxonomy version", manifest.get("taxonomy_version", "-")),
        ("record schema", manifest.get("record_schema_version", "-")),
        ("reward function", manifest.get("reward_fn_id", "-")),
        ("python", tools.get("python", "-")),
        ("git commit", provenance.get("git_commit") or "not recorded"),
        ("git dirty", str(provenance.get("git_dirty")) if provenance.get("git_dirty") is not None else "not recorded"),
        ("status", manifest.get("status", "-")),
    ]

    # --- code ---------------------------------------------------------------
    recorded_source = tools.get("crucible_source_sha256")
    current_source = artifacts.source_tree_sha256()
    if not recorded_source:
        report.findings.append(Finding(
            "code", UNKNOWN, None, current_source,
            "This run recorded no source hash, so the code that produced it cannot "
            "be identified. Runs from this version onward record one."))
    elif recorded_source == current_source:
        report.findings.append(Finding(
            "code", MATCH, recorded_source, current_source,
            "The package source in front of you hashes to what this run recorded."))
    else:
        report.findings.append(Finding(
            "code", DIVERGED, recorded_source, current_source,
            "The package source has changed since this run. Re-running now would "
            "not necessarily reproduce these artifacts, and any difference in them "
            "is not evidence about the input."))

    recorded_version = manifest.get("crucible_version")
    if recorded_version and recorded_version != artifacts.CRUCIBLE_VERSION:
        report.findings.append(Finding(
            "crucible version", DIVERGED, recorded_version, artifacts.CRUCIBLE_VERSION,
            "The declared version differs, so the two are not expected to agree."))

    # --- taxonomy -----------------------------------------------------------
    recorded_taxonomy = manifest.get("taxonomy_version")
    if recorded_taxonomy and recorded_taxonomy != artifacts.TAXONOMY_VERSION:
        report.findings.append(Finding(
            "taxonomy", DIVERGED, recorded_taxonomy, artifacts.TAXONOMY_VERSION,
            "Rows carrying different taxonomy versions may not be compared without "
            "an explicit mapping."))
    elif recorded_taxonomy:
        report.findings.append(Finding(
            "taxonomy", MATCH, recorded_taxonomy, artifacts.TAXONOMY_VERSION,
            "Findings from this run are directly comparable with current ones."))

    # --- configuration ------------------------------------------------------
    #
    # Compared structurally rather than by hash. Two hashes differing tells you
    # nothing about what differed, and they differ for an uninteresting reason on
    # every run: `evidence.offline` and `evidence.only` are per-invocation flags
    # folded into the resolved mapping, not configuration anybody edited. Diffing
    # the mappings and excluding those says what actually moved.
    recorded_config = (manifest.get("config") or {}).get("sha256")
    recorded_resolved = (manifest.get("config") or {}).get("resolved")
    try:
        current_resolved = load_config(None).redacted
    except (OSError, ValueError):
        current_resolved = None

    if not isinstance(recorded_resolved, dict) or current_resolved is None:
        report.findings.append(Finding(
            "config", UNKNOWN, recorded_config, None,
            "The resolved configuration could not be compared."))
    else:
        differences = _diff(recorded_resolved, current_resolved,
                            ignore={("evidence", "offline"), ("evidence", "only")})
        if not differences:
            report.findings.append(Finding(
                "config", MATCH, recorded_config, None,
                "Every configured threshold, tolerance and weight matches the "
                "current defaults, ignoring the per-invocation offline and provider "
                "flags."))
        else:
            shown = "; ".join(differences[:6])
            report.findings.append(Finding(
                "config", DIVERGED, recorded_config, None,
                f"{len(differences)} setting(s) differ from the current defaults: "
                f"{shown}"
                + ("; ..." if len(differences) > 6 else "")
                + ". Either a --config file was supplied, or the defaults have "
                  "changed since. Any number on those records was computed under "
                  "the recorded values, not these."))

    # --- reference corpora --------------------------------------------------
    current_corpora = {row["id"]: row for row in providers.reference_data(None)}
    thermo_row = thermo.dataset_manifest_entry()
    if thermo_row is not None:
        current_corpora[thermo_row["id"]] = thermo_row

    recorded_corpora = manifest.get("reference_data") or []
    if not recorded_corpora and manifest.get("status") != "failed":
        report.findings.append(Finding(
            "corpora", DIVERGED, None, None,
            "A run that produced verdicts recorded no reference data, so its novelty "
            "and energy claims cannot be tied to any corpus version."))

    for recorded in sorted(recorded_corpora, key=lambda r: r["id"]):
        identifier = recorded["id"]
        current = current_corpora.get(identifier)
        if current is None:
            report.findings.append(Finding(
                f"corpus {identifier}", UNKNOWN, recorded.get("sha256"), None,
                "This corpus is not present in the current build, so it cannot be "
                "compared. It is not evidence that it changed."))
        elif current["sha256"] == recorded.get("sha256"):
            report.findings.append(Finding(
                f"corpus {identifier}", MATCH, recorded.get("sha256"), current["sha256"],
                f"Unchanged: {current.get('n_entries')} entries at "
                f"{current.get('snapshot')}."))
        else:
            report.findings.append(Finding(
                f"corpus {identifier}", DIVERGED, recorded.get("sha256"), current["sha256"],
                "The corpus contents have changed since this run. Every novelty tier "
                "and every hull distance on those records is relative to the OLD "
                "contents and must not be compared with a new run's."))

    # --- inputs -------------------------------------------------------------
    for entry in (manifest.get("input") or {}).get("files", []):
        name = entry.get("name")
        recorded_hash = entry.get("sha256")
        candidate = None
        if input_path:
            candidate = (input_path if os.path.isfile(input_path)
                         else os.path.join(input_path, name))
        if not candidate or not os.path.exists(candidate):
            report.findings.append(Finding(
                f"input {name}", UNKNOWN, recorded_hash, None,
                "The input file was not supplied to this command, so its hash could "
                "not be re-checked. Pass --input to verify it."))
            continue
        actual = artifacts.sha256_file(candidate)
        if actual == recorded_hash:
            report.findings.append(Finding(
                f"input {name}", MATCH, recorded_hash, actual,
                f"{entry.get('bytes')} bytes, batch {entry.get('batch_id')}."))
        else:
            report.findings.append(Finding(
                f"input {name}", DIVERGED, recorded_hash, actual,
                "The file at that path is not the file this run read."))

    # --- artifacts still match their own recorded hashes --------------------
    altered = []
    for entry in manifest.get("outputs", []):
        path = os.path.join(out_dir, entry["path"])
        if not os.path.exists(path):
            altered.append(f"{entry['path']} (missing)")
            continue
        if artifacts.sha256_file(path) != entry["sha256"]:
            altered.append(entry["path"])
    if altered:
        report.findings.append(Finding(
            "artifacts", DIVERGED, "as written", "modified",
            "These artifacts no longer match the hashes the manifest recorded: "
            + ", ".join(altered[:6])
            + ". The output has been altered since the run produced it."))
    elif manifest.get("outputs"):
        report.findings.append(Finding(
            "artifacts", MATCH, "as written", "as written",
            f"All {len(manifest['outputs'])} recorded artifacts hash to what the "
            f"manifest says."))

    return report


def render(report: LineageReport) -> str:
    lines = [f"lineage for {report.run_id}", ""]

    width = max(len(k) for k, _ in report.facts) if report.facts else 0
    for key, value in report.facts:
        lines.append(f"  {key.ljust(width)}  {value}")
    lines.append("")

    component_width = max((len(f.component) for f in report.findings), default=0)
    for finding in report.findings:
        lines.append(f"  [{finding.status:8}] {finding.component.ljust(component_width)}  "
                     f"{finding.detail}")
        if finding.status == DIVERGED and finding.recorded and finding.current:
            lines.append(f"  {'':10} {''.ljust(component_width)}  "
                         f"recorded {_short(finding.recorded)} -> "
                         f"current {_short(finding.current)}")
    lines.append("")

    if report.diverged:
        lines.append(f"DIVERGED: {len(report.diverged)} component(s) have changed since "
                     f"this run. It is not reproducible from the current tree.")
    elif report.unknown:
        lines.append(f"CONSISTENT, with {len(report.unknown)} component(s) that could "
                     f"not be checked from here. Nothing contradicts this run being "
                     f"reproducible.")
    else:
        lines.append("REPRODUCIBLE: every component of the lineage was checked and "
                     "matches. Re-running this input on this tree should reproduce "
                     "these artifacts byte for byte.")

    return "\n".join(lines) + "\n"
