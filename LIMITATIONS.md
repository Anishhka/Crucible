# Limitations

What this implementation gets wrong, what it cannot do, and where its answers
should not be trusted. Each entry names a **condition** and a **consequence**,
because a limitation without both is hedging.

Honesty here is scored in our favour and overclaiming is scored against us, so
this file is written to be *accurate*, not reassuring. If the review finds
something not listed here, that is a failure of this document.

---

## The big one: thermodynamic coverage is ~146 compounds

**Condition.** Energies come from a hand-entered table of standard formation
enthalpies. It holds 146 species. There is no DFT and no learned potential, so
**the formation energy of a compound not in that table cannot be predicted at
all.**

**Consequence.** This splits every proposal into three regimes, and which one a
record is in is stated on the record:

| Regime | What you get |
|---|---|
| Composition is in the table | Real `formation_energy_ev_per_atom`, `energy_above_hull_ev_per_atom`, decomposition products, and the §7.4 threshold applied. |
| Only the chemical system is covered | `energy_above_hull` is `null`. The hull *at that composition* is still computed, so the competing phases and their energy are reported — what the compound would have to beat. Verdict is `unknown`. |
| Neither is covered | Every energy field `null`, frame `not_evaluated`, verdict from composition screening alone. |

**The regime that matters most is the second one.** A genuinely novel proposal —
the interesting case, the reason this system exists — is by definition not in
the table, so it will almost never receive a hull distance. The thermodynamics
in this build validate well-known compounds and characterise the phase
competition around novel ones; they do not evaluate novel compounds directly.
Anyone reading a `plausible` verdict should check whether the record carries a
non-null `formation_energy_ev_per_atom`, because `plausible` is only reachable
in regime 1.

**The values are hand-entered and not machine-verified.** They were spot-checked
against known quantities — and the hull independently reproduces the wüstite
disproportionation (FeO sits 0.039 eV/atom above the Fe₃O₄ + Fe tie-line) and
the Ellingham reduction ordering, which are real results the table was not tuned
to produce. That is evidence the machinery is right, not that every one of the
146 entries is.

**The 298 K enthalpy is used as a 0 K formation energy.** That difference is
typically tens of meV per atom, against a threshold of 67 meV/atom. It is not
negligible and it is not corrected for.

## The threshold is a percentile, not a classifier

**Condition.** 0.067 eV/atom is the 90th percentile of the metastability
distribution of *observed* compounds — a positive-only sample.

**Consequence.** The distribution it comes from overlaps heavily with that of
never-observed hypothetical polymorphs, so the threshold cannot separate them and
does not claim to. A compound above it is flagged, not condemned; the finding is
level `warning` and does not gate the reward. Per-anion-class ceilings would
behave better — metastability limits vary by over an order of magnitude between
a glass-forming oxide and a nitride — but this build applies one global value
because it has no per-class distributions and inventing them would be worse.

## The open-system screen decomposes to elements only

**Condition.** `thermo.atmosphere_stability` compares the oxide against its
constituent elements plus an oxygen reservoir.

**Consequence.** Intermediate suboxides are not considered. Fe₂O₃ under
hydrogen is evaluated as Fe₂O₃ → Fe, when the real path runs through Fe₃O₄ and
FeO; the *direction* is right and the boundary is close, but the driving force
is not the one a metallurgist would quote. Nor is the volatility of any product
modelled: ZnO is reported as surviving hydrogen at 1223 K, which is true at
strict equilibrium and false in a real furnace where zinc vapour leaves and
drags the equilibrium with it.

Reducing-gas potentials assume a water-to-hydrogen ratio of 10⁻⁴ (10⁻³ for
forming gas). The conclusions are logarithmically insensitive to that, but it is
an assumption, and it is in `config`-adjacent code rather than user config.

## Prior-art coverage is tiny

**Condition.** The four committed corpora hold roughly 270 hand-curated
compositions between them. Real crystallographic and computational databases
hold 10³–10⁶ times that.

**Consequence.** A `no_evidence` tier means "absent from these specific lists at
their pinned versions", never "novel". The corpora are weighted towards
textbook-familiar inorganic compounds, so the failure mode is systematic rather
than random: an unusual-but-real composition is far more likely to miss than a
common one, and the tier will read `no_evidence` with a confidence of 0.45.

Do not use this build's novelty tiers to decide that something is publishable.

## Symmetry is compared, not determined

**Condition.** No symmetry finder is implemented. `structure.py` compares the
crystal family implied by the *lattice metric* against the family the asserted
space-group number belongs to.

