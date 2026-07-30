# Crucible

A verification and feedback service for machine-generated materials hypotheses.

Reads a batch of candidate inorganic materials emitted by a language model and
produces, for each one, a defensible verdict — plus a structured signal about
what is *systematically* wrong with the generator that produced the batch. That
second output is the product; the per-proposal verdicts exist to make it.

Deterministic, fully offline, no language model anywhere in the pipeline.

## Build and run

```
docker build -t crucible:v1.0.0 .

docker run --rm \
  --user 10001:10001 --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev,size=512m \
  --cap-drop ALL --security-opt no-new-privileges=true \
  --network none --cpus 2 --memory 4g --memory-swap 4g --pids-limit 512 \
  -v "$PWD/in:/data/in:ro" -v "$PWD/out:/data/out:rw" \
  crucible:v1.0.0 run --input /data/in/batch.json --out /data/out \
  --offline --seed 12345 --now 2026-01-01T00:00:00Z
```

Without Docker, for iteration:

```
pip install -e .
python -m crucible run --input <spec>/fixtures/public/01_baseline.proposals.json --out /tmp/out --offline
python -m crucible verify --out /tmp/out
```

## Commands

| Command | What it does |
|---|---|
| `crucible run --input <PATH> --out <DIR>` | Verify a batch. Accepts `--config --seed --offline --now --timeout --max-workers --provider --verbose --cache-dir`. |
| `crucible verify --out <DIR>` | Re-derive every invariant from an output directory alone. Does not re-run the pipeline. |
| `crucible schema --emit <NAME>` | Print the JSON Schema the implementation actually validates against. |
| `crucible lineage --out <DIR> [--input <PATH>]` | Reconstruct an output back to the code, config, inputs and corpora that produced it, and report `MATCH` / `DIVERGED` / `UNKNOWN` per component. Exit 1 if anything diverged. |
| `crucible aggregate --runs <DIR>... --out <DIR>` | Aggregate many runs into one cross-batch report: `code × field` rates across batches, movement between first and last run, and new/resolved/persistent findings by fingerprint. |
| `crucible cache warm --input <PATH>` | Reports that the corpora are committed and there is nothing to fetch. Exits 0. |
| `crucible --version` | One line. |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Every proposal reached a terminal verdict; nothing was unavailable. |
| `1` | Unexpected internal error (the only code that prints a stack trace). |
| `2` | Usage or configuration error. Nothing was processed. |
| `3` | The input was rejected at the envelope level. No verdict was possible. |
| `4` | Degraded success: complete, valid artifacts, but something was quarantined or unavailable. |

**Exit 4 is the ordinary healthy outcome, not a failure.** The literature
provider is a replay cassette, so any composition it never recorded produces an
`unavailable` lookup — and a run containing one must not report `0`. A
submission that returned `0` on every batch would be more suspicious than one
that returns `4`.

## Output

```
run_manifest.json     run identity, corpus versions, counts, aggregates, hashes
verdicts.jsonl        the canonical record: one per proposal
violations.jsonl      one row per (record, finding), for group-by across runs
data_quality.json     what was refused and why, including rules that never fired
feedback/index.json   projections, evidence-backed recommendations, caveats
report.html           self-contained; renders with no network at all
figures/              SVG + the JSON companion that is the graded artifact
```

Every artifact besides `verdicts.jsonl` is a pure projection of it — regenerable
from the verdict records, containing no fact that is not in them.

## What it checks

1. **Is this already known?** Four corpora across three evidence classes, behind
   one provider interface, with per-source formula conventions and a five-valued
   tier. `no_evidence` and `unknown` are never collapsed.
2. **Is it internally coherent?** Charge balance under common oxidation states,
   electronegativity ordering, formula-vs-elements-map agreement, periodic
   minimum interatomic distance, lattice and density plausibility, site
   occupancies against the declared composition, asserted symmetry against the
   lattice metric.
3. **Could it plausibly be made?** A convex hull over the known phases of the
   chemical system, a stated and justified stability threshold, and an
   open-system screen against the oxygen chemical potential the proposed
   atmosphere imposes — with the full assumption stack, including the chemical
   potentials used, attached to every number.
