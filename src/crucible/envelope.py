"""
Envelope-level hardening for Crucible input (CONTRACT.md §9, PROJECT.md §5.1).

This module answers exactly one question: can this input document be admitted at
all? It never inspects proposal *content* for scientific plausibility -- that is
the pipeline's job, done later, per record. It decides only between "admit the
document" and "exit 3, nothing was processed".

The discriminating behaviour throughout is **detect, quarantine, count, report**
-- never coerce and continue. A pipeline that silently repairs bad input
launders a defect into a result, which is worse than crashing, because a crash
is visible.

Two enforcement details are load-bearing:

  * The byte budget is checked on the raw file size *before* the document is
    handed to a parser. A budget checked after parsing has already lost: the
    memory is allocated by then.

  * Nesting depth is measured by scanning the raw character stream, also before
    parsing. The obvious alternative -- let the parser blow its stack and catch
    the RecursionError -- is not enforcement. The taxonomy says so explicitly:
    different parsers on different runtimes fail differently, or not at all, so
    the bound has to be ours.

Every code raised here comes from taxonomy/violations.core.v1.json. Inventing a
parallel vocabulary makes historical rows uninterpretable, which is the whole
reason the taxonomy is versioned.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

# RFC 3339 with a mandatory explicit offset: 'Z' or +HH:MM / -HH:MM. A value
# that stops at the seconds is timezone-naive and is not trusted.
_RFC3339_WITH_OFFSET = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$"
)
_TIMESTAMP_SHAPED = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}")

# Fields whose value is a timestamp by contract. Checked by name rather than by
# sniffing every string, so a formula that happens to look like a date is not
# reinterpreted as one.
TIMESTAMP_FIELDS = ("created_at", "retrieved_at", "timestamp")


class EnvelopeRejected(Exception):
    """The input cannot be admitted. Carries the taxonomy code and the exact
    reason needed for the exit-3 manifest record."""

    def __init__(self, violation_code: str, reason: str, *, rule_id: str | None = None):
        self.violation_code = violation_code
        self.reason = reason
        self.rule_id = rule_id
        super().__init__(reason)


@dataclass
class RuleHit:
    rule_id: str
    description: str
    action: str  # quarantine | reject_field | coerce | flag
    violation_code: str | None = None
    n_triggered: int = 0
    examples: list[str] = field(default_factory=list)


@dataclass
class EnvelopeReport:
    """Accounting produced whether or not the envelope was admitted.

    `data_quality.rules[]` is built from this. Every rule is registered up front
    so that a clean input still *proves* the rule was checked, rather than the
    rule merely being absent from the output. A rule that only appears when it
    fires cannot be used to demonstrate anything.
    """

    rules: dict[str, RuleHit] = field(default_factory=dict)
    bytes_read: int = 0
    totals: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        defaults = [
            ("input.byte_budget",
             "Raw input size does not exceed the configured byte budget, checked "
             "on the byte stream before any parse is attempted.",
             "reject_field", "FMT.JSON.SIZE_EXCEEDED"),
            ("input.nesting_depth",
             "JSON nesting depth does not exceed the configured maximum, measured "
             "by scanning the raw stream rather than by catching a parser error.",
             "reject_field", "FMT.JSON.DEPTH_EXCEEDED"),
            ("input.node_count",
             "Total JSON node count does not exceed the configured maximum.",
             "reject_field", "FMT.JSON.SIZE_EXCEEDED"),
            ("input.string_length",
             "No string value exceeds the configured maximum length.",
             "quarantine", "FMT.SCHEMA.TYPE_MISMATCH"),
            ("input.array_length",
             "No array exceeds the configured maximum length.",
             "quarantine", "FMT.SCHEMA.TYPE_MISMATCH"),
            ("input.duplicate_keys",
             "No JSON object contains a repeated key. Default decoding keeps the "
             "last occurrence, which lets one payload show two different values.",
             "reject_field", "FMT.JSON.DUPLICATE_KEY"),
            ("input.non_finite_numbers",
             "No numeric literal is NaN, Infinity or -Infinity. These are not "
             "valid JSON but common decoders accept them by default.",
             "reject_field", "FMT.JSON.NON_FINITE_NUMBER"),
            ("input.timezone_naive_timestamps",
             "Every timestamp carries an explicit UTC offset. A naive timestamp "
             "is not trusted and the field is refused rather than assumed to be UTC.",
             "reject_field", "FMT.TIME.NAIVE_TIMESTAMP"),
            ("input.schema_version",
             "The batch's schema_version MODEL component is one this build implements.",
             "reject_field", "FMT.SCHEMA.VERSION_UNSUPPORTED"),
            ("input.json_parseable",
             "The input is syntactically valid JSON.",
             "reject_field", "FMT.JSON.UNPARSEABLE"),
            ("record.schema_conformance",
             "Every proposal conforms to proposals.schema.json: no undeclared "
             "field, no missing required field, no constraint violation.",
             "quarantine", "FMT.SCHEMA.TYPE_MISMATCH"),
            ("record.duplicate_proposal_id",
             "No proposal_id repeats within a batch. A duplicate is a defect in "
             "the generator, not an instruction to merge or overwrite.",
             "quarantine", "FMT.ID.DUPLICATE_PROPOSAL_ID"),
            ("claims.unit_normalization",
             "Every claimed property's unit is recognised and converted to the "
             "canonical unit for its quantity, with the factor recorded.",
             "coerce", "CLAIM.UNITS.INCONSISTENT"),
            ("claims.sentinel_values",
             "No claimed value matches a placeholder convention (-999, 9999, -1 "
             "and similar repdigit or round-negative magnitudes).",
             "flag", "CLAIM.SENTINEL.SUSPECTED"),
        ]
        for rule_id, desc, action, code in defaults:
            self.rules[rule_id] = RuleHit(rule_id, desc, action, code, 0, [])

    def hit(self, rule_id: str, n: int = 1, example: str | None = None) -> None:
        rule = self.rules[rule_id]
        rule.n_triggered += n
        if example and len(rule.examples) < 5 and example not in rule.examples:
            rule.examples.append(example)

    def as_list(self) -> list[dict]:
        out = []
        for r in sorted(self.rules.values(), key=lambda x: x.rule_id):
            entry = {
                "rule_id": r.rule_id,
                "description": r.description,
                "action": r.action,
                "n_triggered": r.n_triggered,
                "violation_code": r.violation_code,
            }
            if r.examples:
                entry["examples"] = sorted(r.examples)
            out.append(entry)
        return out


# --------------------------------------------------------------------------- #
# pre-parse scanning
# --------------------------------------------------------------------------- #

def scan_depth(text: str, max_depth: int) -> int:
    """Maximum nesting depth of a JSON document, from the raw characters.

    String literals are skipped so that a brace inside a formula or a rationale
    cannot inflate the count, and escape sequences are honoured so that a
    trailing backslash cannot escape the closing quote. Iterative, so measuring
    the depth of a hostile document cannot itself overflow the stack.

    Raises as soon as the bound is crossed: an input nested a million deep is
    rejected after max_depth+1 characters, not after a million.
    """
    depth = 0
    deepest = 0
    in_string = False
    escaped = False

    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
            if depth > deepest:
                deepest = depth
                if depth > max_depth:
                    raise EnvelopeRejected(
                        "FMT.JSON.DEPTH_EXCEEDED",
                        f"JSON nesting depth exceeds the configured maximum of "
                        f"{max_depth}, measured on the raw stream before parsing.",
                        rule_id="input.nesting_depth",
                    )
        elif ch in "}]":
            depth -= 1
    return deepest


def _reject_non_finite(token: str):
    # json.load's parse_constant hook fires for exactly the NaN / Infinity /
    # -Infinity tokens. Refusing to return a value is what makes them
    # unrepresentable rather than silently becoming a Python float.
    raise EnvelopeRejected(
        "FMT.JSON.NON_FINITE_NUMBER",
        f"Input contains the non-finite numeric literal {token!r}. These are not "
        f"valid JSON, and a verifier that accepts one will propagate it into "
        f"arithmetic that silently produces nan.",
        rule_id="input.non_finite_numbers",
    )


def _duplicate_key_hook(report: EnvelopeReport):
    def hook(pairs):
        out: dict = {}
        for k, v in pairs:
            if k in out:
                report.hit("input.duplicate_keys", example=str(k))
                raise EnvelopeRejected(
                    "FMT.JSON.DUPLICATE_KEY",
                    f"Duplicate key {k!r} inside one JSON object. Default decoding "
                    f"keeps the last occurrence, which lets a payload show one "
                    f"value to a validator and a different value to a consumer.",
                    rule_id="input.duplicate_keys",
                )
            out[k] = v
        return out
    return hook


def _walk_bounds(node: Any, report: EnvelopeReport, cfg: dict, counter: list[int],
                 depth: int = 0) -> None:
    """Node count, string length and array length, iteratively bounded.

    Depth is already guaranteed under the configured maximum by scan_depth, so
    this recursion is bounded by that same number rather than by the input.
    """
    counter[0] += 1
    if counter[0] > cfg["max_nodes"]:
        raise EnvelopeRejected(
            "FMT.JSON.SIZE_EXCEEDED",
            f"Total JSON node count exceeds the configured maximum of {cfg['max_nodes']}.",
            rule_id="input.node_count",
        )
    if isinstance(node, dict):
        for v in node.values():
            _walk_bounds(v, report, cfg, counter, depth + 1)
    elif isinstance(node, list):
        if len(node) > cfg["max_array_length"]:
            report.hit("input.array_length")
            raise EnvelopeRejected(
                "FMT.SCHEMA.TYPE_MISMATCH",
                f"An array of {len(node)} elements exceeds the configured maximum "
                f"of {cfg['max_array_length']}.",
                rule_id="input.array_length",
            )
        for v in node:
            _walk_bounds(v, report, cfg, counter, depth + 1)
    elif isinstance(node, str):
        if len(node) > cfg["max_string_length"]:
            report.hit("input.string_length")
            raise EnvelopeRejected(
                "FMT.SCHEMA.TYPE_MISMATCH",
                f"A string of {len(node)} characters exceeds the configured maximum "
                f"of {cfg['max_string_length']}.",
                rule_id="input.string_length",
            )


def check_timestamps(doc: Any, report: EnvelopeReport, path: str = "") -> list[str]:
    """Find every timezone-naive timestamp. Reported, never coerced to UTC.

    Assuming UTC for a naive timestamp is a coercion that changes meaning, and
    the contract's rule is to refuse the field rather than guess the offset. The
    envelope is still admitted: the taxonomy puts this at level `warning`, and a
    batch with a sloppy `created_at` still has verifiable proposals in it.
    """
    found: list[str] = []
    if isinstance(doc, dict):
        for k, v in doc.items():
            child = f"{path}/{k}"
            if k in TIMESTAMP_FIELDS and isinstance(v, str):
                if _TIMESTAMP_SHAPED.match(v) and not _RFC3339_WITH_OFFSET.match(v):
                    found.append(child)
                    report.hit("input.timezone_naive_timestamps", example=child)
            else:
                found.extend(check_timestamps(v, report, child))
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            found.extend(check_timestamps(v, report, f"{path}/{i}"))
    return found


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #

def read_envelope(path: str, report: EnvelopeReport, cfg: dict) -> tuple[dict, list[str]]:
    """Read and hard-validate one input file's JSON envelope.

    Returns (document, naive_timestamp_pointers). Raises EnvelopeRejected --
    which the caller maps to exit 3 -- when the document cannot be admitted.
    """
    size = os.path.getsize(path)
    report.bytes_read += size
    if size > cfg["max_input_bytes"]:
        report.hit("input.byte_budget", example=os.path.basename(path))
        raise EnvelopeRejected(
            "FMT.JSON.SIZE_EXCEEDED",
            f"Input file is {size} bytes, exceeding the configured budget of "
            f"{cfg['max_input_bytes']} bytes. Checked on the file size before "
            f"any read, so an oversize input costs a stat() rather than its own "
            f"size in memory.",
            rule_id="input.byte_budget",
        )

    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()

    # Depth first, on the raw text: this is what makes the bound ours rather
    # than the parser's.
    scan_depth(text, cfg["max_depth"])

    try:
        doc = json.loads(
            text,
            object_pairs_hook=_duplicate_key_hook(report),
            parse_constant=_reject_non_finite,
        )
    except EnvelopeRejected:
        raise
    except json.JSONDecodeError as exc:
        report.hit("input.json_parseable", example=os.path.basename(path))
        raise EnvelopeRejected(
            "FMT.JSON.UNPARSEABLE", f"Input is not valid JSON: {exc}",
            rule_id="input.json_parseable",
        ) from exc

    if not isinstance(doc, dict):
        raise EnvelopeRejected(
            "FMT.JSON.UNPARSEABLE",
            "Top-level JSON value is not an object; the input contract requires "
            "a batch object.",
            rule_id="input.json_parseable",
        )

    _walk_bounds(doc, report, cfg, [0])

    schema_version = doc.get("schema_version")
    if not isinstance(schema_version, str) or not re.match(r"^\d+-\d+-\d+$", schema_version):
        report.hit("input.schema_version", example=str(schema_version))
        raise EnvelopeRejected(
            "FMT.SCHEMA.VERSION_UNSUPPORTED",
            f"schema_version {schema_version!r} is missing or not SchemaVer "
            f"MODEL-REVISION-ADDITION.",
            rule_id="input.schema_version",
        )

    model = schema_version.split("-", 1)[0]
    if model != cfg["supported_schema_model"]:
        report.hit("input.schema_version", example=schema_version)
        raise EnvelopeRejected(
            "FMT.SCHEMA.VERSION_UNSUPPORTED",
            f"schema_version MODEL component {model!r} is not implemented by this "
            f"build (which implements MODEL {cfg['supported_schema_model']!r}). "
            f"Processing it on the assumption that the recognised parts are still "
            f"meaningful is exactly what the version field exists to prevent.",
            rule_id="input.schema_version",
        )

    if not isinstance(doc.get("proposals"), list) or not doc["proposals"]:
        raise EnvelopeRejected(
            "FMT.SCHEMA.REQUIRED_FIELD_MISSING",
            "Batch contains no `proposals` array, so no verdict about any "
            "proposal is possible.",
            rule_id="record.schema_conformance",
        )

    naive = check_timestamps(doc, report) if cfg["require_timezone_aware_timestamps"] else []
    return doc, naive
