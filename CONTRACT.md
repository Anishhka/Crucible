# Crucible — Interface Contract

This document specifies the parts of Crucible that are **not** open to interpretation: the command
line, the exit codes, the artifacts, the schemas, the determinism guarantee, and the container.

Everything in this file is mechanically checkable, and `scripts/selfcheck.sh` checks it. Nothing in
this file is a matter of taste.

The scientific content of the tool — which checks to run, which thresholds to use, what counts as
prior art, how to weigh contradictory sources — is deliberately **not** here. That belongs to
`PROJECT.md`, and it is where the judgment lives.

Requirement keywords follow RFC 2119: **MUST**, **MUST NOT**, **SHOULD**, **MAY**.

---

## 1. Runtime and invocation

Crucible is a Python program that runs inside a container. The container is the delivery mechanism
and the graded execution environment. A working directory-relative or host-Python invocation MAY be
documented as a convenience, but it is not the contract.

The image **MUST** declare an `ENTRYPOINT` such that the following works from any host directory:

```
docker run --rm <image> --version
docker run --rm <image> --help
docker run --rm -v /abs/in:/data/in:ro -v /abs/out:/data/out:rw <image> run --input /data/in/batch.json --out /data/out
```

The program:

- **MUST** work from any working directory. No path may be discovered relative to the repository, to
  `$HOME`, or to the location of the source tree.
- **MUST** take every input and output location from an argument.
- **MUST NOT** write anywhere except inside `--out` and a temporary directory it creates itself.
- **MUST NOT** prompt, require a TTY, use colour codes when stdout is not a TTY, or block on input.
- **MUST NOT** require a network connection to run any graded command.
- **MUST NOT** require any credential to run any graded command.

## 2. Command line

Exactly these commands. Additional commands **MAY** exist; these **MUST** exist with these names and
these options.

`--help` **MUST** be accepted at the top level and on every subcommand, **MUST** print usage to
stdout, **MUST** exit `0`, and **MUST NOT** require any other argument or a network connection. It
is how the graded environment discovers the command surface before it has an input to give you.

### `crucible run`

```
run --input <PATH> --out <DIR>
    [--config <FILE>] [--seed <INT>] [--offline] [--now <RFC3339>]
    [--timeout <SECONDS>] [--max-workers <INT>] [--provider <NAME>]... [--verbose]
```

| Option | Required | Meaning |
|---|---|---|
| `--input` | yes | A batch file, or a directory of batch files. When a directory, files are processed in sorted order by name and the results are merged into one run. |
| `--out` | yes | Output directory. Created if absent. Must be writable. An existing non-empty directory **MUST** be either refused or fully overwritten — never merged, because a merged output directory is not reproducible. |
| `--config` | no | Configuration file. Format is yours. Absent means documented defaults. |
| `--seed` | no | Integer seed. Default `0`. Threaded into every source of randomness and echoed into the manifest. |
| `--offline` | no | Fail-closed offline mode. See §7. |
| `--now` | no | An RFC 3339 instant with an explicit offset, used as the run's logical time. Absent means `SOURCE_DATE_EPOCH` if set, otherwise wall clock. |
| `--timeout` | no | Wall-clock budget in seconds for the whole run. Reaching it is a degraded success, not a crash. Default **MUST** be finite. |
| `--max-workers` | no | Concurrency bound. Any value **MUST** produce identical graded output. |
| `--provider` | no | Repeatable. Restricts the evidence providers used. |
| `--verbose` | no | More diagnostics on stderr. **MUST NOT** change any graded artifact. |

### `crucible verify --out <DIR>`

Re-reads an output directory produced by a previous `run` and independently re-derives every
invariant it can from the artifacts alone. Exits `0` when the artifact set is internally consistent,
non-zero when it is not.

This command **MUST NOT** simply re-run the pipeline. It exists to answer the question *"is this
output directory self-consistent?"* using only what is in it. At minimum it re-checks: every artifact
against its schema; the manifest's count identities; that every `sha256` in `outputs[]` matches the
file on disk; that every violation row references a verdict that exists; that every figure referenced
by a verdict exists and has its declared data companion; that every reward equals the value implied
by its own recorded components; and that `feedback/` projections are consistent with the verdicts
they claim to project.

### `crucible schema --emit <NAME>`

