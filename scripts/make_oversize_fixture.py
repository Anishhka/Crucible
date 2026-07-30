#!/usr/bin/env python3
"""Generate an oversized proposal batch.

The file is not committed because committing a quarter-gigabyte fixture to a git
repository is antisocial. It is generated on demand instead.

The output is *structurally valid* JSON. That is the point: the only thing wrong
with it is its size, so it can only be rejected by a byte budget enforced before
the document is handed to a parser. A budget checked after parsing has already
lost, because by then the memory has already been allocated.

    python3 scripts/make_oversize_fixture.py --out /tmp/08_oversize.proposals.json --mib 256
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HEADER = {
    "schema_version": "1-0-0",
    "batch_id": "public-oversize-008",
    "generator": {
        "model_id": "matgen-inorganic-preview",
        "prompt_id": "hypothesis.inorganic@2.4.0",
    },
    "objective": (
        "Envelope-level defect: size. Structurally valid, semantically unremarkable, "
        "and far larger than any legitimate batch. Rejecting it must cost approximately "
        "nothing in memory and approximately nothing in time."
    ),
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="destination path")
    ap.add_argument("--mib", type=int, default=256, help="approximate target size in MiB (default: 256)")
    args = ap.parse_args(argv)

    target = args.mib * 1024 * 1024
    if target <= 0:
        print("--mib must be positive", file=sys.stderr)
        return 2

    # A single repeated proposal, emitted streaming so that generating the fixture
    # does not itself require holding it in memory.
    unit = {
        "proposal_id": "OS-000000",
        "composition": {"formula": "TiO2", "elements": {"Ti": 1, "O": 2}},
        "intended_application": "Filler.",
        "rationale": "Padding. " * 64,
    }
    unit_text = json.dumps(unit, separators=(",", ":"))
    approx_per_record = len(unit_text) + 1

    head = json.dumps(HEADER, separators=(",", ":"))[:-1] + ',"proposals":['
    tail = "]}"
    budget = target - len(head) - len(tail)
    n = max(1, budget // approx_per_record)

    tmp = args.out + ".partial"
    written = 0
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(head)
        written += len(head)
        for i in range(n):
            unit["proposal_id"] = f"OS-{i:06d}"
            chunk = ("" if i == 0 else ",") + json.dumps(unit, separators=(",", ":"))
            fh.write(chunk)
            written += len(chunk)
        fh.write(tail)
        written += len(tail)
    os.replace(tmp, args.out)

    size = os.path.getsize(args.out)
    print(f"wrote {args.out}: {size} bytes ({size / 1024 / 1024:.1f} MiB), {n} proposals")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
