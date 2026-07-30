"""
Evidence providers and novelty tiering (PROJECT.md §5.3).

Every provider implements one interface and is registered in one table. Adding a
provider means adding a row there; nothing in the pipeline changes. There will
be more of them.

The distinctions this module exists to preserve:

  * **snapshot miss vs replay miss.** A snapshot is a local corpus that was
    searched, so a miss is real evidence of absence *within that corpus*. A
    cassette is a recording, so a miss means only that this question was never
    asked. The second is `unavailable`, never `miss`. Recording a replay miss as
    an absence of prior art converts "we didn't look" into "it's novel", which
    is the most consequential error this system can make.

  * **no_evidence vs unknown.** `no_evidence` requires that *every* provider was
    able to answer and none matched. If even one could not answer, the tier is
    `unknown`. They are never collapsed.

  * **different-class disagreement vs same-class contradiction.** A
    crystallographic source and a DFT corpus returning different answers are not
    in conflict; they are answering different questions, and both answers are
    true. A conflict is recorded only when two providers of the *same* evidence
    class disagree.

  * **the exact string sent.** Formula conventions differ per source and a
    mismatched convention returns zero rows with a success status, which looks
    exactly like novelty. Every query records what was actually asked, after
    that provider's own normalisation.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field

from .normalize import composition_key, spell

CORPUS_DIR = os.path.join(os.path.dirname(__file__), "reference_data")

# Strongest evidence first. Used to pick the tier and to resolve conflicts.
CLASS_PRECEDENCE = ("experimental", "computed", "literature")

CLASS_TO_TIER = {
    "experimental": "experimentally_reported",
    "computed": "computed_only",
    "literature": "literature_mention",
}


@dataclass
class ProviderSpec:
    name: str
    corpus_file: str
    mode: str            # snapshot | replay | live
    evidence_class: str  # experimental | computed | literature
    formula_convention: str
    match_kind: str


# The registry. Adding a provider is adding a row here.
PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec("experimental_snapshot", "experimental_snapshot.json", "snapshot",
                 "experimental", "reduced_hill", "reduced_formula"),
    ProviderSpec("mineral_reference", "mineral_reference.json", "snapshot",
                 "experimental", "reduced_alphabetical", "reduced_formula"),
    ProviderSpec("computed_snapshot", "computed_snapshot.json", "snapshot",
                 "computed", "reduced_alphabetical", "reduced_formula"),
    ProviderSpec("literature_cassette", "literature_cassette.json", "replay",
                 "literature", "reduced_spaced", "text"),
)


@dataclass
class Query:
    """One provider's answer about one composition, in the shape the verdict
    schema's evidence block expects."""
    provider: str
    mode: str
    status: str  # hit | miss | unavailable
    query: str
    normalization_note: str | None = None
    n_results: int | None = None
    reference_data_id: str | None = None
    error: str | None = None
    matches: list[dict] = field(default_factory=list)
    evidence_class: str = ""

    def to_json(self) -> dict:
        out: dict = {
            "provider": self.provider,
            "mode": self.mode,
            "status": self.status,
            "query": self.query,
        }
        if self.normalization_note:
            out["normalization_note"] = self.normalization_note
        if self.n_results is not None:
            out["n_results"] = self.n_results
        if self.reference_data_id is not None:
            out["reference_data_id"] = self.reference_data_id
        if self.error is not None:
            out["error"] = self.error
        if self.matches:
            out["matches"] = self.matches
        return out


class Corpus:
    """A loaded corpus, indexed by convention-independent composition key."""

    def __init__(self, spec: ProviderSpec, payload: dict, sha256: str):
        self.spec = spec
        self.payload = payload
        self.sha256 = sha256
        self.index: dict[str, list[dict]] = {}
        for entry in payload.get("entries", []):
            self.index.setdefault(entry["key"], []).append(entry)

    @property
    def reference_data_id(self) -> str:
        return f"{self.spec.name}@{self.payload['corpus_version']}"

    def manifest_entry(self) -> dict:
        """The run_manifest.reference_data row for this corpus.

        A novelty verdict is a statement relative to a specific corpus at a
        specific version; without this row two runs a month apart are neither
        comparable nor reproducible.
        """
        return {
            "id": self.reference_data_id,
            "provider": self.spec.name,
            "kind": "cassette" if self.spec.mode == "replay" else "snapshot",
            "snapshot": self.payload["corpus_version"],
            "sha256": self.sha256,
            "n_entries": self.payload.get("n_entries", len(self.index)),
            "coverage_note": self.payload.get("coverage_note"),
            "license": self.payload.get("license"),
            "retrieved_at": None,
        }


_CORPUS_CACHE: dict[str, Corpus | None] = {}