**Consequence.** It catches a cubic cell asserted as `P1`, and a cubic space
group asserted on an unequal-axis cell. It does **not** catch a structure whose
lattice metric is right and whose internal symmetry is wrong — asserting
`Fm-3m` on a cubic cell whose atomic positions have no such symmetry passes
unremarked. Anything requiring actual symmetry operations (Wyckoff positions,
structure matching against a reference, prototype identification) is absent.

## Structure matching and structure-level novelty do not exist

**Condition.** Novelty is decided on composition alone.

**Consequence.** Two different polymorphs of the same composition are the same
material to this build. A genuinely new structure in a well-known chemical
system will be reported as `experimentally_reported` and scored at novelty 0.0.
For a generator proposing new polymorphs, this build's novelty signal is close
to useless.

## Claimed properties are range-checked, never corroborated

**Condition.** There is no corpus of measured property values.

**Consequence.** No claim can ever be assessed `supported` or `contradicted`.
The reachable outcomes are `unsupported` (survived the checks, nothing to
compare against), `implausible`, `unit_error` and `unverifiable`. A band gap of
2.9 eV claimed for rutile TiO₂ is scored identically to one of 3.2 eV, even
though the literature distinguishes them.

The property table in `claims.py` covers ~16 quantities. A property outside it
is `unverifiable` with a stated reason — correct, but uninformative.

## The oxidation-state and electronegativity tables are curated, not sourced

**Condition.** `chemdata.py` carries a hand-entered shortlist of commonly
observed oxidation states per element, and Pauling electronegativities.

**Consequence.** Compounds relying on an uncommon or mixed valence will fail the
charge-balance screen and pick up a `CHEM.CHARGE.NOT_NEUTRAL` warning. That
warning is level `warning` and never gates (`ASSUMPTIONS.md` §7.6), so it costs
partial reward rather than rejecting the proposal — but the false-positive rate
on mixed-valence and f-block chemistry is real and unmeasured here. The values
have not been cross-checked against a primary source.

## Route driving forces are enthalpies, not free energies

**Condition.** A route's driving force is ΔH of reaction at 298.15 K, computed
only where every species in it is in the thermochemical table.

**Consequence.** **The carbonate family is systematically understated.**
`BaCO3 + TiO2 → BaTiO3 + CO2` comes out at +0.13 eV/atom — endothermic — which
is correct at 298 K and thoroughly misleading about a reaction that runs fine at
1500 K because the entropy of CO₂ release drives it. The caveat travels on each
route's own `assumptions` list rather than living only in this file, but a
reader who ranks routes purely on this number will prefer the wrong family.

`competing_products` is always empty: no side-reaction enumeration is performed,
so a route that balances and would in practice yield something else is reported
without comment.

## Repairs are narrow by design

**Condition.** Repairs are generated only for defects where the correct value is
unambiguous: an undeclared field, an unreduced formula, a formula/elements-map
disagreement, a placeholder value, a physically impossible value, and a reducing
atmosphere on an oxide.

**Consequence.** No repair is offered for a homoglyph formula or an unknown
element symbol, because both would require guessing which element was meant —
the exact failure this system exists to catch. Those records therefore never
produce a preference pair, so the preference data is biased towards the defect
classes that happen to be mechanically fixable.

The `sync-elements-map` repair treats the formula string as authoritative when
the two disagree. If the map was the intended one, that repair fixes the wrong
side; its own rationale says so and its confidence is 0.5 to reflect it.

A repair is promoted to a preference pair only when re-verification actually
raised the reward. Repairs that were genuinely re-verified and did not help are
reported on the verdict with their real post-repair status — on the public
fixtures, 6 repairs are re-verified and 3 become preference pairs.

## The cache is not exercised under grading

**Condition.** `--cache-dir` is optional and has no default, because the
contract forbids writing anywhere except `--out` and a self-created temporary
directory.

**Consequence.** A graded run passes no `--cache-dir`, so it never writes or
reads a cache and every graded run is cold. The capability is real -- a warm run
produces byte-identical graded artifacts to a cold one, which is asserted by a
test, and a superset re-run processes only the records that are new -- but it
buys nothing under grading and should not be read as a performance claim about
the graded path.

The cache key includes the config hash, every corpus hash, and the code version,
so a threshold change invalidates every entry. That is deliberate and it means
the cache is nearly useless during development, which is the correct trade for a
verification system.

## Concurrency is not implemented

**Condition.** `--max-workers` is accepted and recorded in
`provenance.environment`. It does not change the execution strategy
(`ASSUMPTIONS.md` §7.9).