Prints the named JSON Schema to stdout and exits `0`. `<NAME>` is one of `proposals`, `verdict`,
`violation`, `run_manifest`, `data_quality`, `feedback_bundle`.

The emitted schema **MUST** be the one the implementation actually validates against. A schema that
lives in a documentation folder and diverges from the code is worse than no schema.

### `crucible cache warm --input <PATH> [--out <DIR>]`

Populates the local evidence cache from live sources so that a later `--offline` run can answer.
This is the **only** command permitted to require a network connection. It is never part of a graded
run.

An implementation that ships a committed corpus rather than a live cache has nothing to warm. That is
a legitimate design, and in that case the command **MUST** still exist, **MUST** report that there is
nothing to do and why, and **MUST** exit `0`. Nothing is graded on what it fetches; what is graded is
that the command exists and does not fail when the answer is "the corpus is already here".

### `crucible --version`

Prints exactly one line to stdout and exits `0`.

## 3. Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. Every proposal reached a terminal verdict. All artifacts written and schema-valid. |
| `1` | Unexpected internal error. A stack trace is acceptable on stderr; a stack trace is not acceptable for any other exit code. |
| `2` | Usage or configuration error. Unknown flag, missing required argument, unreadable config, contradictory options. Nothing was processed. |
| `3` | The input was rejected at the envelope level. No verdict about any proposal was possible. |
| `4` | Degraded success. A complete, schema-valid artifact set was written, but at least one of: a proposal was quarantined, a check was `unavailable`, a provider was unreachable, or the timeout was reached. |

Rules that follow from this and are checked:

- A single defective proposal inside a well-formed envelope is **never** exit `3`.
- Exit `4` **MUST** be accompanied by evidence in the artifacts: a non-zero `counts.quarantined`, or
  a check with outcome `unavailable`, or a non-empty `config.providers_unavailable`.
- Exit `0` **MUST NOT** be returned when any check outcome is `unavailable`. Reporting a clean run
  while silently having skipped a check is the failure mode this contract exists to prevent.
- For the purposes of the exit code, an `evidence.queries[]` entry with `status` `unavailable` counts
  as an unavailable check: any run containing one **MUST** exit `4`, never `0`, and its
  `run_manifest.status` **MUST NOT** be `ok` — regardless of whether another provider answered the
  same question. A question one source could not be asked is a question that was not fully asked.
- A `proposal_id` that repeats within a batch is a defect, not a merge instruction: the later record
  **MUST** be quarantined and counted in `counts.quarantined` (never merged or overwritten), and a
  run that quarantines any record is a degraded success and exits `4`.
- Exit `3` **MUST** still write `run_manifest.json` and `data_quality.json` recording the rejection.
  A run that never admitted a proposal cannot honestly populate every field the schemas require of a
  completed run, and it **MUST NOT** invent them: on an exit-`3` manifest the `minItems` constraints
  on `reference_data`, `input.files` and `outputs`, and the requirement for `aggregates.reward`, are
  waived, and those members **MAY** be empty or absent. Everything that *is* known — which file was
  read, which bound it exceeded, which rule fired — **MUST** still be recorded.

## 4. Output artifacts

For a run that exits `0` or `4`, `--out` **MUST** contain exactly this structure. Additional files
**MAY** be added; these **MUST** be present.

```
<out>/
  run_manifest.json          # one object, run_manifest.schema.json
  verdicts.jsonl             # one verdict per line, verdict.schema.json
  violations.jsonl           # one finding per line, violation.schema.json
  data_quality.json          # one object, data_quality.schema.json
  feedback/
    index.json               # one object, feedback_bundle.schema.json
    <projection files>       # as declared in index.json
  report.html                # self-contained, see §5
  figures/
    <figure files>           # each with a JSON companion, see §5
```

**Ordering.** `verdicts.jsonl` **MUST** be sorted by `proposal_id` using byte-wise ordering of the
UTF-8 encoding. `violations.jsonl` **MUST** be sorted by `(proposal_id, code, json_pointer)`, same
ordering. Where a duplicate `proposal_id` exists in the input, the tie **MUST** be broken by
`candidate_key`. Sort keys **MUST** be fully discriminating: two rows that compare equal on the sort
key and differ in content make the file non-reproducible. The full, fully-discriminating sort key for
`violations.jsonl` is therefore `(proposal_id, code, json_pointer, candidate_key)`; for
`verdicts.jsonl` it is `(proposal_id, candidate_key)`. Two rows that compare equal on the full key
**MUST** be byte-identical.

