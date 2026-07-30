# Assumptions

Every decision `PROJECT.md` §7 deliberately leaves open, what this build chose,
and why. One heading per decision, cited by number. Where a choice is visible in
the code, the module is named so the claim can be checked rather than taken on
faith.

Nothing here claims to be the right answer. Each is a defensible answer that the
implementation actually follows.

---

## §7.1 What "already reported" means

**Choice.** Prior art is tiered by the *class of evidence*, never by a single
boolean, and the tier is a property of which provider answered:

| Tier | Raised when |
|---|---|
| `experimentally_reported` | A provider carrying experimental provenance returns a match on the reduced formula. |
| `computed_only` | Only a computational-provenance provider matches. |
| `literature_mention` | Only the bibliographic provider matches. |
| `no_evidence` | **Every** provider was *able to answer* and none matched. |
| `unknown` | At least one provider could not answer, so the tiers above cannot be distinguished. |

A hit on the parent chemical system but not the stoichiometry is **not** prior
art for the proposal; it is recorded as a `chemsys` match on the query and used
only to inform the feasibility rationale.

**Why.** The distinction that costs the most to get wrong is "computed once by
somebody" versus "actually made". Collapsing those into `known: true` throws away
exactly the signal an operator needs. `no_evidence` is deliberately expensive to
reach — it requires *every* provider to have genuinely answered — because the
alternative is manufacturing novelty out of infrastructure failure.

**Where.** `providers.py` (`tier_from_answers`), `evidence.py`.

---

## §7.2 Which source wins when sources disagree

**Choice.** Sources are not averaged and not last-write-wins. Precedence is by
evidence class, strongest first: **experimental > computed > literature**. When
two providers of *different* classes both match, the higher class sets the tier
and the disagreement is not recorded as a conflict, because they are answering
different questions and both answers are true.

A `NOVEL.EVIDENCE.SOURCES_DISAGREE` finding is raised only for a genuine
contradiction: two providers of the **same** evidence class returning different
answers for the same query. Every conflict is written to
`novelty.conflicts[]` with the providers involved, a description, and the
resolution rule that was applied.

**Why.** "Disagree" is usually the wrong word, as §7.2 itself hints. A
crystallographic database and a DFT corpus returning different answers is not a
contradiction; it is two different questions. Reserving the conflict finding for
same-class disagreement keeps the signal meaningful instead of firing on every
proposal.

**Where.** `providers.py` (`CLASS_PRECEDENCE`, `resolve_conflicts`).

---

## §7.3 Formula equality

**Choice.** Two compositions are the same material when their **reduced integer
stoichiometries are equal**, compared as an element→coefficient mapping, not as
strings.

- A supercell **is** the same material: `Fe4O6` and `Fe2O3` share a candidate
  key for matching purposes. The fact that the generator wrote the unreduced
  form is preserved separately as `CHEM.FORMULA.NOT_REDUCED` at level `note`,
  because it is an observation about the generator, not a defect in the material.
- Non-stoichiometric compositions are **not** tolerance-matched. `LiFePO4` and
  `Li0.98FePO4` are different keys. No tolerance was chosen because any value
  would be arbitrary here and a wrong merge is worse than a missed one.
- Reduction is by GCD of integer coefficients **only**. It never reorders
  element symbols — see the bug note in `DECISIONS.md`; alphabetising as part of
  "reduction" makes `TiO2` compare unequal to itself.

**Why.** Reduced-composition equality is what every public corpus keys on, and
it is the only definition under which the same material proposed twice in two
spellings deduplicates. Fractional tolerance matching is left out because it
requires a threshold with no defensible value.

**Where.** `normalize.py` (`reduce_counts`, `composition_key`).

---

## §7.4 The stability threshold

**Choice.** **0.067 eV/atom**, applied against a real convex hull, with a second
band at twice that value.

| Energy above hull | Verdict |
|---|---|
| ≤ 0.067 eV/atom | `plausible` |
| ≤ 0.134 eV/atom | `marginal` |
| > 0.134 eV/atom | `implausible` |

**Why this number.** It is the 90th percentile of the metastability
distribution of experimentally observed compounds. It was chosen over the round
cutoffs in common use — 25, 50, 100 meV/atom — precisely because those are
convention rather than derivation, and because a study across 41 chemical
systems found blanket cutoffs at those values exclude roughly 63%, 39% and 26%
respectively of polymorphs that had *already been synthesised*.

**What this number is not.** It is a descriptive percentile of a **positive-only
sample**. It is not a classifier, and the distribution it comes from overlaps
heavily with that of never-observed hypothetical polymorphs. A value above it is
a prompt for review, never a proof of impossibility — which is why
`FEAS.THERMO.EHULL_ABOVE_THRESHOLD` is level `warning` and does not gate the
reward.

**The second band exists because of that overlap.** A single cutoff would force
a binary decision exactly where the evidence is weakest. The `marginal` band
between one and two times the threshold is where the two distributions are most
entangled, and saying so is more useful than picking a side.

