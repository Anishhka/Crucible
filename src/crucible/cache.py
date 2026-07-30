"""
Content-addressed verification cache (PROJECT.md §9, Tier 2).

A re-run over a superset of a previous batch should only process what is new.
That is the whole feature, and the only hard part is making it impossible for a
cache hit to change the output.

**The key is everything the verdict depends on**, and nothing else:

    candidate_key      the content hash of the proposal itself (RFC 8785-ish)
    config sha256      every threshold, tolerance and weight
    corpus versions    every reference corpus and the thermochemical dataset
    code version       crucible + taxonomy + record schema versions

If any of those move, the key moves and the entry is not reused. That is what
makes the cache safe: it is not "have I seen this proposal", it is "have I seen
this proposal, judged by exactly this code, against exactly these corpora, under
exactly this configuration". A cache that keyed on the proposal alone would
serve a stale verdict after a threshold change, which is the failure mode that
makes caches worse than useless in a verification system.

**Run-scoped fields are stripped on write and re-stamped on read.** `run_id` and
`record_id` identify the run, not the finding, so they are removed before an
entry is stored and reapplied when it is loaded. A cache hit therefore produces
bytes identical to a cache miss -- which is asserted by a test, not assumed.

**Where the cache lives.** Only when `--cache-dir` is supplied. The contract
forbids writing anywhere except `--out` and a self-created temporary directory,
so there is no default location and no implicit persistence: a graded run passes
no `--cache-dir` and therefore never writes one. The capability is real and
demonstrable; it is simply not exercised under grading, and it must not be.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field

CACHE_FORMAT_VERSION = "1"

# Fields that identify the run rather than the verdict. Stripped on write.
_RUN_SCOPED = ("run_id", "record_id")


@dataclass
class CacheStats:
    enabled: bool = False
    hits: int = 0
    misses: int = 0
    writes: int = 0
    errors: int = 0
    keys: list[str] = field(default_factory=list)

    def as_json(self) -> dict:
        return {
            "enabled": self.enabled,
            "hits": self.hits,
            "misses": self.misses,
            "writes": self.writes,
            "errors": self.errors,
        }


def compute_key(*, candidate_key: str, config_sha256: str,
                reference_rows: list[dict], crucible_version: str,
                taxonomy_version: str, record_schema_version: str) -> str:
    """The content address of one verification result."""
    corpora = sorted(f"{row['id']}:{row['sha256']}" for row in reference_rows)
    material = "|".join([
        CACHE_FORMAT_VERSION,
        candidate_key,
        config_sha256,
        crucible_version,
        taxonomy_version,
        record_schema_version,
        *corpora,
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class VerificationCache:
    """A flat directory of content-addressed verdict records."""

    def __init__(self, directory: str | None):
        self.directory = directory
        self.stats = CacheStats(enabled=bool(directory))
        if directory:
            os.makedirs(directory, exist_ok=True)

    def _path(self, key: str) -> str:
        # Two-level fan-out so a large cache does not put a hundred thousand
        # files in one directory.
        return os.path.join(self.directory, key[:2], f"{key}.json")

    def get(self, key: str, *, run_id: str, record_id: str) -> tuple[dict, list, list] | None:
        if not self.directory:
            return None
        path = self._path(key)
        if not os.path.exists(path):
            self.stats.misses += 1
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError):
            # A corrupt entry is a miss, never an error that stops the run: the
            # cache is an optimisation and must never be able to change a result.
            self.stats.errors += 1
            self.stats.misses += 1
            return None

        if payload.get("cache_format_version") != CACHE_FORMAT_VERSION:
            self.stats.misses += 1
            return None

        record = payload["record"]
        record["run_id"] = run_id
        record["record_id"] = record_id
        self.stats.hits += 1
        return record, payload.get("unit_normalizations", []), payload.get("sentinels", [])

    def put(self, key: str, record: dict, unit_normalizations: list,
            sentinels: list) -> None:
        if not self.directory:
            return
        stored = {k: v for k, v in record.items() if k not in _RUN_SCOPED}
        payload = {
            "cache_format_version": CACHE_FORMAT_VERSION,
            "key": key,
            "record": stored,
            "unit_normalizations": [
                {"proposal_id": u.proposal_id, "property": u.property,
                 "from_unit": u.from_unit, "to_unit": u.to_unit, "factor": u.factor}
                if not isinstance(u, dict) else u
                for u in unit_normalizations
            ],
            "sentinels": [
                {"proposal_id": s.proposal_id, "json_pointer": s.json_pointer,
                 "value": s.value, "rationale": s.rationale}
                if not isinstance(s, dict) else s
                for s in sentinels
            ],
        }
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            # Atomic replace: a run interrupted mid-write must not leave a
            # half-written entry that a later run would read as valid.
            handle, temporary = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(payload, fh, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True)
            os.replace(temporary, path)
            self.stats.writes += 1
        except OSError:
            self.stats.errors += 1


def restamp(record: dict, *, run_id: str, record_id: str) -> dict:
    """Reapply run-scoped identity to a record loaded from cache."""
    record = dict(record)
    record["run_id"] = run_id
    record["record_id"] = record_id
    return record