def _load(spec: ProviderSpec) -> Corpus | None:
    """Load one corpus, or None when it is missing.

    A missing corpus is an infrastructure failure and must surface as
    `unavailable`, never as a miss -- so this returns None rather than an empty
    index, which would silently answer "not found" to every question.
    """
    if spec.name in _CORPUS_CACHE:
        return _CORPUS_CACHE[spec.name]
    path = os.path.join(CORPUS_DIR, spec.corpus_file)
    corpus: Corpus | None = None
    if os.path.exists(path):
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
            payload = json.loads(raw.decode("utf-8"))
            corpus = Corpus(spec, payload, hashlib.sha256(raw).hexdigest())
        except (OSError, json.JSONDecodeError, KeyError):
            corpus = None
    _CORPUS_CACHE[spec.name] = corpus
    return corpus


def active_providers(only: list[str] | None = None) -> list[ProviderSpec]:
    """Providers for this run, in a fixed order. `--provider` restricts the set;
    an unknown name is ignored here and reported by the caller."""
    if not only:
        return list(PROVIDERS)
    wanted = set(only)
    return [p for p in PROVIDERS if p.name in wanted]


def reference_data(only: list[str] | None = None) -> list[dict]:
    """Manifest rows for every corpus actually available this run."""
    rows = []
    for spec in active_providers(only):
        corpus = _load(spec)
        if corpus is not None:
            rows.append(corpus.manifest_entry())
    return sorted(rows, key=lambda r: r["id"])


def unavailable_providers(only: list[str] | None = None) -> list[dict]:
    """Providers configured but unable to answer. Their presence is what
    justifies exit code 4."""
    out = []
    for spec in active_providers(only):
        if _load(spec) is None:
            out.append({
                "provider": spec.name,
                "reason": f"Corpus file {spec.corpus_file} is missing or unreadable; "
                          f"this provider could not be consulted and its answers are "
                          f"recorded as unavailable rather than as misses.",
            })
    return out


def query_all(counts: dict[str, float], *, only: list[str] | None = None) -> list[Query]:
    """Ask every configured provider about one composition.

    Every provider consulted appears in the result, INCLUDING providers that
    could not answer. Omitting an unreachable provider is a silent-failure bug,
    not a tidy output.
    """
    key = composition_key(counts)
    queries: list[Query] = []

    for spec in active_providers(only):
        query_string = spell(counts, spec.formula_convention)
        note = (f"Formula spelled with this provider's {spec.formula_convention} "
                f"convention; matching is done on the reduced composition, not on "
                f"this string.")
        corpus = _load(spec)

        if corpus is None:
            queries.append(Query(
                provider=spec.name, mode=spec.mode, status="unavailable",
                query=query_string, normalization_note=note,
                error=f"Corpus {spec.corpus_file} unavailable.",
                evidence_class=spec.evidence_class,
            ))
            continue

        hits = corpus.index.get(key, [])

        # A recorded query that returned nothing is a recorded MISS, not a hit.
        # The cassette distinguishes "asked, found nothing" from "never asked",
        # and only the second is unavailable. Dropping this distinction would
        # promote the negative control to a literature mention on the strength
        # of a recording that says there is no literature.
        if hits and all(h.get("n_documents") == 0 for h in hits):
            queries.append(Query(
                provider=spec.name, mode=spec.mode, status="miss",
                query=query_string, normalization_note=note, n_results=0,
                reference_data_id=corpus.reference_data_id,
                evidence_class=spec.evidence_class,
            ))
            continue

        if hits:
            matches = [{
                "identifier": h["identifier"],
                "match_kind": spec.match_kind,
                "url": None,
                "title": h.get("note"),
                "doi": None,
                "year": None,
                "provenance": h.get("provenance", "unknown"),
                **({"properties": {"n_documents": h["n_documents"]}}
                   if "n_documents" in h else {}),
            } for h in hits]
            queries.append(Query(
                provider=spec.name, mode=spec.mode, status="hit",
                query=query_string, normalization_note=note,
                n_results=len(matches),
                reference_data_id=corpus.reference_data_id,
                matches=matches, evidence_class=spec.evidence_class,
            ))
            continue

        if spec.mode == "replay":
            # The defining property of replay mode. This question was never
            # recorded, so there is no answer -- not a negative answer.
            queries.append(Query(
                provider=spec.name, mode=spec.mode, status="unavailable",
                query=query_string, normalization_note=note,
                reference_data_id=corpus.reference_data_id,
                error="No recorded interaction for this query in the cassette. A "
                      "replay miss means the question was never asked, not that "
                      "the answer is no.",
                evidence_class=spec.evidence_class,
            ))
            continue

        queries.append(Query(
            provider=spec.name, mode=spec.mode, status="miss",
            query=query_string, normalization_note=note, n_results=0,
            reference_data_id=corpus.reference_data_id,
            evidence_class=spec.evidence_class,
        ))

    return queries