**Per-anion-class overrides are not implemented**, though §6.3 is right that
chemistry-specific ceilings behave far better than blanket ones: metastability
limits vary by over an order of magnitude between a glass-forming oxide and a
nitride. Doing it properly needs per-class distributions this build does not
have, and inventing the per-class numbers would be worse than applying one
honest global value. Stated in `LIMITATIONS.md`.

**The threshold only applies where the compound's own energy is known** — that
is, where it appears in the committed thermochemical table. For a genuinely
novel composition, `energy_above_hull_ev_per_atom` is `null` and no threshold is
applied to it. What is still reported is the hull at that composition: the
competing phases and what they are worth.

**Where.** `config.py` (`feasibility.stability_threshold_ev_per_atom`),
`feasibility.py`, `thermo.convex_hull`.

---

## §7.5 The thermodynamic frame

**Choice.** The frame is **whichever one actually ran**, recorded per record
rather than declared globally. Three values occur:

| Frame | When |
|---|---|
| `grand_potential` | An open-system oxygen screen ran: the composition is an oxide covered by the thermochemical table, and the atmosphere's chemical potential was computed. |
| `closed_0k` | A convex hull was built, but no open-system screen applied. |
| `not_evaluated` | No phase in the table lies in this chemical system; nothing energetic was computed. |

**Closed-system half.** Standard formation enthalpies at 298.15 K, used as a
proxy for 0 K formation energies, in a defect-free stoichiometric bulk system.
The proxy is the main approximation and it is not free: the difference between
ΔH(298 K) and ΔE(0 K) is typically tens of meV per atom, which is a substantial
fraction of the 0.067 eV/atom threshold. Stated on every record's assumption
stack, not just here.

**Open-system half.** For an oxide, the oxygen chemical potential Δμ_O imposed
by the atmosphere, against the formation energy. Oxidising and inert gases fix
μ_O through their oxygen partial pressure; reducing gases fix it through the
H₂ + ½O₂ ⇌ H₂O(g) equilibrium, which lands near −2.9 eV at 1223 K. The
resulting μ_O values are written into `assumptions.chemical_potentials`.

This is what licenses `FEAS.ATMOSPHERE.UNSTABLE`, which the taxonomy explicitly
forbids raising from a closed zero-temperature analysis. It reproduces the
Ellingham ordering — CuO, Cu₂O, NiO and Fe₂O₃ are reduced by hydrogen at 1223 K;
TiO₂, Al₂O₃, MgO and SiO₂ are not — and that reproduction is asserted by a test,
because a screen that got that ordering wrong would be worse than none.

**`FEAS.FRAME.INAPPROPRIATE` is now narrower than it was.** It fires when the
proposed conditions sit outside what was *actually evaluated* — a 1200 K
synthesis judged only by a closed 298 K hull, or a reactive atmosphere for which
no open-system screen could run. It does **not** fire when the open-system screen
did run for the proposed atmosphere, because there the frame reaches the question
and crying wolf would devalue the finding.

**Where.** `feasibility.py` (`screen`, `assumption_stack`,
`check_frame_applicability`), `thermo.oxygen_chemical_potential`.

---

## §7.6 Severity of a charge-neutrality failure

**Choice.** `CHEM.CHARGE.NOT_NEUTRAL` is **level `warning`, never a gate, and
never sufficient on its own to reject a proposal.** It contributes to the
non-gate `composition_valid` reward component at partial credit (0.5), not zero.

Before the check runs at all, compositions are classified. For the classes where
the ionic model is known not to apply — metals and intermetallics, and metal
borides/silicides/carbides/phosphides — the check returns `not_applicable` with
a stated reason rather than a failure. That is a statement about the *model's*
domain, not about the material.

**Why.** §6.2 puts the false-positive rate of this screen at roughly one in six
*real reference structures*, concentrated in alloys, mixed-valence and f-block
chemistry. MgB₂ in the baseline fixture is a famous superconductor with no
charge-neutral ionic assignment. A screen that treats its own known blind spot
as fatal discards real materials, so the blind spot is encoded explicitly and
generically — by bonding class, not by special-casing MgB₂ by name.

**Where.** `composition.py` (`check_charge_balance`, `IONIC_MODEL_SCOPE`),
`chemdata.is_metallic_bonding_class`.

---

## §7.7 Reward decomposition, gates, and weights

**Choice.** `reward.crucible@1.0.0`.

| Component | Weight | Gate | Meaning |
|---|---:|:---:|---|
| `parse_ok` | — | ✅ | The record was admitted at the envelope level. |
| `schema_valid` | — | ✅ | The record conforms to `proposals.schema.json`. |
| `formula_valid` | — | ✅ | The formula normalised to real element symbols. |
| `composition_valid` | 0.25 | — | Charge balance and electronegativity ordering. |
| `structure_valid` | 0.20 | — | Geometry, lattice, density, occupancy, symmetry. |
| `novelty` | 0.25 | — | Graded by tier; unknown scores neutral, not zero. |
| `feasibility` | 0.15 | — | Screen outcome. |
| `claims_supported` | 0.15 | — | Fraction of claimed properties that survive audit. |

Arithmetic is exactly the one fixed by the contract: gates multiply, non-gates
are a weighted mean normalised by their own weight total.