**JSON formatting.** Every `.jsonl` line **MUST** be a single line of compact JSON with sorted keys
and no trailing whitespace; every record, **including the last**, **MUST** be terminated by exactly
one `\n`, and the file **MUST NOT** contain blank lines. Every `.json` file **MUST** end with exactly
one newline. Non-ASCII characters **MUST** be escaped, so that the artifacts are byte-stable
regardless of locale.

**Floats.** Every floating-point number written to a graded artifact **MUST** be rounded, at
serialisation, to a fixed number of significant digits which **MUST** be recorded in the manifest.
Do not write full-precision repr into a graded file and hope it matches; it will not. Choosing the
number of digits is a scientific decision and you are expected to justify it.

**Paths.** Every path appearing in an artifact **MUST** be relative to `--out`, **MUST NOT** contain
`..`, and **MUST** resolve inside `--out`. Absolute host paths **MUST NOT** appear anywhere in a
graded artifact.

## 5. Report and figures

`report.html` **MUST**:

- be a single file that renders correctly with no network access whatsoever. No CDN, no external
  stylesheet, no remote font, no remote image, no `<script src>` pointing outside the file. Assets
  are inlined or data-URI encoded. `report.html` **MUST NOT** contain any absolute `http://` or
  `https://` URL at all, including plain `<a href>` hyperlinks. A checker reading the file cannot
  tell a fetched asset from a hyperlink, so the rule is the strict one: cite a source by identifier
  (e.g. `mp-149`, a DOI string, a corpus entry id), never by link.
- present, at minimum: the run's identity and reference-data versions; the batch-level aggregates;
  a per-proposal table with status, reward, novelty tier and feasibility verdict; the top violation
  codes and the fields they attach to; and the stated caveats.
- contain nothing that is not derivable from the graded artifacts.

`figures/` **MUST** contain at least one figure that communicates the thermodynamic argument for at
least one proposal — a phase diagram, a chemical-potential diagram, or an equivalent — and at least
one figure that communicates a synthesis route.

**Every graded figure MUST ship a JSON companion** containing the data the figure draws, referenced
from the verdict's `artifacts[].data_companion`. The companion is what is read; the image is for
humans. Rendered images are not compared, because rendered images are not stable across font and
library versions, and a specification that pretends otherwise is a specification that fails on a
machine other than the author's.

Any layout algorithm with a random component **MUST** be seeded from `--seed`.

## 6. Determinism

Two runs of the same version of the image, with the same `--input`, `--config`, `--seed` and
`--now`, on the same machine, **MUST** produce byte-identical:

- `verdicts.jsonl`
- `violations.jsonl`
- `data_quality.json`
- every JSON figure companion
- `run_manifest.json`, **excluding** the single subtree `provenance.environment`

`provenance.environment` is the only exemption, and it exists so that wall-clock durations, host
names, process ids and library build details have somewhere honest to live. Anything nondeterministic
that appears anywhere else is a defect.

`--seed` fixes arbitrary choices only. Changing `--seed` **MAY** change figure layout and tie-broken
ordering among items the implementation treats as equivalent. It **MUST NOT** change
`verdict.status`, `novelty.tier`, `feasibility.verdict`, or any violation code raised — a conclusion
that depends on a pseudo-random draw must derive that draw from the proposal content, not from
`--seed`.

This **MUST** hold with `--max-workers 1` and with `--max-workers 8`.

The graded environment sets:

```
PYTHONHASHSEED=0   TZ=UTC   LC_ALL=C   SOURCE_DATE_EPOCH=<fixed>
OMP_NUM_THREADS=1  OPENBLAS_NUM_THREADS=1  MKL_NUM_THREADS=1  MPLBACKEND=Agg
```

Your program **MUST NOT** depend on these being set. They reduce the surface area; they are not the
mechanism. If your determinism relies on `PYTHONHASHSEED`, it is not determinism.

## 7. Offline behaviour

`--offline` is **fail-closed**. In offline mode:

- No socket connection to a remote host may be attempted. Not attempted-and-caught: not attempted.
  The enforcement **MUST** live at a single transport chokepoint, not be sprinkled across call sites.
- A lookup that cannot be answered from local data **MUST** be recorded as `unavailable`, not as a
  miss, and **MUST NOT** contribute to a conclusion.
- The run **MUST** still complete and produce a full artifact set.

