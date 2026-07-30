# AI usage

Undisclosed use is disqualifying; disclosed use is not penalised in any way.
This file is written to be specific enough to be checkable.


---

## Assistants used

This repository had two distinct assistant phases, by two different tools. The
split matters for reading everything below, because the defects each one
produced are recorded separately.

| Phase | Tool | Scope |
|---|---|---|
| 1. Scaffold | **OpenAI Codex** | The original implementation: CLI surface, envelope hardening, a first-pass formula normaliser, artifact writers, the run pipeline, a partial `verify`, the starter compound corpus, and the first `Dockerfile` and conda environment. This is the state described under "What the audit found in the scaffold" below. |
| 2. Audit and rebuild | **Claude (Anthropic)** | An audit of phase 1 against `CONTRACT.md` and `PROJECT.md`, followed by a rebuild of most of the implementation. Everything in the "Per component" table below is from this phase unless stated otherwise. |

## Per component

Phase 2 replaced or substantially rewrote nearly every module, so "Codex" below
means the code that was *superseded*, not code still shipping.

| Component | How it was produced |
|---|---|
| `cli.py`, `pipeline.py` | Codex draft, rewritten in phase 2. Exit-code logic and the config/`run_id` relationship were reworked in response to a determinism test failure (case 1 below). |
| `envelope.py` | Codex draft, substantially rewritten: depth scanning moved ahead of the parser, codes remapped onto the core taxonomy. |
| `normalize.py` | Rewritten from scratch in phase 2 after the audit found the reduction bug. |
| `chemdata.py` | Codex produced a ~65-element subset; phase 2 replaced it with the full 118-element tables. Spot-checked, not exhaustively verified — see below. |
| `composition.py` | Codex original (including the MgB₂ bonding-class idea, which was sound and was kept); generalised and made config-driven in phase 2. |
| `structure.py`, `claims.py`, `routes.py`, `providers.py`, `report.py`, `reward.py`, `recordcheck.py`, `config.py` | New in phase 2. No phase-1 equivalent existed. |
| `feasibility.py` | Codex original was an electronegativity heuristic; phase 2 kept the screen, added the assumption stack and the frame-applicability check, and removed the claim to a threshold. |
| `verify.py`, `artifacts.py` | Codex draft, rewritten in phase 2 to cover the full invariant list. |
| `reference_data/` | Codex produced a 110-entry single corpus; phase 2 replaced it with four corpora across three evidence classes. Contents reviewed for chemical accuracy. |
| Tests | Codex produced a small starter suite; phase 2 rewrote it around invariants and adversarial parametrisation. Several phase-2 tests were themselves wrong and were corrected (case 5 below). |
| `ASSUMPTIONS.md`, `LIMITATIONS.md`, `DECISIONS.md`, `README.md` | Codex left stubs; phase 2 wrote them from the decisions actually encoded in the code. |
| `Dockerfile`, `environment.yml`, lock file | Codex draft was tag-pinned and single-base; phase 2 rewrote it multi-stage with both `FROM`s digest-pinned from the live registry. Built, run under the graded invocation, and verified with `scripts/selfcheck.sh` (83/83). |

> **To be completed by the repository owner:** anything in the above that was
> written or materially revised by hand, and any phase-1 decision that was yours
> rather than the tool's. The attributions above are conservative — they credit
> the assistants with everything they produced — so correcting them can only
> improve the accuracy of this file.

---

## Where the assistant was wrong, and how it was caught

Eight concrete cases, all from **phase 2 (Claude)**. Every one was caught by
*running* something — the program, the tests, the build, or the conformance
script — and not one by reading the code, which is the point. Phase 1's defects
are listed separately in the section after this one.

### 1. `--max-workers` silently broke the byte-identity guarantee