**Gate choice.** Only the three structural preconditions gate. If a record could
not be parsed, does not conform to the input contract, or has a formula that is
not chemistry, then every downstream number about it is meaningless and it must
not collect partial credit from a lucky novelty lookup. Nothing *scientific*
gates — not charge balance (§7.6), not novelty, not feasibility — because every
one of those has a known false-positive rate and a gate is unrecoverable.

**Unknown scores neutral.** A component whose check could not be run contributes
`0.5`, not `0.0`, and the record's status becomes `unverifiable`. Scoring an
unavailable lookup as zero trains the generator against our infrastructure.

**Where.** `reward.py`.

---

## §7.8 What goes in the offline corpus

**Choice.** Five committed datasets, all hand-curated, all with their coverage
stated in the manifest rather than implied:

| Corpus | Class | Entries | What it is |
|---|---|---:|---|
| `experimental_snapshot` | experimental | 156 | Well-established inorganic compounds with experimental provenance. |
| `mineral_reference` | experimental | 20 | A second experimental-class list, partly disjoint, so that same-class disagreement is reachable at all. |
| `computed_snapshot` | computed | 55 | Compositions plausibly appearing in DFT corpora but not established experimentally. |
| `literature_cassette` | literature | 40 | A **replay cassette**: recorded question/answer pairs only. |
| `thermochemistry` | — | 146 | Standard formation enthalpies and entropies (298.15 K, 1 bar). Not an evidence provider: it is what makes §7.4 and §7.5 computable at all. |

The thermochemical table is hand-entered from standard reference values and is
**not machine-verified against a primary source** — stated in its own
`coverage_note`, in the assumption stack of every record that uses it, and in
`LIMITATIONS.md`. It is indexed by composition alone, so it cannot distinguish
polymorphs: quartz and cristobalite share a key. Where that matters acutely, as
for gas-phase water in the H₂/H₂O equilibrium, the value is carried explicitly
in code rather than looked up, with a comment saying why.

**The coverage rule is what makes all of this safe:** a quantity is computed only
when every species it depends on is in the table, and is otherwise `null` with a
stated reason. That is why a driving force, a hull distance and an atmosphere
screen can each be absent independently on the same record.

A question outside a snapshot's coverage is a **miss** (real evidence of absence
*within that corpus*). A question outside the cassette's recording is
**`unavailable`** — not a miss — because a replay miss means the question was
never asked. This distinction is the single most consequential one in the system
and it is what makes exit `4` the ordinary outcome here.

Every corpus declares `coverage_note` and a pinned `snapshot` version in
`run_manifest.reference_data`, so a novelty claim is always relative to a stated
corpus at a stated version.

**Why.** A raw dump cannot be committed (§8.2: hosts reject files past ~100 MB)
and cannot be fetched in a graded run. A small honest corpus whose limits are
stated beats a large one whose limits are not. The corpus is deliberately small
enough that `no_evidence` is rarely reachable, which is the correct outcome: this
build should almost never claim novelty.

**Where.** `reference_data/*.json`, `providers.py`.

---

## §7.9 Concurrency and work bounding

**Choice.** **Serial processing. `--max-workers` is accepted, recorded in the
resolved config, and deliberately does not change the execution strategy.**

Work is bounded instead by refusing unbounded computations up front: the
oxidation-state search is capped at 20 000 combinations and skipped (not
truncated) beyond that; the periodic minimum-distance scan is bounded to a
3×3×3 image shell; route enumeration is capped per proposal. Input is bounded by
a byte budget enforced on the raw stream *before* a parser sees it, so peak
memory does not scale with input size.

**Why.** The contract requires byte-identical output at `--max-workers 1` and
`--max-workers 8`. The batch is at most 1000 records of pure-Python arithmetic
and finishes in well under a second; a thread pool would buy nothing measurable
and would introduce a reordering surface for no gain. §8 of `PROJECT.md` is
explicit that where throughput and determinism conflict, determinism wins. Being
able to say "concurrency cannot affect the output because there is none" is a
stronger guarantee than a sorted merge after a parallel map.

**Where.** `pipeline.py`, `LIMITATIONS.md`.

---

## §7.10 What the report says

**Choice.** `report.html` is written for a materials scientist with ten minutes
who is deciding whether to trust the run, in this order:

1. **Whether to trust it at all** — run identity, exit status, corpus versions
   and their coverage notes, and the count identity, at the top.
2. **What was wrong with the generator** — the `code × field` table first,
   because "the oxygen stoichiometry is wrong in 40% of perovskites" is
   actionable and "40% were bad" is not.
3. **Per-proposal disposition** — status, reward, novelty tier, feasibility.
4. **Caveats** — what this run cannot tell you, unconditionally present.

Everything on the page is derived from the graded artifacts and nothing else.
There is no external asset, no CDN, no remote font, and no `http://` or
`https://` string anywhere in the file — sources are cited by identifier, since
a checker reading the file cannot tell a fetched asset from a hyperlink.

**Why.** The reader's first question is not "what did you find" but "should I
believe this". Putting corpus coverage and the unavailable-check count above the
results is what lets them answer it in the first thirty seconds.

**Where.** `report.py`.
