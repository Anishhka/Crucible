"""
Offline snapshot evidence provider (PROJECT.md 5.3).

Queries the bundled starter corpus (reference_data/known_compounds.json)
for exact and reduced-formula matches. This is a `snapshot`-mode provider
in the vocabulary of verdict.schema.json's evidence_block: the corpus is
local and complete-with-respect-to-itself, so a miss here is real evidence
of absence WITHIN THIS CORPUS -- it is emphatically not evidence the
material has never been reported anywhere. That distinction is exactly why
novelty_block.tier includes 'unknown' as a first-class value: this
provider alone can support 'experimentally_reported' (on a hit) but should
NOT be trusted alone to support 'no_evidence' (a miss) without a caveat,
because a 110-entry starter corpus does not cover materials science.

Wire in a second, larger provider before treating 'no_evidence' verdicts
from this module as reliable -- see LIMITATIONS.md.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache

CORPUS_PATH = os.path.join(os.path.dirname(__file__), "reference_data", "known_compounds.json")
PROVIDER_ID = "crucible_starter_corpus"


@dataclass
class EvidenceQuery:
    provider: str
    mode: str  # live | snapshot | replay
    status: str  # hit | miss | unavailable
    query: str
    reference_data_id: str | None = None
    n_results: int | None = None
    matches: list[dict] = field(default_factory=list)
    error: str | None = None


@lru_cache(maxsize=1)
def _load_corpus() -> dict | None:
    if not os.path.exists(CORPUS_PATH):
        return None
    with open(CORPUS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _index_by_reduced_formula() -> dict[str, list[dict]]:
    corpus = _load_corpus()
    index: dict[str, list[dict]] = {}
    if not corpus:
        return index
    for entry in corpus["entries"]:
        index.setdefault(entry["reduced_formula"], []).append(entry)
    return index


def reference_data_manifest_entry() -> dict | None:
    """The run_manifest.reference_data entry describing this corpus, for
    the run overall -- include this exactly once per run, not per query."""
    corpus = _load_corpus()
    if not corpus:
        return None
    import hashlib
    with open(CORPUS_PATH, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    return {
        "id": PROVIDER_ID,
        "provider": PROVIDER_ID,
        "kind": "known_compounds_snapshot",
        "snapshot": corpus["corpus_version"],
        "sha256": digest,
    }


def query_prior_art(reduced_formula: str | None, formula_normalized: str | None) -> EvidenceQuery:
    """Looks up a proposal by reduced formula against the offline corpus.

    Returns status='unavailable' (never a fabricated 'miss') if the corpus
    file itself could not be loaded -- a missing corpus is an
    infrastructure failure, not evidence of novelty.
    """
    corpus = _load_corpus()
    query_str = reduced_formula or formula_normalized or ""

    if corpus is None:
        return EvidenceQuery(
            provider=PROVIDER_ID, mode="snapshot", status="unavailable",
            query=query_str, error=f"Corpus file not found at {CORPUS_PATH}.",
        )
    if not query_str:
        return EvidenceQuery(
            provider=PROVIDER_ID, mode="snapshot", status="unavailable",
            query=query_str, error="No normalized formula available to query.",
        )

    index = _index_by_reduced_formula()
    hits = index.get(reduced_formula, []) if reduced_formula else []

    if hits:
        matches = [
            {
                "identifier": f"{PROVIDER_ID}:{h['reduced_formula']}",
                "match_kind": "reduced_formula",
                "url": None,
                "title": h.get("note"),
                "doi": None,
                "year": None,
                "provenance": h.get("provenance", "unknown"),
            }
            for h in hits
        ]
        return EvidenceQuery(
            provider=PROVIDER_ID, mode="snapshot", status="hit",
            query=query_str, reference_data_id=PROVIDER_ID,
            n_results=len(matches), matches=matches,
        )

    return EvidenceQuery(
        provider=PROVIDER_ID, mode="snapshot", status="miss",
        query=query_str, reference_data_id=PROVIDER_ID, n_results=0,
    )


def novelty_from_query(eq: EvidenceQuery) -> dict:
    """Maps one evidence query into a novelty_block. Kept conservative:
    a miss against this small starter corpus is reported as 'unknown', not
    'no_evidence' -- see module docstring. Promote this to 'no_evidence'
    only once a corpus large enough to make that claim defensible is wired
    in (LIMITATIONS.md)."""
    if eq.status == "unavailable":
        return {
            "tier": "unknown",
            "confidence": 0.0,
            "rationale": f"Prior-art provider unavailable: {eq.error}",
            "supporting_provider_ids": [],
        }
    if eq.status == "hit":
        provenances = {m["provenance"] for m in eq.matches}
        tier = "experimentally_reported" if "experimental" in provenances else "literature_mention"
        return {
            "tier": tier,
            "confidence": 0.7,
            "rationale": f"Matched {eq.n_results} entr{'y' if eq.n_results == 1 else 'ies'} "
                         f"in the starter corpus by reduced formula.",
            "supporting_provider_ids": [PROVIDER_ID],
        }
    # miss
    return {
        "tier": "unknown",
        "confidence": 0.1,
        "rationale": "No match in the ~110-entry starter corpus. This corpus is far "
                     "too small to support a genuine 'no prior art exists' claim; "
                     "reported as unknown rather than no_evidence until a real "
                     "materials database snapshot is wired in.",
        "supporting_provider_ids": [PROVIDER_ID],
    }
