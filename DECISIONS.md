# Decisions

The engineering narrative: what was built, what was cut and why, and what two
more weeks would buy. Scientific judgment calls live in `ASSUMPTIONS.md`.

---

## What was built

A deterministic, offline, pure-Python batch analyser. Nine verification modules
behind one pipeline, four committed evidence corpora behind one provider
interface, and a self-check (`crucible verify`) that re-derives ten classes of
invariant from an output directory alone.

## Dependencies: stdlib plus `jsonschema`, and nothing else

The obvious move in this domain is pymatgen. It was not taken, and the reasoning
matters more than the choice.

What pymatgen would have bought: real symmetry determination via spglib,
structure matching, and composition parsing. What it costs: several hundred
megabytes against a 2 GiB image ceiling, a split between the core distribution
and the shim (the bond-valence and interface-reaction pieces live only in the
full package, so installing the core alone imports successfully and fails
later), and a dependency whose symmetry defaults differ from the ones the large
public corpora use for relaxed structures.

The arithmetic actually needed here — fractional-to-Cartesian conversion, a
periodic minimum-distance scan, cell volume, density, crystal-family
classification from a metric — is roughly 120 lines and is exactly reproducible.
The trade taken is: implement that directly, and be honest in `LIMITATIONS.md`
that full symmetry determination is therefore absent rather than pretend a
metric comparison is a determination.

The same reasoning rules out matplotlib. Figures are hand-written SVG. A
plotting library would build a font cache on first import that takes the better
part of a minute, and its output is not byte-stable across versions — which
would breach the determinism guarantee for no gain, since the JSON companion is
the graded artifact and the image is for humans.

## Serial processing, deliberately

`--max-workers` is accepted, recorded, and ignored. Reasoning in
`ASSUMPTIONS.md` §7.9. The short version: the contract demands byte-identical
output at 1 and 8 workers, the workload is a thousand records of pure-Python
arithmetic, and "concurrency cannot affect the output because there is none" is
a stronger and cheaper guarantee than a sorted merge after a parallel map.

**This produced a real bug, caught by the test suite.** `--max-workers` was
initially folded into the resolved configuration, which moved `config.sha256`,
which moved `run_id`, which moved `record_id`, which changed every graded
artifact — so `--max-workers 1` and `--max-workers 8` produced different bytes
while the executed code path was identical. Execution knobs now live in
`provenance.environment`, the one subtree exempt from byte-identity, which is
precisely what that exemption is for.

## Quarantine on record-level schema failure

A record that violates `proposals.schema.json` is quarantined rather than given
a verdict with a failed gate. Both readings are defensible; this one was chosen
because `EXPECTATIONS.md` describes the adversarial fixture as one where "some
records cannot be admitted", and because a `proposal_id` that violates its
declared pattern should never reach the code that names per-proposal artifacts.

The cost is that those records carry no per-field attribution in
`violations.jsonl`. It is paid back by naming the offending code and pointer in
the `data_quality.quarantined` reason string, so the information is not lost —
only relocated.

`recordcheck.safe_slug` is a second, independent barrier: even a hostile
identifier that somehow passed validation cannot produce a path with a
separator or a `..` in it.

## Reduction is by GCD only

Inherited code alphabetised element symbols as part of "reduction", so `TiO2`
reduced to `O2Ti`, compared unequal to itself, and raised
`CHEM.FORMULA.NOT_REDUCED` on a formula that was already reduced. Every formula
whose elements were not already in alphabetical order — `TiO2`, `MgB2`, `WO3`,
`NaCl`, `BaTiO3`, `LiFePO4` — carried a false finding.

That is worse than a cosmetic bug: `code × json_pointer` is the single most
actionable table this system produces, and it was being flooded with a defect
the generator had not committed. Reduction now divides by the GCD and touches
nothing else; ordering is a separate, per-provider concern handled by
`normalize.spell`.

## Four providers, not three

The specification asks for at least three independent sources spanning at least
two evidence classes. Three would have satisfied it. A fourth was added —
`mineral_reference`, a second experimental-class list with partly disjoint
coverage — because with exactly one source per class, two sources of the *same*
class can never disagree, and the conflict-resolution path would be dead code
that nothing exercises. The fourth corpus makes
`NOVEL.EVIDENCE.SOURCES_DISAGREE` reachable and testable.

## Thermodynamics: a real hull over a small table, not a fabricated one

Tier 1 shipped with no energies at all, deliberately. A hull invented over a
curated novelty corpus produces numbers shaped like thermodynamics that mean
nothing, and once a number exists in a field called
`energy_above_hull_ev_per_atom`, no downstream reader treats it with the
suspicion it deserves.

Tier 2 requires driving forces, an open-system treatment and a phase diagram, so
that position had to be revisited rather than restated. The resolution is a
**separate, explicitly-bounded table of standard formation enthalpies** — real
tabulated thermochemistry, not values back-derived from the novelty corpus —
with one rule enforced at every call site: *a quantity is computed only when
every species it depends on is in the table, and is otherwise null with a stated
reason.*

That rule is the whole difference between this and the thing that was rejected.
A compound in the table gets a real hull distance against real competing phases.
A compound outside it gets `null`, plus a description of the phase competition it
would have to beat. Neither case involves an estimated energy, and the two are
distinguishable in the artifact by a consumer who never reads this file.