Every outbound request **MUST** carry an explicit connect and read timeout. A request with no timeout
is an unbounded hang inside a batch, and a batch that hangs is a batch nobody sees the result of.

The distinction that matters, and that is checked:

| Evidence mode | A "not found" means |
|---|---|
| `snapshot` | The local corpus was searched and does not contain it. This is real evidence, bounded by the corpus's coverage. |
| `replay` | This particular question was never recorded. This is **not** evidence. |
| `live` | The remote source was asked and answered. |

Recording a replay miss as an absence of prior art is the most consequential error this system can
make, because it converts "we didn't look" into "it's novel".

A consequence worth stating plainly, because it changes how you should read your own runs. If your
submission answers prior-art questions from a replay or cassette provider, then every composition
your recording does not cover produces an `unavailable` query, and by §3 the run exits `4`. **For
such a submission, exit `4` is the ordinary, healthy outcome and exit `0` is the exception** — it
means your local evidence happened to cover the whole batch. Exit `4` on a fixture is not a symptom
to be tuned away, and the way to turn it into an exit `0` is to genuinely extend coverage, never to
downgrade an `unavailable` lookup into a miss. A submission that reports exit `0` on every batch is,
for that reason, more suspicious than one that reports `4`.

Without `--offline`, and with the network genuinely unavailable, the program **MUST NOT** exit `0`.

## 8. Container

The image **MUST**:

1. Build from a clean clone with a single `docker build` and no manual steps, no interactive prompts,
   and no host-specific configuration.
2. Install all Python dependencies into a **conda environment** created at build time from a
   committed `environment.yml`. That file **MUST** declare a `name:`, so the environment the
   entrypoint activates is the environment the file describes. Every dependency in that file **MUST**
   carry an explicit version. A solver that is free to pick a different version tomorrow is not a
   build.
3. Activate that environment for the entrypoint, such that `python` inside the container resolves
   inside the environment without the caller sourcing anything. That environment **MUST** be a named
   environment distinct from the installation's `base`/root environment: `sys.prefix` inside the
   final image **MUST NOT** be the conda/mamba root prefix (e.g. `/opt/conda`, `/opt/micromamba`).
   Installing into `base` works, and it also makes the pinned set indistinguishable from whatever the
   base image shipped.
4. Pin every `FROM` by `sha256:` digest, not by tag.
5. Use a multi-stage build such that the final image contains no compiler, no `curl`, no `wget`, no
   `nc`.
6. Declare a final `USER` with a **numeric, non-zero** uid.
7. Run correctly with `--read-only`, `--cap-drop ALL`, `--security-opt no-new-privileges=true`,
   `--network none`, and a memory limit.
8. Contain no credential in any layer, any `ENV`, or any build argument.
9. Not require `--privileged`, a relaxed seccomp or AppArmor profile, an added capability, or a
   mounted docker socket.
10. Contain a POSIX shell at `/bin/sh` plus `id`, `find`, `du` and `awk`; the graded container checks
    inspect the image through them, and an image they cannot enter is graded as failing.
11. Leave no directory containing application code or the conda environment writable by the final
    `USER`; copy application code owned by uid 0. Code the running process can rewrite is code an
    input can rewrite.
12. Retain no package-manager caches: no directory under the environment's package cache
    (`$(python -c 'import sys;print(sys.prefix)')/../pkgs`), `/var/cache/apt`, `/root/.cache`, or any
    `~/.cache` may exceed 50 MiB in the final image.
13. No file in the repository — including developer-convenience `Makefile`s, `justfile`s, compose
    files and shell scripts — may invoke the image with `--privileged`, an added capability, a
    relaxed seccomp or AppArmor profile, a mounted docker socket, or `--user root`. A hardened image
    with an unhardened convenience wrapper is an unhardened image, because the wrapper is what people
    actually run.
14. Run correctly on a **general-purpose CPU with two cores and 4 GiB of memory**, with no
    accelerator of any kind present. See §8.1.
15. Be no larger than **2 GiB uncompressed** — the on-disk rootfs, as reported by
    `docker image inspect --format '{{.Size}}'`.
16. Contain no GPU or accelerator runtime: no CUDA, ROCm, oneAPI or NVIDIA driver libraries, and no
    package whose purpose is to bind to one.

The graded invocation is:

