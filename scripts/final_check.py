#!/usr/bin/env python3
"""Ad-hoc reviewer's sweep over a set of output directories.

Not part of the contract and not a replacement for scripts/selfcheck.sh -- this
checks the artifact-level rules that can be verified without Docker, so that a
machine with no daemon can still catch most of what review will catch.
"""

from __future__ import annotations

import json
import pathlib
import sys

GRADED = ["verdicts.jsonl", "violations.jsonl", "data_quality.json", "run_manifest.json"]
HOST_MARKERS = ["/data/in/", "/data/out/", "/Users/", "/home/", "/root/", "C:" + "\\"]


def main(base_dir: str) -> int:
    base = pathlib.Path(base_dir)
    problems = 0

    for run_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        for name in GRADED:
            path = run_dir / name
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if name == "run_manifest.json":
                payload = json.loads(text)
                payload.get("provenance", {}).pop("environment", None)
                text = json.dumps(payload)
            for marker in HOST_MARKERS:
                if marker in text:
                    print(f"  LEAK {run_dir.name}/{name}: {marker!r}")
                    problems += 1

        report = run_dir / "report.html"
        if report.exists():
            html = report.read_text(encoding="utf-8")
            for scheme in ("http://", "https://"):
                if scheme in html:
                    print(f"  URL  {run_dir.name}/report.html contains {scheme!r}")
                    problems += 1

        verdicts = run_dir / "verdicts.jsonl"
        if verdicts.exists():
            raw = verdicts.read_bytes()
            if not raw.isascii():
                print(f"  NON-ASCII {run_dir.name}/verdicts.jsonl is not escaped")
                problems += 1
            if raw and not raw.endswith(b"\n"):
                print(f"  NEWLINE {run_dir.name}/verdicts.jsonl missing trailing newline")
                problems += 1
            if b"\n\n" in raw:
                print(f"  BLANK {run_dir.name}/verdicts.jsonl contains a blank line")
                problems += 1

    print(f"\nartifact-level problems: {problems}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