4. **How would you make it?** Balanced reactions from obtainable precursors,
   ranked by computed driving force, and an assessment — not an echo — of the
   generator's own synthesis hint.
5. **What is systematically wrong with the generator?** `code × field`
   aggregates, a decomposed reward, calibration pairs, minimal repairs that are
   **actually re-run through the same checks** before any is promoted to a
   preference pair, and prioritised recommendations that each cite the counts
   that motivated them.

## Read these next

- **`ASSUMPTIONS.md`** — every decision `PROJECT.md` §7 left open, what was
  chosen, and why. One heading per decision.
- **`LIMITATIONS.md`** — what this gets wrong and where not to trust it. Start
  with "thermodynamic coverage is ~146 compounds", which is the largest gap.
- **`DECISIONS.md`** — the engineering narrative, including what was cut.
- **`AI_USAGE.md`** — how assistants were used, and where they were wrong.

## Across batches

One batch tells you this batch was 40% junk. `PROJECT.md` §1 is explicit that
this is the useless form of the information — what you need is *which kind* of
junk, on *which field*, at *what rate*, **across** batches.

```
crucible aggregate --runs out/2026-01 out/2026-02 out/2026-03 --out trend/
```

That produces the `code × field` table pooled over every run, the movement in
each code's rate between the first run and the last, and — using the
`fingerprint` the violation schema defines for exactly this — which findings are
new, which have disappeared, and which are the standing backlog.

It refuses to pool quietly: runs carrying different taxonomy versions, reward
functions, corpus versions or verifier source hashes are still aggregated, but
the report leads with what makes them incomparable, because a rate that moved
because the *checker* changed is not a fact about the generator.

```
crucible lineage --out out/2026-03 --input batches/2026-03.json
```

answers the question that makes any of that trustworthy: is the code in front of
me the code that produced this output, against these corpora, under this
configuration? Every component reports `MATCH`, `DIVERGED` or `UNKNOWN` — and
`UNKNOWN` is never folded into either, because a corpus that cannot be located is
not a corpus that changed.

## Honest summary

Tier 0, Tier 1 and Tier 2 are implemented, plus two Tier 3 items: full lineage
reconstruction and cross-run aggregation.

**Thermodynamics are real but narrowly covered.** A convex hull is built from a
hand-entered table of 146 standard formation enthalpies. Where a composition is
in that table, the record carries a genuine formation energy, hull distance,
decomposition and open-system atmosphere screen. Where it is not — which is the
case for most genuinely *novel* proposals, the interesting ones — the energy
fields are `null` and the record says so, reporting instead the competing phases
at that composition and what they are worth. No energy is ever estimated to fill
a field.

The machinery independently reproduces the wüstite disproportionation and the
Ellingham reduction ordering; both are asserted by tests.

**Prior-art coverage is ~270 hand-curated compositions** — enough to recognise
well-known compounds, nowhere near enough to support a novelty claim.

**The container is built and verified.** `scripts/selfcheck.sh` reports
**83 passed, 0 failed, 0 skipped** against the image. It is 77 MiB against a
2 GiB ceiling, runs a 15-proposal batch in 4 s against a 600 s budget, and
produces byte-identical artifacts at 1 core / 1 GiB as it does at 2 cores /
4 GiB.

`LIMITATIONS.md` states all of the above in full, including what the container
verification did *not* cover: a fresh `git clone` on a machine that has never
seen this project.

## Testing

```
CRUCIBLE_FIXTURES=<spec>/fixtures/public pytest tests/ -q
```

230 tests: named positive and negative controls, metamorphic invariants
(reduction idempotence, coefficient scaling, spelling equivalence), adversarial
coverage parametrised across variants rather than asserting the one example in a
fixture, thermodynamics checked against results known independently of this code
(the wüstite disproportionation, the Ellingham reduction ordering), and a
determinism test that runs the pipeline repeatedly and diffs the bytes.

The container is checked separately by the specification's own conformance
script, which is what the CI workflow runs on every push:

```
<path-to-spec-packet>/scripts/selfcheck.sh --image crucible:v1.0.0 --skip-build
```