```
docker run --rm \
  --user <uid>:<gid> --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev,size=512m \
  --cap-drop ALL --security-opt no-new-privileges=true \
  --network none --cpus 2 --memory 4g --memory-swap 4g --pids-limit 512 \
  -v "$PWD/in:/data/in:ro" -v "$PWD/out:/data/out:rw" \
  <image> run --input /data/in/<file> --out /data/out --offline --seed 12345 --now 2026-01-01T00:00:00Z
```

`<uid>` and `<gid>` are both the numeric uid declared by your final `USER`; the image **MUST NOT**
depend on any other group membership, nor on an `/etc/passwd` entry existing for that uid.

If your image does not run under exactly that, it does not run.

### 8.1 Resource budget

Crucible runs on ordinary hardware. It is a batch analyser that will be executed on laptops, on
small cloud instances, and inside CI containers, by people who do not have a GPU and will not
provision one to check a chemical formula. Treating compute as free is the easiest way to build
something that is technically correct and practically unusable.

This is a **hard requirement**, and it is checked. The numbers are generous on purpose. A competent
implementation of this system — pymatgen, a committed index rather than a raw corpus, no
accelerator — has been measured at well under 300 MiB of memory and a stripped image well under
half a gigabyte, so the table below leaves several times that in headroom. The limits are not here
to make the work hard; they are here to keep it honest, and each is set at a specific line.

| Constraint | Value | The line it draws |
|---|---|---|
| CPU | 2 cores. No specific instruction-set extension may be required. | The size of a standard CI runner. Parallelism is optional and must degrade to serial. |
| Memory | 4 GiB, including the interpreter and every loaded corpus. | Comfortable for a streamed or indexed corpus; forbids loading a multi-gigabyte raw dump whole. |
| Accelerator | None. Not present, not detected, not optional-but-preferred. | Forces CPU-only design. This is the primary constraint; the others mostly serve it. |
| Image size | ≤ 2 GiB uncompressed rootfs. | Admits pymatgen and the scientific-Python stack; excludes a bundled deep-learning framework and the CUDA stack. |
| Wall clock | ≤ 10 minutes for a batch of 20 proposals, under the constraints above. | A correctly indexed novelty check finishes in a minute or two; an all-pairs structural scan does not finish at all. |

The reason each number is where it is matters more than the number, so it is stated:

- **Memory is 4 GiB because a good corpus costs almost nothing and a bad one costs everything.** The
  scientific work here — parsing compositions, building a convex hull over a few thousand entries,
  matching structures — was measured at single-digit megabytes on top of the import footprint. What
  actually consumes gigabytes is loading a raw reference dump into memory whole: the common public
  corpora cost roughly five times their on-disk size when parsed naively, which runs to several
  gigabytes. The ceiling is set to leave a streamed or indexed corpus completely comfortable while
  making the naive whole-file load fail. If you are near this limit, the corpus is the reason.
- **The image ceiling is 2 GiB because that is the line between the scientific stack and the
  deep-learning stack.** A hardened multi-stage image carrying pymatgen was measured at a few
  hundred megabytes; a bundled CPU build of a machine-learned interatomic potential roughly triples
  that, and the CUDA build is several gigabytes. The ceiling admits the former and excludes the
  latter, which is deliberate: combined with the accelerator ban and the wall-clock budget, it makes
  a learned potential a poor trade rather than forbidding it outright.

Specifically, the implementation:

- **MUST** run to completion, and produce identical graded artifacts, under `--cpus 2 --memory 4g`.
  Determinism under the resource budget is part of the determinism guarantee in §6, not separate
  from it, and it **MUST** hold at tighter settings too: a one-core, one-gigabyte run must produce
  the same bytes, even if it is slower.
- **MUST NOT** require a GPU, TPU, or any other accelerator, and **MUST NOT** degrade into an error
  or an unavailable check merely because none is present. Code paths that would use one, if any
  exist, are optional and their absence is unremarkable — not a `VERIFY.UNAVAILABLE.*` finding, and
  the program **MUST NOT** probe for a device or select one.
- **MUST NOT** depend on a CUDA, ROCm or oneAPI runtime, an NVIDIA driver library, or a
  GPU-flavoured build of any framework. `environment.yml` and the lock file **MUST NOT** name
  `pytorch-cuda`, `cudatoolkit`, `cudnn`, `nccl`, `tensorflow-gpu`, any `nvidia-*` distribution, or
  a `jax[cuda]`-style extra.