**Consequence.** Wall-clock time scales linearly with batch size. Measured at
roughly 1–2 seconds for a 15-record batch on one core, so the 10-minute budget
for 20 proposals is met with several orders of magnitude of headroom — but a
100 000-record batch would take minutes rather than seconds, and nothing in the
design would exploit a second core.

## Where it would stop scaling

The corpora are loaded whole into memory and indexed by composition key. At
~270 entries that is negligible. A corpus of 10⁶ entries would need roughly
200–400 MB parsed naively, which still fits the 4 GiB budget but would want a
sorted on-disk index or a Bloom filter instead. The minimum-distance scan is
O(n² × 27) per structure; at the schema's 500-site maximum that is ~3.4M
distance evaluations, around a second in pure Python. A structure an order of
magnitude larger would need the neighbour search rewritten around a cell list.

## Container: built and verified

**Condition.** The image builds from a clean tree with `docker build --no-cache`
and has been run under the exact graded invocation.

**Measured, not asserted:**

| Check | Result |
|---|---|
| `scripts/selfcheck.sh` | **83 passed, 0 failed, 0 skipped** |
| Image size | **77 MiB** against a 2048 MiB ceiling |
| Wall clock, 15-proposal batch, 2 cores | **4 s** against a 600 s budget |
| Determinism at 1 core / 1 GiB vs 2 cores / 4 GiB | byte-identical |
| Final `USER` | `10001:10001`, numeric non-zero |
| `curl`/`wget`/`nc`/`gcc` in final image | none |
| `sys.prefix` | `/opt/crucible-env`, not the conda root |
| Accelerator runtime | none present |

**What the build actually caught.** The first build failed: `conda env create`
does not accept `--override-channels`/`--channel` — those are `conda create`
flags. That was a hard build failure sitting in a Dockerfile that had never been
executed, and it would have failed at review time as a Tier 0 gate. Channel
selection now lives inside the lock file, which lists `nodefaults`.

**Residual risk.** The build was verified on Docker Desktop on Windows with a
linux/amd64 backend, not on a Linux host, and not from a fresh `git clone` on a
machine that has never seen this project. The remaining gap is small but real:
`git clone && docker build` on a clean machine is still the exact command
`PROJECT.md` §12 asks for and it has not been run in that form.

## Test-suite gaps

The determinism test compares graded artifacts across reruns, `--max-workers`
and a hostile environment, and it runs the program rather than mocking it. It
does **not** test across machines or Python patch versions, so a
platform-dependent float formatting difference would not be caught here.

The container checks are exercised by `scripts/selfcheck.sh` rather than by
pytest, so they do not run in the unit-test suite. The CI workflow runs the
equivalent checks on every push.

## Things that look like features and are not

- `verdict.summary` is generated from a fixed template per status. It is not a
  natural-language explanation of the specific proposal.
- `novelty.confidence` is a fixed value per tier adjusted for provider
  availability. It is not a calibrated probability and should not be read as one.
- Route `confidence` is a constant 0.3. It encodes "this build does not rank
  routes", not a belief about the route.

## Lineage cannot prove what it did not record

**Condition.** `crucible lineage` verifies an output against the *current*
machine.

**Consequence.** It answers "has anything moved since this run", not "was this
run correct". A run whose corpora and code both still match can still be wrong,
just reproducibly so. It also cannot verify anything the manifest did not
record: runs produced before `crucible_source_sha256` existed report `UNKNOWN`
for code, and an input file that is not supplied via `--input` reports `UNKNOWN`
rather than being assumed intact.

`git_commit` is `null` inside the container, because a built image carries no
repository. `CRUCIBLE_GIT_COMMIT` can be stamped in by CI; absent that, the
source hash is the identifier that survives packaging, and it is the one lineage
actually compares.

## Cross-run aggregation cannot attribute a change to the generator

**Condition.** `crucible aggregate` pools verdict and violation records across
run directories.

**Consequence.** A code whose rate falls between two runs is **not** evidence
the generator improved. It is equally consistent with a batch that happened to
contain fewer of that defect, with a corpus that grew and started recognising
more compounds, or with a change to the verifier itself. The report leads with
whichever of those it can detect — differing taxonomy versions, reward
functions, corpus fingerprints or source hashes — but it cannot detect a batch
that is simply differently composed, and that is the most likely confound.

The new/resolved/persistent split is computed on violation fingerprints, which
are stable for the same finding on the same input. Across **disjoint** batches a
"new" finding therefore means only "a defect not previously seen", not a
regression. Reading it as a regression requires the batches to overlap, which
the tool cannot check and does not claim.

Rates are per 100 verdict records, not per proposal: quarantined records never
reach a verdict, so a batch that quarantines heavily will show rates computed
over a smaller denominator than its proposal count suggests.
