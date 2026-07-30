"""
Per-record conformance against proposals.schema.json (CONTRACT.md §9).

Envelope validation admits the *document*. This module admits, or refuses, each
individual proposal inside it. The distinction matters: one bad record inside a
good envelope is never an envelope failure, and a run that returns exit 3
because a single proposal had a bad formula has thrown away nine good verdicts.

Three findings come out of here, all from the core taxonomy, all gate-level,
because when the generator and the verifier disagree about the input contract
nothing downstream about that record is trustworthy:

    FMT.SCHEMA.UNKNOWN_FIELD          an undeclared field is present
    FMT.SCHEMA.REQUIRED_FIELD_MISSING a required field is absent
    FMT.SCHEMA.TYPE_MISMATCH          wrong type, or a violated constraint
                                      (pattern, enum, minimum, maxLength, ...)

The third is the one that catches a `proposal_id` of `../../etc/passwd`. Its
declared pattern exists precisely so that value never reaches the part of the
system that decides where to write a file; if it does reach that part, the
pattern was decoration.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schemas", "proposals.schema.json")

# Anything outside this set is replaced when a value is used to build a path.
# Applied after validation as a second, independent barrier: the pattern check
# above should already have refused a hostile id, and this exists so that a hole
# in the first barrier is still not a path traversal.
_UNSAFE_PATH_CHARS = re.compile(r"[^A-Za-z0-9._-]")


@dataclass
class RecordIssue:
    code: str
    json_pointer: str
    message: str
    observed: object | None = None
    expected: object | None = None


@dataclass
class RecordCheckResult:
    conforms: bool
    issues: list[RecordIssue] = field(default_factory=list)

    @property
    def codes(self) -> list[str]:
        return sorted({i.code for i in self.issues})


def _load_proposal_schema() -> dict:
    """The `proposal` subschema, self-contained so it can validate one record.

    $defs is carried across because the proposal definition references its
    siblings; $id is dropped so the validator resolves those references locally
    rather than attempting to fetch the published URL. A graded run has no
    network, and a schema that only validates online is not a schema.
    """
    with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
        full = json.load(fh)
    return {
        "$schema": full["$schema"],
        "$defs": full["$defs"],
        **{k: v for k, v in full["$defs"]["proposal"].items()},
    }


_PROPOSAL_SCHEMA: dict | None = None


def _proposal_schema() -> dict:
    global _PROPOSAL_SCHEMA
    if _PROPOSAL_SCHEMA is None:
        _PROPOSAL_SCHEMA = _load_proposal_schema()
    return _PROPOSAL_SCHEMA


def _pointer(parts) -> str:
    """RFC 6901 pointer from a jsonschema absolute_path deque."""
    if not parts:
        return ""
    escaped = [str(p).replace("~", "~0").replace("/", "~1") for p in parts]
    return "/" + "/".join(escaped)


def _classify(err) -> str:
    """Map one validation error onto a core taxonomy code.

    FMT.SCHEMA.TYPE_MISMATCH deliberately covers constraint violations as well
    as type errors -- the taxonomy says so in its guidance, and reaching for an
    X.* extension for a condition the core already names would fragment the
    vocabulary for no gain.
    """
    if err.validator == "additionalProperties":
        return "FMT.SCHEMA.UNKNOWN_FIELD"
    if err.validator == "required":
        return "FMT.SCHEMA.REQUIRED_FIELD_MISSING"
    return "FMT.SCHEMA.TYPE_MISMATCH"


def _unknown_field_pointer(err) -> str:
    """additionalProperties errors name the offending key in the message rather
    than in the path, so the pointer has to be reconstructed to be useful. A
    finding that points at the object instead of the field is not per-field
    attribution."""
    base = _pointer(err.absolute_path)
    match = re.search(r"'([^']+)' was unexpected", err.message)
    if match:
        key = match.group(1).replace("~", "~0").replace("/", "~1")
        return f"{base}/{key}"
    return base


def check_record(proposal: object) -> RecordCheckResult:
    """Validate one proposal object against the input contract."""
    try:
        from jsonschema import Draft202012Validator
    except ImportError:  # pragma: no cover - jsonschema is a pinned dependency
        return RecordCheckResult(conforms=True, issues=[])

    if not isinstance(proposal, dict):
        return RecordCheckResult(False, [RecordIssue(
            "FMT.SCHEMA.TYPE_MISMATCH", "",
            "Proposal entry is not a JSON object.",
            observed=type(proposal).__name__, expected="object",
        )])

    validator = Draft202012Validator(_proposal_schema())
    issues: list[RecordIssue] = []

    for err in validator.iter_errors(proposal):
        code = _classify(err)
        pointer = _unknown_field_pointer(err) if code == "FMT.SCHEMA.UNKNOWN_FIELD" \
            else _pointer(err.absolute_path)

        observed = err.instance
        if isinstance(observed, (dict, list)):
            observed = json.dumps(observed, sort_keys=True)[:200]
        elif isinstance(observed, str):
            observed = observed[:200]
        elif not isinstance(observed, (int, float, bool, type(None))):
            observed = str(observed)[:200]

        expected = None
        if err.validator in ("pattern", "enum", "const", "type", "minimum",
                             "maximum", "maxLength", "minLength", "maxItems",
                             "minItems", "required"):
            expected = json.dumps(err.validator_value)[:200]

        issues.append(RecordIssue(
            code=code, json_pointer=pointer,
            message=err.message[:900], observed=observed, expected=expected,
        ))

    # Deterministic order: the same defect must produce the same rows in the
    # same sequence on every run, and iter_errors does not promise that.
    issues.sort(key=lambda i: (i.json_pointer, i.code, str(i.message)))
    return RecordCheckResult(conforms=not issues, issues=issues)


def safe_slug(value: str, *, fallback: str = "record") -> str:
    """A filesystem-safe token derived from an untrusted value.

    Never used to *recover* a hostile identifier -- only to name an artifact
    without letting a generator-supplied string choose where it lands. The
    result is always a single path component: no separators, no `..`, bounded
    length, and never empty.
    """
    if not isinstance(value, str) or not value:
        return fallback
    cleaned = _UNSAFE_PATH_CHARS.sub("_", value)
    # Collapse every run of dots. A single leading strip is not enough: `..` can
    # survive in the middle of an otherwise-sanitised name, and a name that
    # still literally contains `..` invites the next reader to assume the
    # traversal risk was handled when only the separators were.
    cleaned = re.sub(r"\.{2,}", "_", cleaned).strip(".")
    cleaned = cleaned.strip("_") or fallback
    return cleaned[:64] or fallback