The assistant folded runtime knobs into the resolved configuration, which is a
reasonable-looking thing to do: `--max-workers` *is* configuration. But
`config.sha256` feeds `run_id`, `run_id` feeds `record_id`, and `record_id`
appears in every graded artifact including the figure companions. So
`--max-workers 1` and `--max-workers 8` produced different bytes while executing
an identical code path — a direct breach of CONTRACT.md §6.

**Caught by** the determinism test that runs the pipeline at both settings and
diffs the graded digests. Reading the diff was what exposed the chain, since the
first visible symptom was a figure companion hash, which looks like a figure
problem and is not.

**Fixed by** moving `max_workers` and `timeout` into `provenance.environment`,
the one subtree exempt from byte-identity.

### 2. Recorded-but-empty cassette answers were treated as hits

The literature cassette records a query for `Fe17O23` with zero documents —
meaning "we asked, and there is nothing". The assistant's provider logic treated
any presence in the cassette as a hit, so the batch's designated **negative
control** came back as `literature_mention` instead of `no_evidence`.

**Caught by** running the named controls directly rather than trusting the
fixture to exercise them. The distinction between "not recorded" (`unavailable`),
"recorded as nothing" (`miss`) and "recorded with documents" (`hit`) is three
states, and the draft collapsed two of them.

### 3. A generated string silently violated the schema it was written for

`aggregates.calibration.method` had a `maxLength` of 256. The assistant wrote a
well-argued 290-character explanation into it. Every run produced a
schema-invalid manifest.

**Caught by** `crucible verify`, which validates artifacts against the schemas —
i.e. by the tool built for exactly this, on its first real use. Worth recording
because it is evidence the self-check earns its place.

### 4. Path sanitisation left `..` in the output

`safe_slug` replaced path separators but only stripped leading dots, so
`../../etc/passwd` became `_.._etc_passwd`. That is *safe* — it is a single path
component with no separators — but a name still containing `..` invites the next
reader to assume the traversal risk was handled when only half of it was.

**Caught by** an adversarial test parametrised across six traversal shapes rather
than asserting the one string in the fixture. The single fixture example passed.

### 5. The assistant's own test encoded wrong chemistry

A test asserted that every member of the metallic/covalent class must return
`applicable=False` from the charge-balance screen, listing SiC among them. SiC
balances cleanly as Si⁴⁺C⁴⁻, and reporting that is correct.

**Caught by** the test failing. The *test* was wrong, not the code. It was
restated around the invariant that actually matters — these materials must never
come back `balanced=False` — which is both true and the property worth
protecting.

### 6. The Dockerfile had never been executed, and did not build

Phase 2 wrote a multi-stage Dockerfile against CONTRACT.md §8 with both base
images digest-pinned, and could not build it: no Docker daemon was available at
the time. It looked correct and was not. `conda env create` does not accept
`--override-channels`/`--channel` — those belong to `conda create` — so the very
first `docker build` failed outright at the environment step.

**Caught by** finally running `docker build`. Nothing short of that would have
found it: the flags are real conda flags, just on the wrong subcommand, so they
read as correct to anyone reviewing the file.

Worth recording because it is the highest-severity class of assistant error in
this repository — Tier 0 is a gate, and an image that does not build caps the
entire submission regardless of what else was written.

### 7. `--offline` was decorative

The pipeline treated online and offline runs identically, because every evidence
provider reads from a committed corpus and none of them opens a socket. That is
defensible right up until CONTRACT.md §7's last line: *"Without `--offline`, and
with the network genuinely unavailable, the program MUST NOT exit 0."* The build
exited 0, because it never noticed it had been asked for something it could not
deliver.

**Caught by** `scripts/selfcheck.sh`, which runs the program without `--offline`
under `--network none` and asserts a non-zero exit. It was the single failure in
an otherwise clean 82-check run.

**Fixed by** adding a single transport chokepoint (`net.py`) that arms on
`--offline` and, in online mode, probes reachability once: unreachable means the
requested live evidence could not be obtained, which is a degraded success, not
a clean one.