The evidence that the machinery is right rather than merely present: the hull
independently reproduces the wüstite disproportionation — FeO lands 0.039 eV/atom
above the Fe3O4 + Fe tie-line — and the open-system screen reproduces the
Ellingham reduction ordering. Neither was tuned for; both are asserted by tests.

**Two real bugs were caught by checking against known chemistry, not by reading
the code.** The oxygen-potential comparison had its sign inverted, which made
every stable oxide look reducible. And gas-phase water was being read from a
composition-keyed table holding the *liquid* value, understating how reducing
hydrogen is by about 0.9 eV — enough to put CuO, NiO and Fe2O3 on the wrong
side. Both were invisible to schema validation and to every structural test.
What found them was asking whether the answers matched the Ellingham series.

## What was cut

- **Per-anion-class stability ceilings.** §6.3 is right that they behave better
  than a blanket cutoff, but this build has no per-class distributions and
  inventing the numbers would be worse than applying one honest global value.
- **Intermediate suboxides in the open-system screen.** Decomposition is
  evaluated to the elements, so Fe2O3 under hydrogen is scored as Fe2O3 to Fe
  rather than through Fe3O4 and FeO. The direction is right, the magnitude is
  not what a metallurgist would quote, and the limitation is stated.
- **Finite-temperature hulls.** Entropies are tabulated and used for the gas
  equilibria, but the hull itself is a 298 K enthalpy hull.
## Tier 3: two taken, three declined

§9 is explicit that "attempting a Tier 3 item badly is worse than not attempting
it", so the six were assessed individually rather than swept at.

**Taken: full lineage reconstruction.** The manifest already carried most of the
chain — config hash, corpus hashes and versions, input hashes, tool versions.
What was missing was a content hash of the code itself and something that reads
the chain back and *checks* it. `crucible lineage` reports `MATCH` / `DIVERGED` /
`UNKNOWN` per component and exits non-zero only on `DIVERGED`. Keeping `UNKNOWN`
distinct is the whole design: a corpus that cannot be located is not a corpus
that changed, and collapsing the two would make the tool either alarmist or
useless.

**Taken: cross-run aggregation.** This closes the loop `PROJECT.md` §1 opens.
Every other artifact answers "what is wrong with this batch"; this answers "what
is wrong with the generator, at what rate, across batches", which §1 says is the
only useful form. It is a pure projection — it re-verifies nothing and opens no
corpus — and it refuses to pool runs quietly when their taxonomy, reward
function, corpus versions or verifier source differ.

**Declined: a machine-learned synthesizability prior.** The corpora hold ~270
positive examples and **zero negatives**. A classifier trained on positive-only
data learns "appears in my list", not "can be made" — which is precisely the
blind spot §5.8 asks the bundle to *name* in its caveats. Building it would have
instantiated the blind spot instead of naming it. A prior is admissible only as
orthogonal evidence and never as a gate, and orthogonal evidence that is noise is
still noise.

**Declined: structure-level novelty matching.** The reference corpora contain no
structures at all — they are keyed on composition. There is nothing to match
against without either committing CIFs, which is a licensing and size problem, or
inventing them. Doing it correctly also needs symmetry-aware reduced-cell
comparison, which this build does not have. The decisive argument is the
direction of the failure: a naive matcher returns false "no match", and a false
novelty claim is the most consequential error this system can make (§3.1).

**Not built separately: the evaluation harness.** §9 lists "a small evaluation
harness comparing two versions of the pipeline on the same batch". That case is
`crucible aggregate` over two runs of one input, and building a second command
for it would have duplicated the loading, the rate arithmetic and the
fingerprint diff for no new capability.

What it did need was a gap closing: aggregation compared taxonomy version,
reward function, corpus fingerprint and verifier source, but not the resolved
**configuration** -- which is the thing that most often differs between two
versions of a pipeline. Two runs with different thresholds were being pooled
without comment. That is six lines and a test, not a new command. Widening the
minimum-distance tolerance and re-aggregating now reports exactly one code
moving (`STRUCT.GEOM.ATOM_OVERLAP`, 11.1 -> 0.0 per 100 records), one finding
retired, nothing new, and a warning that the difference is a statement about the
verifier rather than about the generator.

**Declined: reaction-network enumeration.** A network needs intermediates, and
the thermochemical table holds 146 species. Most multi-step paths would terminate
in "no data", so it would look like retrosynthesis and be a lookup over a table
too small to support one. §6.5 flags the whole area as research-grade and warns
that the prominent library in it pins an old core dependency and has not been
released in some time.

## What two more weeks would buy, in priority order

1. **A real offline corpus, for both novelty and energies.** A committed index —
   sorted table or Bloom filter, a few megabytes — built from a
   licence-compatible snapshot by a deterministic builder committed alongside.
   This is the single change that most improves the output: it is what makes
   `no_evidence` mean something, and it is what would let a genuinely *novel*
   composition receive a hull distance, which is the gap that most limits this
   build today.
2. **A formation-energy estimator** for compositions outside the table — even a
   Miedema-style model — so that the second regime in `LIMITATIONS.md` shrinks.
   Behind an optional extra, with the graded path identical without it.
3. **spglib for symmetry**, behind the existing `structure.py` interface, with
   the tolerance already in config and already in the assumption stack.
4. **Structure-level novelty matching**, which is what makes this useful to a
   generator proposing polymorphs rather than compositions.
5. **Finite-temperature hulls** from the entropies already tabulated, which
   would stop the carbonate routes reading as endothermic.

## Commit history

`main` is written to be read: one concern per commit, each building and passing
the suite present at that commit.
