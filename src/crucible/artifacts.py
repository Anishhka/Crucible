"""
Artifact construction and serialisation (CONTRACT.md §4).

Everything written to `--out` is produced here. Three properties are enforced in
this module and nowhere else, so there is one place to check them:

  * **Byte-stability.** Compact JSON, sorted keys, non-ASCII escaped, exactly one
    trailing newline, no blank lines. Escaping non-ASCII makes the artifacts
    byte-identical regardless of the locale the run happened in.

  * **Float rounding at serialisation.** Every float written to a graded artifact
    is rounded to a fixed number of significant digits, and that number is
    recorded in the manifest. Writing full-precision repr into a graded file and
    hoping it matches does not work; it will differ across platforms in the last
    bits and break byte-identity.

  * **Ordering.** verdicts.jsonl sorts by (proposal_id, candidate_key);
    violations.jsonl by (proposal_id, code, json_pointer, candidate_key). Both
    keys are fully discriminating: two rows that compare equal on the full key
    must be byte-identical, or the file is not reproducible.

The canonical record is the verdict. Every other artifact is a pure projection
of the verdict set -- regenerable from it, containing no fact that is not in it.
That is not architectural preference; it is what makes the outputs auditable and
re-weightable without re-running verification.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from typing import Any

CRUCIBLE_VERSION = "1.0.0"
TAXONOMY_VERSION = "1.0.0"
RECORD_SCHEMA_VERSION = "1-0-0"


# --------------------------------------------------------------------------- #
# hashing and serialisation
# --------------------------------------------------------------------------- #

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_tree_sha256() -> str:
    """A content hash of the code that produced a run.

    Covers every `.py` in the package plus the committed schemas and taxonomy --
    i.e. everything whose contents can move a graded number. The reference
    corpora are deliberately excluded: they are hashed individually in
    `run_manifest.reference_data`, where a reader can tell *which* corpus moved
    rather than only that something did.

    Paths are relativised to the package directory and sorted before hashing, so
    the value is identical whether the tree lives at `src/crucible` on a laptop
    or `/app/src/crucible` in the image. `__pycache__` is skipped, because a
    compiled artifact is not source and its presence must not change the hash.

    This is what makes `crucible lineage` able to answer "is the code in front of
    me the code that produced this output".
    """
    package_dir = os.path.dirname(os.path.abspath(__file__))
    digest = hashlib.sha256()

    interesting: list[tuple[str, str]] = []
    for root, dirs, files in os.walk(package_dir):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__" and d != "reference_data")
        for name in sorted(files):
            if not (name.endswith(".py") or name.endswith(".json")):
                continue
            absolute = os.path.join(root, name)
            relative = os.path.relpath(absolute, package_dir).replace(os.sep, "/")
            interesting.append((relative, absolute))

    for relative, absolute in sorted(interesting):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        with open(absolute, "rb") as fh:
            # Normalise line endings so a CRLF checkout and an LF checkout of
            # identical source agree. .gitattributes forces LF, but a hash that
            # depends on that holding is a hash that breaks quietly when it does
            # not.
            digest.update(fh.read().replace(b"\r\n", b"\n"))
        digest.update(b"\0")

    return digest.hexdigest()


def git_provenance() -> tuple[str | None, bool | None]:
    """The commit this ran from, when that is knowable.

    Read from the environment first so CI can stamp it into the image, then from
    a working `.git` for a developer run. In a built container neither exists and
    both fields are null -- which is honest: the image carries no repository, and
    `tool_versions.crucible_source_sha256` is the identifier that survives.
    """
    commit = os.environ.get("CRUCIBLE_GIT_COMMIT")
    dirty_env = os.environ.get("CRUCIBLE_GIT_DIRTY")
    if commit:
        dirty = None if dirty_env is None else dirty_env.strip().lower() in ("1", "true", "yes")
        return commit.strip()[:64], dirty

    import subprocess
    package_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=package_dir, capture_output=True,
            text=True, timeout=5).stdout.strip()
        if not commit:
            return None, None
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=package_dir, capture_output=True,
            text=True, timeout=5).stdout
        return commit[:64], bool(status.strip())
    except (OSError, subprocess.SubprocessError):
        return None, None


def canonical_json(obj: Any) -> str:
    """RFC 8785-style canonicalisation, close enough for a content hash.

    Sorted keys, no whitespace, shortest round-trip numbers. Python sorts keys by
    code point where JCS sorts by UTF-16 code unit; the two agree for everything
    outside the supplementary planes, and a chemical proposal containing an
    astral-plane object key has already failed the ASCII checks upstream.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def candidate_key(proposal: dict) -> str:
    """Content hash of a proposal. Two byte-different but semantically identical
    proposals produce the same key, which is how the same material is recognised
    across batches."""
    return "sha256:" + sha256_bytes(canonical_json(proposal).encode("utf-8"))