### 8. Balanced equations were printed with alphabetised products

Route output read `1 BaO + 1 TiO2 -> 1 BaO3Ti`. Chemically correct, and unusable
to a materials scientist reading it.

**Caught by** printing the routes and looking at them.

---

## What the audit found in the scaffold

These are **phase 1 (Codex)** defects, found by auditing that implementation
against `CONTRACT.md` and by running it over the public fixtures. They are
recorded in full rather than quietly fixed, because the pattern in them is the
useful part: every one produced output that was schema-valid, ran without error,
and was wrong.

The scaffold passed its own 18-test suite and returned the correct exit code on
all seven fixtures while doing all of the following:

- **Reduction alphabetised element symbols**, so `TiO2` "reduced" to `O2Ti`,
  compared unequal to itself, and raised `CHEM.FORMULA.NOT_REDUCED` on formulas
  that were already reduced. This flooded the `code × field` table — the single
  most actionable output — with a defect the generator had not committed.
- **`reference_data` was always empty.** A `reference_data_manifest_entry()`
  function existed and was never called, and its `kind` value was not a member
  of the schema's enum, so it would have failed validation had it been wired in.
- **`verdicts.jsonl` and `violations.jsonl` were unsorted.** They *looked*
  sorted on two of three fixtures by accident of input order.
- **Records with the least data scored highest.** Status was `accepted` only
  when nothing was `unavailable`; since structure and claim checks were
  unimplemented, a proposal supplying *neither* was "fully verified" and scored
  1.0, while a well-formed proposal with a structure did not.
  `../../etc/passwd` and a record with an undeclared field were both accepted at
  reward 1.0.
- **Violation codes were invented** (`CHEM.CHARGE.UNBALANCED`, an entire `ENV.*`
  family) rather than taken from the core taxonomy, and sat in neither the core
  vocabulary nor the reserved `X.*` extension namespace.
- **A declared data-quality rule was never implemented.** The
  timezone-naive-timestamp rule reported zero fires on a fixture that contains a
  naive timestamp — a false clean bill of health, which is the exact failure
  mode the "report rules that never fired" requirement exists to prevent.

**The lesson we take from this list** is the one `PROJECT.md` §10 states
directly: test quality is the clearest signal available when an assistant wrote
much of the implementation, because assistants write tests that restate the
implementation and pass regardless of whether it is correct. The scaffold's
suite asserted that the pipeline produced the exit codes it produced. It had no
test for sort order, none for `reference_data` being populated, none for the
required artifact set, and no named control — so all six defects above were
invisible to it while it reported green.

The phase-2 suite is written the other way round: against invariants that would
survive a rewrite (reduction idempotence, coefficient scaling, spelling
equivalence), against named positive and negative controls whose answers are
known independently of this code, and adversarially across variants rather than
against the one example that happened to appear in a fixture. That is why it
caught cases 1, 4 and 5 above rather than shipping them.

---

## Anything in the repository that cannot be explained line by line

Two areas warrant flagging honestly:

1. **The numeric tables in `chemdata.py`** — 118 atomic masses,
   electronegativities, and curated oxidation-state lists. Generated by Claude
   in phase 2 and spot-checked against known values (H 1.008, Fe 55.845,
   O 3.44 Pauling, and the density of rutile recovered to 4.23 g/cm³ from the
   lattice, which exercises the mass table end to end). They have **not** been
   cross-checked entry by entry against a primary source. An error in a rarely
   used element's mass would surface only as a density warning on a proposal
   containing it.

2. **The corpus contents** in `reference_data/`. Each entry was reviewed for
   chemical plausibility and the identifiers are synthetic (`exp-00001`, not real
   ICSD or Materials Project ids, precisely so they cannot be mistaken for
   database references). Whether every one of the ~270 compositions is
   experimentally reported *as written* has not been individually verified
   against primary literature.

Both are stated in `LIMITATIONS.md` as well, because they bound what the
verdicts mean.