- **MUST** keep peak memory bounded by the *configured limits* rather than by the size of the input:
  a batch ten times larger, or an input file at the byte budget, must not increase peak resident
  memory proportionally. Stream, chunk, index, or bound; do not load and hope.
- **MUST** bound its own concurrency. `--max-workers` defaults to a value sensible for two cores,
  and a larger value **MUST NOT** be required to meet the wall-clock budget.
- **SHOULD** be honest in `LIMITATIONS.md` about where it would stop scaling, and what it would cost
  to go further.

**Enforcement note.** Memory is enforced as a hard cgroup limit: the graded run is executed under
`docker run --memory=4g`, and a process the kernel kills for exceeding it (exit 137) has failed the
budget. The image ceiling is the uncompressed on-disk rootfs, not the compressed registry size, and
they can differ several-fold. Thread counts for the numeric stack are pinned in the graded
environment (§6) because an unpinned `OMP_NUM_THREADS` changes floating-point reduction order and
breaks byte-identity; do not unpin them for speed.

### 8.2 The packaging traps

Most of the ways a competent submission breaches the budget have nothing to do with its engineering
and everything to do with a packaging default. They are listed here so that a failure here is a
failure of attention, not of ability.

- **`pip install torch` pulls the CUDA build by default on Linux** — several gigabytes, an instant
  breach of the image ceiling, and a violation of §8.1 besides. If you use torch at all, install the
  CPU build explicitly (`--index-url https://download.pytorch.org/whl/cpu`, or the `pytorch-cpu`
  conda-forge package).
- **The conda `defaults` channel pulls MKL**, which adds a few hundred megabytes over the OpenBLAS
  build for no benefit here. Build against conda-forge with `--override-channels`, or require
  `nomkl`; the scientific stack is OpenBLAS by default there.
- **A single-stage conda image keeps the package cache and the installer base** and is roughly three
  times the size it needs to be. Use a multi-stage build: resolve the environment in a builder
  stage, copy only the environment into a slim final stage.
- **matplotlib builds its font cache on first import**, which takes the better part of a minute and
  makes the first run look hung. Build the cache into the image, or warm it before the timed path.
- **Importing the heavy stack eagerly** — matplotlib, plotly, pandas — on `--help` or on a trivial
  subcommand pays the full import cost every time. Import the heavy modules lazily, where they are
  used.
- **A raw reference corpus is usually too large to commit at all.** Common hosts reject files past
  roughly a hundred megabytes and warn well before that. Commit a compact index — a sorted table, a
  hash, a Bloom filter — that answers the novelty question in a few megabytes, and rebuild anything
  larger deterministically. See §7 and §12 of `PROJECT.md`; how you do this is one of the graded
  decisions.

**A large dependency is a design decision, not a detail.** Machine-learned interatomic potentials
and learned synthesizability priors are genuinely interesting here, and on CPU a single relaxation
can consume the entire wall-clock budget while a bundled framework consumes the entire image budget.
If you want one, it belongs behind an optional extra, with weights bundled or pre-cached, CPU-only
execution forced, single-point evaluation rather than relaxation, and the graded path working
identically without it. Noticing that it is the wrong tool under these constraints, and saying so, is
worth more than including it.

**Optimisation is not an excuse to become nondeterministic.** Every guarantee in §6 still holds
under the budget. Where the two appear to conflict — a faster reduction that changes summation
order, a thread pool that returns results as they finish — determinism wins, and the honest move is
to say so in `LIMITATIONS.md` rather than to quietly trade it away.

## 9. Input hardening

The input is produced by a language model. It is untrusted in the ordinary engineering sense: it may
be malformed, hostile, or merely wrong in ways that are invisible.

Before any semantic interpretation, the implementation **MUST** enforce, with configured and
documented bounds:

- a maximum input size in bytes, enforced **before** the document is handed to a parser;
- a maximum nesting depth, enforced by an explicit bound rather than by whatever the parser happens
  to do;
- maximum node count, string length, and array length;
- rejection of duplicate keys within a JSON object;
- rejection of non-finite numeric literals;
- rejection of timestamps without an explicit UTC offset.

Each of those bounds is a rule, and `data_quality.json` **MUST** carry one `rules[]` entry for each
of them — INCLUDING rules that fired zero times: at least one entry each for input byte budget,
nesting depth, node count, string length, array length, duplicate object keys, non-finite numeric
literals, and timezone-naive timestamps. A rule that only appears in the output when it triggers
cannot be used to demonstrate that a clean batch was actually checked.