def resolve_conflicts(queries: list[Query]) -> list[dict]:
    """Same-class disagreements, with the rule that resolved each.

    Deliberately narrow. Two providers of *different* evidence classes returning
    different answers are not in conflict -- they were asked different questions
    -- and recording that as a conflict would fire on nearly every proposal and
    mean nothing.
    """
    conflicts: list[dict] = []
    by_class: dict[str, list[Query]] = {}
    for q in queries:
        by_class.setdefault(q.evidence_class, []).append(q)

    for evidence_class in CLASS_PRECEDENCE:
        group = by_class.get(evidence_class, [])
        answered = [q for q in group if q.status in ("hit", "miss")]
        if len(answered) < 2:
            continue
        statuses = {q.status for q in answered}
        if len(statuses) > 1:
            hitters = sorted(q.provider for q in answered if q.status == "hit")
            missers = sorted(q.provider for q in answered if q.status == "miss")
            conflicts.append({
                "providers": sorted(q.provider for q in answered),
                "description": (
                    f"Two sources of the same evidence class ({evidence_class}) "
                    f"disagree about this composition: {', '.join(hitters)} report "
                    f"a match, {', '.join(missers)} do not. Both were able to "
                    f"answer, so this is a genuine contradiction rather than a "
                    f"coverage gap in one of them."
                ),
                "resolution": (
                    "Resolved in favour of the positive answer: within a single "
                    "evidence class a hit is a positive observation and a miss is "
                    "bounded by that corpus's coverage, so a hit anywhere in the "
                    "class establishes the tier. Both the conflict and this rule "
                    "are recorded rather than averaged away."
                ),
            })
    return conflicts


def tier_from_answers(queries: list[Query]) -> tuple[str, float, str, list[str]]:
    """Reduce the provider answers to (tier, confidence, rationale, supporters).

    `no_evidence` requires that every provider was able to answer. One
    `unavailable` anywhere is enough to force `unknown`, because the evidence
    needed to distinguish the tiers was not available.
    """
    if not queries:
        return ("unknown", 0.0,
                "No evidence provider was consulted, so no statement about prior "
                "art can be made.", [])

    hits_by_class: dict[str, list[Query]] = {}
    for q in queries:
        if q.status == "hit":
            hits_by_class.setdefault(q.evidence_class, []).append(q)

    unavailable = [q for q in queries if q.status == "unavailable"]

    for evidence_class in CLASS_PRECEDENCE:
        group = hits_by_class.get(evidence_class)
        if not group:
            continue
        tier = CLASS_TO_TIER[evidence_class]
        supporters = sorted(q.provider for q in group)
        identifiers = sorted(m["identifier"] for q in group for m in q.matches)
        # A hit is a positive observation and stands on its own; an unavailable
        # provider elsewhere lowers confidence but cannot turn a match into a
        # non-match.
        confidence = 0.9 if evidence_class == "experimental" else 0.7
        if unavailable:
            confidence -= 0.1
        rationale = (
            f"Matched in the {evidence_class} evidence class by "
            f"{', '.join(supporters)} ({', '.join(identifiers[:5])}"
            f"{', ...' if len(identifiers) > 5 else ''}). Tier is set by the "
            f"strongest class that matched: an experimental determination is a "
            f"much stronger claim than an appearance in a computational corpus."
        )
        if unavailable:
            rationale += (
                f" {len(unavailable)} provider(s) could not answer "
                f"({', '.join(sorted(q.provider for q in unavailable))}), which "
                f"lowers confidence but cannot undo a positive match."
            )
        return tier, round(confidence, 3), rationale, supporters

    if unavailable:
        names = ", ".join(sorted(q.provider for q in unavailable))
        return ("unknown", 0.1,
                f"No provider matched this composition, but {len(unavailable)} of "
                f"{len(queries)} could not answer at all ({names}). The evidence "
                f"needed to distinguish 'nothing has reported this' from 'we could "
                f"not look' was not available, so the tier is unknown. This is not "
                f"a euphemism for no_evidence and must not be collapsed into it.",
                [])

    answered = sorted(q.provider for q in queries)
    return ("no_evidence", 0.45,
            f"Every consulted provider ({', '.join(answered)}) was able to answer "
            f"and none reported this composition. Note the bound on this claim: it "
            f"is absence from these specific corpora at their pinned versions, not "
            f"absence from the literature. Their coverage notes are in the run "
            f"manifest and they are small.",
            answered)


def novelty_block(queries: list[Query]) -> dict:
    tier, confidence, rationale, supporters = tier_from_answers(queries)
    return {
        "tier": tier,
        "confidence": confidence,
        "rationale": rationale[:2000],
        "supporting_provider_ids": supporters,
        "conflicts": resolve_conflicts(queries),
    }