def fingerprint(code: str, json_pointer: str, observed: Any) -> str:
    """Stable across runs for the same finding on the same input, so that
    'is this a new problem or the same one' is answerable."""
    payload = canonical_json({"code": code, "json_pointer": json_pointer,
                              "observed": _normalise_observed(observed)})
    return "sha256:" + sha256_bytes(payload.encode("utf-8"))


def round_significant(value: float, digits: int) -> float:
    """Round to a fixed number of significant digits.

    Significant rather than decimal places: a lattice parameter of 4.5937 and an
    energy of 0.00012 need the same relative precision, and rounding both to six
    decimals would keep noise in one and destroy the other.
    """
    if not math.isfinite(value) or value == 0.0:
        return 0.0 if value == 0.0 else value
    magnitude = math.floor(math.log10(abs(value)))
    factor = digits - 1 - magnitude
    rounded = round(value, factor)
    # round() can return -0.0, which serialises as "-0.0" and differs from "0.0".
    return rounded + 0.0 if rounded != 0 else 0.0


def round_floats(obj: Any, digits: int) -> Any:
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        return round_significant(obj, digits)
    if isinstance(obj, dict):
        return {k: round_floats(v, digits) for k, v in obj.items()}
    if isinstance(obj, list):
        return [round_floats(v, digits) for v in obj]
    return obj