And it **MUST NOT**:

- construct a filesystem path from any generator-supplied value without validating it against an
  anchored allowlist and confirming, after resolution, that the result is inside the intended
  directory;
- dereference any URL supplied in the input;
- evaluate, compile, execute, or deserialise any generator-supplied value as code;
- interpolate any generator-supplied value into a shell command;
- allow narrative text in the input to influence control flow.

**Chemical formulas specifically.** Element symbols are one or two characters, and many of them have
visually identical counterparts in other scripts. A formula **MUST** be normalised and then validated
against an anchored ASCII pattern, and each parsed symbol **MUST** be checked against a real
periodic table. Compatibility normalisation alone is not sufficient: it folds typographic subscripts
and fullwidth forms, and it leaves cross-script homoglyphs untouched. A formula containing a
non-ASCII element symbol **MUST** be rejected, not repaired — guessing which element was meant
produces a confident wrong answer, which is worse than no answer.

## 10. Secrets

No credential may appear in stdout, stderr, any artifact, any cached response, any figure, any
committed file, or any image layer. If an optional keyed provider is configured, the key is read from
the environment, held in a type whose string representation is masked, and redacted by a logging
filter installed before any other module logs.

In `config.resolved`, every value whose key contains `key`, `secret`, `token`, or `password` **MUST**
be the literal string `<redacted>` when a value was supplied and `<unset>` when it was not —
including name-only fields such as `api_key_env`. These exact spellings are checked, because a
redaction marker that varies per implementation cannot be distinguished from a leaked value.

A credential found anywhere in a submission ends the review.

## 11. Repository

The repository **MUST** contain, at its root:

```
README.md            how to build and run it, in the first screenful
CONTRACT.md          your copy of this file, unmodified
ASSUMPTIONS.md       §12 below
LIMITATIONS.md       §12 below
DECISIONS.md         §12 below
AI_USAGE.md          §12 below
Dockerfile
environment.yml
environment.lock.yml a lock or fully-resolved export of the conda environment; name it one of
                     conda-lock.yml, conda-lock.yaml, conda-linux-64.lock, environment.lock.yml,
                     environment-export.yml, or anything ending .lock
.github/workflows/   at least one workflow that builds the image and runs your tests
src/ or <package>/
tests/
```

## 12. Required documents

**`ASSUMPTIONS.md`** — every decision the specification deliberately left open, what you chose, and
why. What is graded is whether the assumption is stated, defensible, and actually reflected in the
code; not which option you picked. It **MUST** address each of the ten decisions in `PROJECT.md` §7
under its own heading that cites the decision number, for example `## §7.3 Formula equality`. The
heading requirement exists so that a reader — and a checker — can tell a document that engaged with
a decision from one that happened to use the same words elsewhere.

**`LIMITATIONS.md`** — what your implementation gets wrong, what it cannot do, and where its answers
should not be trusted. Be specific: a limitation that names a condition and a consequence is worth
more than a paragraph of hedging.

**`DECISIONS.md`** — the engineering narrative. What you built, what you cut and why, what you would
do with two more weeks in priority order.

**`AI_USAGE.md`** — which assistants and models you used, and roughly for what. Per component:
delegated, hand-written, drafted-then-revised, or reviewed-but-not-rewritten. **Two or three concrete
cases where the assistant was wrong**: what it produced, how you noticed, what you did. And anything
in the repository you could not explain line by line.

Using AI assistants is expected and is not penalised. Undisclosed use is disqualifying. A non-empty
answer to the last question is not penalised; an inaccurate empty one is.

## 13. History

`main` **MUST** be readable as a narrative, and **MUST** be bisectable: every commit builds, and
every commit passes the test suite present at that commit. Each commit should be independently
revertable. At least one commit message should be a full paragraph explaining a non-obvious decision
and the alternative you rejected.

Rebase and squash freely. What is reviewed is the history you chose to present, not a keystroke log.
Commit timestamps, cadence, and contribution-graph shape are not examined, because they are trivially
forgeable and examining them would only punish people who rebase properly.

## 14. Self-check

```
scripts/selfcheck.sh
```

Run it before you submit. Everything it checks is checked again at review time, and there is no
credit for discovering these at review time rather than yours.
