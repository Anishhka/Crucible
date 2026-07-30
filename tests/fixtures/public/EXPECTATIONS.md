# Public fixture expectations

These are the inputs shipped with the specification. They exist so that every mechanical requirement
in `CONTRACT.md` can be checked by you, on your machine, before you submit anything.

## A note on how these files are written

In the public fixtures, **each proposal's `rationale` field describes that proposal's own defect.**
They are a teaching set. Read them.

The fixtures used when your submission is reviewed are drawn from the same schema and the same
defect classes, but they are **not** self-documenting, they contain records these files do not, and
their contents are not derivable from these files. Hard-coding anything about the public fixtures —
a proposal id, a formula, a count, a hash — will pass here and fail there.

## Envelope-level outcomes

A conforming implementation produces these exit codes. Where two are listed, both are correct and the
difference is a property of your evidence corpus, not of your code.

| Fixture | Proposals | Expected exit | Why |
|---|---|---|---|
| `01_baseline.proposals.json` | 6 | `0` or `4` | Well-formed throughout. Every proposal reaches a terminal verdict and nothing is quarantined, so nothing here forces a degraded run. `4` is nevertheless correct, and common: `CONTRACT.md` §3 requires exit `4` whenever any lookup was `unavailable`, and §7 makes a replay lookup with no recording exactly that. An honest corpus that does not happen to cover all six compositions therefore exits `4` on a clean batch. |
| `02_messy.proposals.json` | 10 | `4` | Schema-valid, semantically defective. Records are quarantined or downgraded; the run still produces a complete, valid artifact set. |
| `03_adversarial.proposals.json` | 15 | `4` | Hostile content at the record level. Some records cannot be admitted; the rest are processed. |
| `04_malformed_nonfinite.proposals.json` | — | `3` | Envelope-level. Non-finite numeric literals. No verdicts are possible. |
| `05_malformed_duplicate_key.proposals.json` | — | `3` | Envelope-level. A repeated key inside one object. |
| `06_malformed_depth.proposals.json` | — | `3` | Envelope-level. Nesting depth beyond any legitimate bound. |
| `07_unsupported_version.proposals.json` | — | `3` | Envelope-level. A `schema_version` MODEL component this build does not implement. |
| oversize (generated, see below) | — | `3` | Envelope-level. Exceeds the configured byte budget. |

The oversize fixture is not committed, because committing a file that large is antisocial. Generate
it with:

```
python3 scripts/make_oversize_fixture.py --out /tmp/08_oversize.proposals.json --mib 256
```

Reading it must not consume memory proportional to its size. If your process grows to hundreds of
megabytes before rejecting the file, the byte budget is being enforced after the parse rather than
before it, which is not a budget.

## The distinction between exit 3 and exit 4

This trips people up, so it is stated once, precisely:

- **Exit 3** is an *envelope* failure. The input document as a whole could not be admitted, so no
  verdict about any proposal is possible. Fixtures 04 through 07 are envelope failures.
- **Exit 4** is a *degraded success*. The document was admitted. Some individual records were
  quarantined, or some checks could not be completed, but a complete and schema-valid artifact set
  was still written. Fixtures 02 and 03 are degraded successes.

One bad record inside a good envelope is never an envelope failure. A run that returns 3 because a
single proposal had a bad formula has thrown away nine good verdicts.

Exit `4` is not a grade. If your submission answers prior-art questions from a replay or cassette
provider, exit `4` is the ordinary outcome and exit `0` is the exception; see `CONTRACT.md` §7. The
way to turn a `4` into a `0` is to extend coverage so the lookups genuinely succeed, never to record
an `unavailable` lookup as a miss. The second is the failure this whole specification is built to
punish, and it is the one that looks like success.

## What every run must produce

For fixtures that exit `0` or `4`, the output directory must contain a complete artifact set as
defined in `CONTRACT.md` §4, and every artifact must validate against its schema. For fixtures that
exit `3`, the output directory must contain `run_manifest.json` and `data_quality.json` recording
the rejection, and must not contain a verdicts file implying work that was not done. That manifest is
held to the relaxations stated in `CONTRACT.md` §3: `reference_data`, `input.files`, `outputs` and
`aggregates.reward` may be empty or absent, because a run that read no proposal consulted no corpus
and computed no reward. Do not populate them with placeholder values to satisfy a schema.

## Reference materials in the baseline fixture

`01_baseline.proposals.json` contains four materials that are unambiguously known to science:
rutile titanium dioxide, hematite, lithium iron phosphate, and magnesium diboride. They are there as
positive controls. If your novelty logic reports any of them as having no prior art, that is not a
threshold that needs tuning; it is a defect, and it is the defect this fixture exists to catch.

`P-0005` is a negative control with an implausible stoichiometry.

`P-0006`, magnesium diboride, is there for a more specific reason. Charge-neutrality screening is
built around ionic compounds and rejects a meaningful fraction of intermetallics and borides. A
verifier that treats such a rejection as fatal will discard a material that is not merely real but
famous. How you handle that is one of the decisions this specification deliberately leaves to you.

## Determinism

Any fixture that exits `0` or `4` must satisfy the byte-identity requirement in `CONTRACT.md` §6.
`scripts/selfcheck.sh` checks this for you.
