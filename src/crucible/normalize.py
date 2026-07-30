"""
Formula normalisation and scope determination (PROJECT.md §5.2).

Produces, for one raw formula string: an ASCII-safe normalised form, a reduced
form, the element set, and the chemical system -- or an explicit None with a
stated reason. Never a guess.

Three rules govern this module and they are worth stating because each was a bug
before it was a rule:

  1. **Every transformation is recorded** as a (step, before, after) triple.
     Recovering a formula written with typographic subscripts is correct;
     recovering it *silently* is not, because the fact that the generator emits
     typographic subscripts is itself something we want to know about it.

  2. **Reduction is by GCD of the coefficients, and nothing else.** It does not
     reorder element symbols. Alphabetising as part of "reduction" makes TiO2
     reduce to O2Ti, compare unequal to itself, and raise
     CHEM.FORMULA.NOT_REDUCED on a formula that is already reduced -- which
     poisons the code-by-field table that is this system's main product.

  3. **A formula that cannot be normalised safely is rejected, never repaired.**
     Compatibility normalisation folds typographic subscripts and fullwidth
     forms but leaves cross-script homoglyphs untouched. Guessing which element
     a Cyrillic 'О' was meant to be produces a confident wrong answer, which is
     worse than no answer.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import reduce as _reduce
from math import gcd

from . import chemdata

# Characters in Unicode general category Cf (format) plus the zero-width
# non-joiner/joiner and BOM. These render as nothing, compare unequal, and
# silently split deduplication -- the reason FMT.ENCODING.INVISIBLE_CHARACTER
# exists as its own code.
_INVISIBLE_CATEGORIES = {"Cf"}
_EXPLICIT_INVISIBLE = {
    "​",  # zero width space
    "‌",  # zero width non-joiner
    "‍",  # zero width joiner
    "⁠",  # word joiner
    "﻿",  # zero width no-break space / BOM
}

_SUBSCRIPT_MAP = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
_SUPERSCRIPT_MAP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")

# Hydrate and adduct separators. The middle dot is legitimate notation and is
# one of several visually similar characters; NFKC leaves it untouched, so it is
# folded explicitly rather than assumed to converge.
_ADDUCT_SEPARATORS = {
    "·": "*",  # MIDDLE DOT
    "•": "*",  # BULLET
    "‧": "*",  # HYPHENATION POINT
    "⋅": "*",  # DOT OPERATOR
    "・": "*",  # KATAKANA MIDDLE DOT
    ".": "*",       # ASCII full stop used as an adduct separator
}

_MINUS_SIGNS = {
    "−": "-",  # MINUS SIGN
    "–": "-",  # EN DASH
    "—": "-",  # EM DASH
}

# Anchored: the whole string must match, so a trailing surprise cannot slip past
# a search() that only anchored the front.
_ALLOWED_FORMULA_RE = re.compile(r"^[A-Za-z0-9()\[\]*+.-]{1,200}$")
_ELEMENT_RE = re.compile(r"([A-Z][a-z]?)")
_NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)")


class FormulaError(Exception):
    """Carries the taxonomy code alongside the human-readable reason, so the
    caller never has to re-derive which finding to raise."""

    def __init__(self, code: str, reason: str):
        self.code = code
        self.reason = reason
        super().__init__(reason)


@dataclass
class NormalizationResult:
    formula_raw: str
    formula_normalized: str | None
    reduced_formula: str | None
    elements: list[str] | None
    counts: dict[str, float] | None
    chemsys: str | None
    steps: list[dict] = field(default_factory=list)
    rejected_reason: str | None = None
    rejected_code: str | None = None
    in_scope: bool = False
    scope_reason: str | None = None
    not_reduced: bool = False
    findings: list[tuple[str, str]] = field(default_factory=list)  # (code, message)

    @property
    def parsed_ok(self) -> bool:
        return self.rejected_reason is None and self.counts is not None


# --------------------------------------------------------------------------- #
# character-level normalisation
# --------------------------------------------------------------------------- #

def _find_invisible(text: str) -> list[str]:
    return sorted({
        ch for ch in text
        if ch in _EXPLICIT_INVISIBLE or unicodedata.category(ch) in _INVISIBLE_CATEGORIES
    })


def _normalize_characters(raw: str, steps: list[dict],
                          findings: list[tuple[str, str]]) -> str:
    working = raw

    invisible = _find_invisible(working)
    if invisible:
        stripped = "".join(
            ch for ch in working
            if ch not in _EXPLICIT_INVISIBLE and unicodedata.category(ch) not in _INVISIBLE_CATEGORIES
        )
        steps.append({"step": "strip_format_characters", "before": working, "after": stripped})
        findings.append((
            "FMT.ENCODING.INVISIBLE_CHARACTER",
            "Formula contained format-category character(s) "
            + ", ".join("U+%04X" % ord(c) for c in invisible)
            + " which render as nothing but make the string compare unequal.",
        ))
        working = stripped

    folded = "".join(_ADDUCT_SEPARATORS.get(ch, _MINUS_SIGNS.get(ch, ch)) for ch in working)
    if folded != working:
        steps.append({"step": "fold_separators", "before": working, "after": folded})
        working = folded

    nfkc = unicodedata.normalize("NFKC", working)
    if nfkc != working:
        steps.append({"step": "unicode_nfkc", "before": working, "after": nfkc})
        working = nfkc

    desub = working.translate(_SUBSCRIPT_MAP).translate(_SUPERSCRIPT_MAP)
    if desub != working:
        steps.append({"step": "digit_script_normalize", "before": working, "after": desub})
        working = desub

    despaced = re.sub(r"\s+", "", working)
    if despaced != working:
        steps.append({"step": "strip_whitespace", "before": working, "after": despaced})
        working = despaced

    return working


# --------------------------------------------------------------------------- #
# tokenisation
# --------------------------------------------------------------------------- #

def _parse_group(text: str, pos: int, depth: int = 0) -> tuple[dict[str, float], list[str], int]:
    """Recursive-descent parse of a formula fragment.

    Returns (counts, element order, position). Handles nested parentheses and
    bracket groups with multipliers to arbitrary depth; depth is bounded because
    the input is untrusted and an unbounded recursion here is a stack overflow
    reachable from a generator string.
    """
    if depth > 8:
        raise FormulaError("CHEM.FORMULA.UNPARSEABLE",
                           "Formula nests grouping symbols more than 8 deep.")

    counts: dict[str, float] = {}
    order: list[str] = []

    def add(sym: str, n: float) -> None:
        if sym not in counts:
            counts[sym] = 0.0
            order.append(sym)
        counts[sym] += n

    while pos < len(text):
        ch = text[pos]

        if ch in "([":
            inner, inner_order, pos = _parse_group(text, pos + 1, depth + 1)
            if pos >= len(text) or text[pos] not in ")]":
                raise FormulaError("CHEM.FORMULA.UNPARSEABLE",
                                   "Unbalanced grouping symbol in formula.")
            pos += 1
            m = _NUMBER_RE.match(text, pos)
            mult = float(m.group(1)) if m else 1.0
            if m:
                pos = m.end()
            for sym in inner_order:
                add(sym, inner[sym] * mult)
            continue

        if ch in ")]":
            return counts, order, pos

        m = _ELEMENT_RE.match(text, pos)
        if not m:
            raise FormulaError(
                "CHEM.FORMULA.UNPARSEABLE",
                f"Unexpected character {ch!r} at position {pos} of the normalised formula.",
            )
        symbol = m.group(1)
        pos = m.end()
        n = _NUMBER_RE.match(text, pos)
        qty = float(n.group(1)) if n else 1.0
        if n:
            pos = n.end()
        add(symbol, qty)

    return counts, order, pos


def _parse_formula(text: str) -> tuple[dict[str, float], list[str]]:
    """Parse a normalised ASCII formula, including `*`-separated adducts.

    `CuSO4*5H2O` is one material: the segments are summed with their leading
    multipliers, which is what makes a hydrate compare equal to itself however
    the generator chose to punctuate it.
    """
    total: dict[str, float] = {}
    order: list[str] = []

    for segment in text.split("*"):
        segment = segment.strip("+")
        if not segment:
            continue
        m = _NUMBER_RE.match(segment)
        multiplier = 1.0
        if m:
            multiplier = float(m.group(1))
            segment = segment[m.end():]
        if not segment:
            raise FormulaError("CHEM.FORMULA.UNPARSEABLE",
                               "Adduct segment carries a multiplier but no formula.")
        counts, seg_order, pos = _parse_group(segment, 0)
        if pos != len(segment):
            raise FormulaError("CHEM.FORMULA.UNPARSEABLE",
                               "Trailing grouping symbol in formula.")
        for sym in seg_order:
            if sym not in total:
                total[sym] = 0.0
                order.append(sym)
            total[sym] += counts[sym] * multiplier

    if not total:
        raise FormulaError("CHEM.FORMULA.UNPARSEABLE", "Formula contains no element symbols.")
    return total, order


# --------------------------------------------------------------------------- #
# reduction and canonical spellings
# --------------------------------------------------------------------------- #

def reduce_counts(counts: dict[str, float]) -> tuple[dict[str, float], int]:
    """Divide integer coefficients by their GCD. Returns (reduced, divisor).

    A divisor greater than one is the *only* thing that makes a formula "not
    reduced". Element ordering has nothing to do with it.
    """
    values = list(counts.values())
    if not values or not all(float(v).is_integer() for v in values):
        return dict(counts), 1
    ints = [int(v) for v in values]
    divisor = _reduce(gcd, ints)
    if divisor <= 1:
        return dict(counts), 1
    return {k: int(v) // divisor for k, v in counts.items()}, divisor


def _format_counts(counts: dict[str, float], order: list[str]) -> str:
    parts = []
    for sym in order:
        v = counts[sym]
        if float(v).is_integer():
            iv = int(v)
            parts.append(sym if iv == 1 else f"{sym}{iv}")
        else:
            parts.append(f"{sym}{v:g}")
    return "".join(parts)


def hill_order(counts: dict[str, float]) -> list[str]:
    """Hill ordering: carbon first, hydrogen second, then alphabetical. For
    compositions without carbon this is plain alphabetical order."""
    symbols = sorted(counts)
    if "C" in counts:
        rest = [s for s in symbols if s not in ("C", "H")]
        return ["C"] + (["H"] if "H" in counts else []) + rest
    return symbols


def spell(counts: dict[str, float], convention: str) -> str:
    """Spell a composition the way a given provider expects it.

    Formula conventions differ per source and a mismatched convention returns
    zero rows with a success status -- which looks exactly like novelty. Every
    provider therefore declares its convention and the exact string sent is
    recorded on the query.
    """
    reduced, _ = reduce_counts(counts)
    if convention == "reduced_hill":
        return _format_counts(reduced, hill_order(reduced))
    if convention == "reduced_alphabetical":
        return _format_counts(reduced, sorted(reduced))
    if convention == "reduced_spaced":
        order = hill_order(reduced)
        return " ".join(
            sym if float(reduced[sym]).is_integer() and int(reduced[sym]) == 1
            else f"{sym}{int(reduced[sym]) if float(reduced[sym]).is_integer() else reduced[sym]:g}"
            for sym in order
        )
    raise ValueError(f"unknown formula convention {convention!r}")


def composition_key(counts: dict[str, float]) -> str:
    """Canonical, convention-independent identity of a composition.

    Reduced stoichiometry as a sorted element->coefficient mapping. This is what
    ASSUMPTIONS.md §7.3 means by formula equality: a supercell and its reduced
    cell share this key; a non-stoichiometric variant does not.
    """
    reduced, _ = reduce_counts(counts)
    return "|".join(
        f"{sym}:{int(reduced[sym]) if float(reduced[sym]).is_integer() else round(reduced[sym], 6)}"
        for sym in sorted(reduced)
    )


# --------------------------------------------------------------------------- #
# scope
# --------------------------------------------------------------------------- #

def determine_scope(counts: dict[str, float]) -> tuple[bool, str | None]:
    """Is this an inorganic crystalline solid -- the only class this tool claims
    competence over?

    Out of scope is a statement about the tool's limits, not a judgement about
    the material, so the reason is always stated. Reporting a polymer as "out of
    scope, because it is a hydrocarbon repeat unit" is a correct answer; running
    a bulk-crystal argument on it and reporting a number is not.
    """
    elements = set(counts)

    if elements <= {"C", "H"}:
        return False, (
            "Composition contains only carbon and hydrogen, which describes a "
            "hydrocarbon or polymer repeat unit rather than an inorganic "
            "crystalline solid. No inorganic bulk-crystal argument applies."
        )

    has_metal = bool(elements & chemdata.METALS)
    organic_like = {"C", "H"} <= elements
    if organic_like and not has_metal:
        return False, (
            "Composition contains both carbon and hydrogen and no metallic "
            "element, which is characteristic of a molecular or organic species "
            "rather than an inorganic crystalline solid."
        )

    if elements <= {"He", "Ne", "Ar", "Kr", "Xe", "Rn"}:
        return False, (
            "Composition consists solely of noble gases, which do not form an "
            "inorganic crystalline solid under any ordinary condition."
        )

    return True, None


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #

def normalize_formula(raw: str) -> NormalizationResult:
    steps: list[dict] = []
    findings: list[tuple[str, str]] = []

    def rejected(code: str, reason: str, normalized: str | None) -> NormalizationResult:
        return NormalizationResult(
            formula_raw=raw, formula_normalized=normalized, reduced_formula=None,
            elements=None, counts=None, chemsys=None, steps=steps,
            rejected_reason=reason, rejected_code=code, in_scope=False,
            scope_reason=reason, findings=findings,
        )

    if not isinstance(raw, str) or not raw:
        return rejected("CHEM.FORMULA.UNPARSEABLE", "Formula is empty or not a string.", None)

    working = _normalize_characters(raw, steps, findings)

    # Non-ASCII survivors are homoglyphs from another script. Rejected, never
    # mapped to the lookalike.
    if not working.isascii():
        offenders = sorted({ch for ch in working if not ch.isascii()})
        return rejected(
            "FMT.ENCODING.NON_ASCII_SYMBOL",
            "Formula still contains non-ASCII character(s) "
            + ", ".join("%r (U+%04X)" % (c, ord(c)) for c in offenders)
            + " after compatibility normalisation. These are almost certainly "
              "element symbols from another script; refusing to guess which "
              "element was meant.",
            None,
        )

    if not _ALLOWED_FORMULA_RE.match(working):
        return rejected(
            "CHEM.FORMULA.UNPARSEABLE",
            "Normalised formula does not match the anchored allowlist of "
            "characters permitted in a chemical formula.",
            working,
        )

    try:
        counts, order = _parse_formula(working)
    except FormulaError as exc:
        return rejected(exc.code, exc.reason, working)

    unknown = sorted({sym for sym in counts if not chemdata.is_element(sym)})
    if unknown:
        return rejected(
            "CHEM.FORMULA.UNKNOWN_ELEMENT",
            f"Formula references symbol(s) that are not chemical elements: "
            f"{', '.join(unknown)}. Well-formed in shape is not the same as real.",
            working,
        )

    if any(v <= 0 for v in counts.values()):
        return rejected(
            "CHEM.FORMULA.UNPARSEABLE",
            "Formula contains a non-positive stoichiometric coefficient.",
            working,
        )

    reduced_counts, divisor = reduce_counts(counts)
    not_reduced = divisor > 1
    if not_reduced:
        findings.append((
            "CHEM.FORMULA.NOT_REDUCED",
            f"Supplied stoichiometry is a {divisor}x multiple of its reduced form "
            f"{_format_counts(reduced_counts, order)}. Recorded because matching "
            f"against sources that store reduced formulas depends on it; this is "
            f"an observation about the generator, not a defect in the material.",
        ))

    elements = sorted(counts)
    in_scope, scope_reason = determine_scope(counts)

    return NormalizationResult(
        formula_raw=raw,
        formula_normalized=working,
        reduced_formula=_format_counts(reduced_counts, order),
        elements=elements,
        counts=counts,
        chemsys="-".join(elements),
        steps=steps,
        rejected_reason=None,
        rejected_code=None,
        in_scope=in_scope,
        scope_reason=scope_reason,
        not_reduced=not_reduced,
        findings=findings,
    )