def _dump(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def write_json(path: str, obj: Any, *, digits: int = 6) -> tuple[str, int]:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    raw = (_dump(round_floats(obj, digits)) + "\n").encode("utf-8")
    with open(path, "wb") as fh:
        fh.write(raw)
    return sha256_bytes(raw), len(raw)


def write_jsonl(path: str, rows: list[dict], *, digits: int = 6) -> tuple[str, int]:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    lines = [_dump(round_floats(row, digits)) for row in rows]
    raw = ("".join(line + "\n" for line in lines)).encode("utf-8")
    with open(path, "wb") as fh:
        fh.write(raw)
    return sha256_bytes(raw), len(raw)


def write_text(path: str, text: str) -> tuple[str, int]:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    raw = text.encode("utf-8")
    with open(path, "wb") as fh:
        fh.write(raw)
    return sha256_bytes(raw), len(raw)


# --------------------------------------------------------------------------- #
# ordering
# --------------------------------------------------------------------------- #

def _byte_key(text: str) -> bytes:
    """Byte-wise ordering of the UTF-8 encoding, as the contract specifies.
    Python's default string comparison is by code point, which differs from
    UTF-8 byte order for characters outside the BMP."""
    return text.encode("utf-8")


def sort_verdicts(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (_byte_key(r["proposal_id"]),
                                       _byte_key(r["candidate_key"])))


def sort_violations(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (_byte_key(r["proposal_id"]),
                                       _byte_key(r["code"]),
                                       _byte_key(r["json_pointer"]),
                                       _byte_key(r["candidate_key"])))


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _normalise_observed(value: Any) -> Any:
    """Coerce an observed/expected value into what the violation schema allows:
    string, number, boolean or null."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:2000]
    return canonical_json(value)[:2000]


def truncate(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    return text if len(text) <= limit else text[:limit - 1] + "…"


def make_violation(code: str, level: str, json_pointer: str, message: str,
                   observed: Any = None, expected: Any = None,
                   confidence: float | None = None,
                   unverifiable: bool = False) -> dict:
    entry: dict = {
        "code": code,
        "level": level,
        "json_pointer": json_pointer,
        "message": truncate(message, 1000),
        "fingerprint": fingerprint(code, json_pointer, observed),
    }
    if observed is not None:
        entry["observed"] = _normalise_observed(observed)
    if expected is not None:
        entry["expected"] = _normalise_observed(expected)
    if confidence is not None:
        entry["confidence"] = confidence
    if unverifiable:
        entry["unverifiable"] = True
    return entry


# --------------------------------------------------------------------------- #
# aggregates
# --------------------------------------------------------------------------- #

def build_aggregates(verdicts: list[dict], violations: list[dict]) -> dict:
    """Batch-level signal. `code_by_field` is the point of the exercise: it is
    the difference between '40% of proposals were bad' and 'the generator gets
    the oxygen stoichiometry wrong in 40% of perovskites'."""
    code_histogram: dict[str, int] = {}
    field_histogram: dict[str, int] = {}
    code_by_field: dict[str, int] = {}

    for row in violations:
        code, pointer = row["code"], row["json_pointer"]
        code_histogram[code] = code_histogram.get(code, 0) + 1
        field_histogram[pointer] = field_histogram.get(pointer, 0) + 1
        combined = f"{code}|{pointer}"
        code_by_field[combined] = code_by_field.get(combined, 0) + 1

    aggregates: dict = {
        "code_histogram": dict(sorted(code_histogram.items())),
        "field_histogram": dict(sorted(field_histogram.items())),
        "code_by_field": dict(sorted(code_by_field.items())),
    }

    tiers: dict[str, int] = {}
    for record in verdicts:
        tier = (record.get("novelty") or {}).get("tier")
        if tier:
            tiers[tier] = tiers.get(tier, 0) + 1
    if tiers:
        aggregates["novelty_tier_histogram"] = dict(sorted(tiers.items()))

    # aggregates.reward is omitted entirely when there are no verdicts: a mean
    # over zero records is not a number, and inventing a zero would be a claim.
    if verdicts:
        rewards = sorted(record["verdict"]["reward"] for record in verdicts)
        component_totals: dict[str, list[float]] = {}
        for record in verdicts:
            for name, component in record["verdict"]["reward_components"].items():
                component_totals.setdefault(name, []).append(component["value"])
        aggregates["reward"] = {
            "mean": sum(rewards) / len(rewards),
            "min": rewards[0],
            "max": rewards[-1],
            "p50": rewards[len(rewards) // 2],
            "component_means": {
                name: sum(values) / len(values)
                for name, values in sorted(component_totals.items())
            },
        }

    calibration = build_calibration_summary(verdicts)
    aggregates["calibration"] = calibration
    return aggregates


def build_calibration_summary(verdicts: list[dict]) -> dict | None:
    """How well the generator's self-reported confidence tracked reality.

    "The model is most confident exactly where it is most wrong" is among the
    most useful things you can tell someone maintaining it, and the generator
    hands us the raw material for free.
    """
    pairs = [pair for record in verdicts
             for pair in (record.get("calibration") or {}).get("pairs", [])]
    if not pairs:
        return None

    scored = [p for p in pairs if p["outcome"] in ("correct", "incorrect")]
    if not scored:
        return {"n_pairs": len(pairs), "brier_score": None,
                "mean_confidence_when_correct": None,
                "mean_confidence_when_incorrect": None,
                "method": "No pair reached a terminal outcome, so no score is defined."}

    correct = [p["self_confidence"] for p in scored if p["outcome"] == "correct"]
    incorrect = [p["self_confidence"] for p in scored if p["outcome"] == "incorrect"]
    brier = sum((p["self_confidence"] - (1.0 if p["outcome"] == "correct" else 0.0)) ** 2
                for p in scored) / len(scored)

    return {
        "n_pairs": len(pairs),
        "brier_score": brier,
        "mean_confidence_when_correct": (sum(correct) / len(correct)) if correct else None,
        "mean_confidence_when_incorrect": (sum(incorrect) / len(incorrect)) if incorrect else None,
        "method": (
            "Brier score over the generator's per-field confidences, scored against "
            "whether an error-level finding attached to that field. Unverifiable "
            "pairs are excluded: they measure our coverage, not its calibration."
        ),
    }


# --------------------------------------------------------------------------- #
# recommendations
# --------------------------------------------------------------------------- #

# code -> (what should change, statement template). A recommendation with no
# supporting count is an opinion and does not belong in a generated artifact,
# so every one of these is emitted only with its evidence attached.
_RECOMMENDATION_RULES: list[tuple[tuple[str, ...], str, str]] = [
    (("FMT.ENCODING.NON_ASCII_SYMBOL", "FMT.ENCODING.INVISIBLE_CHARACTER"),
     "decoding",
     "Constrain decoding to ASCII for the composition.formula field. Homoglyph "
     "and zero-width characters render identically to the intended symbol, "
     "compare unequal, and silently miss every database lookup -- which produces "
     "a confident report of novelty for a material that is already known."),
    (("FMT.SCHEMA.UNKNOWN_FIELD", "FMT.SCHEMA.REQUIRED_FIELD_MISSING",
      "FMT.SCHEMA.TYPE_MISMATCH"),
     "output_schema",
     "Enforce the proposal schema at generation time rather than downstream. "
     "Records that violate the declared contract cannot be scored at all, so "
     "every one of them is a wasted generation."),
    (("CHEM.FORMULA.UNKNOWN_ELEMENT", "CHEM.FORMULA.UNPARSEABLE"),
     "prompt",
     "Require the generator to emit compositions as an explicit element -> "
     "coefficient map validated against a periodic table, instead of as a free "
     "string. A symbol that is well-formed in shape is not necessarily an "
     "element."),
    (("CHEM.FORMULA.INCONSISTENT_WITH_ELEMENTS",),
     "output_schema",
     "Emit the composition once, not twice. When the formula string and the "
     "elements map disagree, only one can describe the intended material and "
     "nothing downstream can tell which."),
    (("CLAIM.UNITS.INCONSISTENT",),
     "prompt",
     "Pin one canonical unit per property in the prompt and reject anything "
     "else. Unit drift produces values wrong by exactly three orders of "
     "magnitude that remain entirely plausible on inspection."),
    (("CLAIM.SENTINEL.SUSPECTED",),
     "training_data",
     "Filter placeholder magnitudes out of the training data. Values such as "
     "-999 pass every type and range check that was not written with "
     "placeholders in mind, so they survive into the output as measurements."),
    (("CLAIM.VALUE.PHYSICALLY_IMPOSSIBLE",),
     "prompt",
     "State the physical domain of each property in the prompt. A negative band "
     "gap is not a small band gap, and no amount of uncertainty makes it one."),
    (("STRUCT.GEOM.ATOM_OVERLAP", "STRUCT.GEOM.IMPLAUSIBLE_LATTICE",
      "STRUCT.GEOM.IMPLAUSIBLE_DENSITY", "STRUCT.SITES.OCCUPANCY_INCONSISTENT"),
     "training_data",
     "Add a geometry validity filter to the generation loop. Coordinates that "
     "place atoms below a bonding distance apart, or a lattice quoted in the "
     "wrong unit, are cheap to detect at generation time and expensive to "
     "discover afterwards."),
    (("STRUCT.SYMMETRY.SPACEGROUP_INCONSISTENT",),
     "tooling",
     "Determine the space group from the coordinates instead of asserting one. "
     "An asserted symmetry that the geometry does not have is a defect the "
     "generator can avoid entirely by not asserting it."),
    (("NOVEL.DUPLICATE.EXACT_MATCH",),
     "prompt",
     "Give the generator the list of compositions already reported for the "
     "target chemical system. A large fraction of this batch re-proposes "
     "materials that were characterised decades ago."),
    (("FEAS.SYNTH.HINT_INCOHERENT",),
     "prompt",
     "Require the proposed atmosphere to be justified against the target "
     "chemistry. An oxide calcined under a reducing atmosphere is a route that "
     "destroys the material it is meant to make."),
    (("CHEM.SCOPE.NOT_INORGANIC_CRYSTALLINE",),
     "prompt",
     "Restate the scope constraint in the prompt. Molecular and polymeric "
     "species cannot be evaluated by this verifier at all, so generating them "
     "consumes budget and returns nothing."),
    (("FEAS.FRAME.INAPPROPRIATE",),
     "prompt",
     "Ask the generator to state the conditions its stability claim applies to. "
     "A claim carried over from a zero-kelvin closed-system calculation does not "
     "survive contact with a furnace at 1200 K in flowing air."),
]


def build_recommendations(verdicts: list[dict], violations: list[dict]) -> list[dict]:
    """Prioritised, evidence-backed changes to the generator."""
    total = len(verdicts) or 1

    records_by_code: dict[str, set[str]] = {}
    pointers_by_code: dict[str, set[str]] = {}
    examples_by_code: dict[str, set[str]] = {}
    for row in violations:
        records_by_code.setdefault(row["code"], set()).add(row["record_id"])
        pointers_by_code.setdefault(row["code"], set()).add(row["json_pointer"])
        examples_by_code.setdefault(row["code"], set()).add(row["proposal_id"])

    scored: list[tuple[int, dict]] = []
    for codes, target, statement in _RECOMMENDATION_RULES:
        affected: set[str] = set()
        pointers: set[str] = set()
        examples: set[str] = set()
        present = []
        for code in codes:
            if code in records_by_code:
                present.append(code)
                affected |= records_by_code[code]
                pointers |= pointers_by_code.get(code, set())
                examples |= examples_by_code.get(code, set())
        if not affected:
            continue
        scored.append((len(affected), {
            "target": target,
            "statement": statement,
            "evidence": {
                "codes": sorted(present),
                "json_pointers": sorted(pointers)[:20],
                # DISTINCT records, not findings: a code firing twice on one
                # record counts once.
                "n_affected": len(affected),
                "fraction_affected": len(affected) / total,
                "example_proposal_ids": sorted(examples)[:5],
            },
        }))

    scored.sort(key=lambda item: (-item[0], item[1]["target"], item[1]["statement"]))

    recommendations = []
    for priority, (_, payload) in enumerate(scored, start=1):
        payload["id"] = f"rec-{priority:03d}"
        payload["priority"] = priority
        payload["expected_effect"] = (
            f"Removing this failure mode would change the disposition of "
            f"{payload['evidence']['n_affected']} of {total} records in this batch."
        )
        payload["confidence"] = 0.6
        recommendations.append(payload)
    return recommendations


def build_caveats(verdicts: list[dict], reference_rows: list[dict],
                  unavailable_count: int) -> list[str]:
    """What this bundle cannot tell you. Never empty: a verifier built on
    positive-only reference data has systematic blind spots, and one that does
    not name its own is overclaiming."""
    caveats = [
        "Reference corpora are built from what people chose to publish, so they "
        "contain almost no negative examples: nothing in this run can tell you "
        "that a composition was tried and failed, only that it was or was not "
        "reported.",
        "This build computes no convex hull, no formation energy and no energy "
        "above hull, so every feasibility verdict rests on composition-level "
        "screening alone and no proposal is ever reported as thermodynamically "
        "plausible.",
        "Charge-neutrality screening is known to reject roughly one in six real "
        "reference structures, concentrated in alloys, mixed-valence compounds "
        "and f-block chemistry, so a CHEM.CHARGE.NOT_NEUTRAL warning is a prompt "
        "for review rather than evidence that the material cannot exist.",
        "Space groups are checked only for consistency between the asserted "
        "group's crystal family and the lattice metric; no symmetry "
        "determination is performed from the coordinates, so a structure with "
        "the right metric and wrong internal symmetry passes unremarked.",
        "No claimed property is checked against a corpus of measured values, so "
        "a claim assessed as 'unsupported' means only that this build has "
        "nothing to compare it against, never that the value is wrong.",
    ]

    total_entries = sum(row.get("n_entries") or 0 for row in reference_rows)
    caveats.append(
        f"Prior-art coverage is {total_entries} hand-curated compositions across "
        f"{len(reference_rows)} corpora, which is a vanishing fraction of the "
        f"reported inorganic literature; a `no_evidence` tier means absence from "
        f"these specific lists at their pinned versions and must not be read as "
        f"a novelty claim about the literature at large."
    )

    if unavailable_count:
        caveats.append(
            f"{unavailable_count} evidence lookup(s) in this run could not be "
            f"answered at all and are recorded as unavailable; those records were "
            f"excluded from every training projection and their novelty tiers are "
            f"`unknown` rather than negative."
        )

    return caveats
