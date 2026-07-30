"""
Claim audit (PROJECT.md §5.7).

For each `claimed_properties` entry, reach one of: supported, unsupported,
contradicted, implausible, unit_error, unverifiable.

Unit handling is the substance of this requirement, and the reason is worth
stating plainly: silent unit drift produces numbers that are wrong by exactly
three orders of magnitude and completely plausible. A band gap of 2100 meV and
a band gap of 2100 eV are the same JSON and different physics. Every conversion
applied here is therefore recorded with its factor in data_quality.json.

Placeholder values get their own treatment. -999 is finite, correctly typed, and
passes every range check that was not written with placeholders in mind -- and
so are 999, 9999, -9999, -99 and -1. They are handled as a *family*, by shape,
rather than as the one example that happened to appear in a fixture.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# unit handling
# --------------------------------------------------------------------------- #
#
# Keyed by the canonical unit of each quantity. The factor converts the supplied
# unit INTO the canonical one, so `value * factor` is always the canonical value
# and the factor is what gets recorded.

_UNIT_FACTORS: dict[str, dict[str, float]] = {
    "eV": {"ev": 1.0, "mev": 1e-3, "kev": 1e3, "j": 6.241509074e18,
           "kj/mol": 0.01036410, "kcal/mol": 0.04336410, "hartree": 27.211386,
           "ry": 13.605693, "rydberg": 13.605693, "cm-1": 1.239841984e-4},
    "K": {"k": 1.0, "c": 1.0, "f": 1.0, "degc": 1.0, "degf": 1.0,
          "celsius": 1.0, "kelvin": 1.0},
    "g/cm3": {"g/cm3": 1.0, "g/cc": 1.0, "kg/m3": 1e-3, "g/ml": 1.0,
              "kg/l": 1.0, "mg/cm3": 1e-3},
    "V": {"v": 1.0, "mv": 1e-3, "kv": 1e3},
    "mAh/g": {"mah/g": 1.0, "ah/g": 1e3, "mah/kg": 1e-3},
    "1": {"1": 1.0, "": 1.0, "dimensionless": 1.0, "none": 1.0, "-": 1.0},
    "S/cm": {"s/cm": 1.0, "s/m": 0.01, "ms/cm": 1e-3, "siemens/cm": 1.0},
    "GPa": {"gpa": 1.0, "mpa": 1e-3, "pa": 1e-9, "kbar": 0.1, "bar": 1e-4},
}

# Temperature is affine, not multiplicative, so it cannot be expressed as a
# factor. Handled separately and reported with factor 1.0 plus an explicit note.
_TEMPERATURE_OFFSETS = {"c": 273.15, "degc": 273.15, "celsius": 273.15}


@dataclass
class PropertySpec:
    canonical_unit: str
    # Physically possible range, independent of any measurement. A value outside
    # this is wrong regardless of what was measured.
    hard_min: float | None = None
    hard_max: float | None = None
    # Range a real material plausibly occupies. Outside this is suspicious, not
    # impossible.
    plausible_min: float | None = None
    plausible_max: float | None = None
    note: str = ""


PROPERTY_SPECS: dict[str, PropertySpec] = {
    "band_gap": PropertySpec("eV", 0.0, None, 0.0, 20.0,
                             "A band gap is a magnitude and cannot be negative; a "
                             "negative value is not a small gap."),
    "density": PropertySpec("g/cm3", 0.0, None, 0.5, 25.0),
    "melting_point": PropertySpec("K", 0.0, None, 100.0, 4500.0),
    "boiling_point": PropertySpec("K", 0.0, None, 100.0, 6000.0),
    "critical_temperature": PropertySpec("K", 0.0, None, 0.0, 250.0),
    "curie_temperature": PropertySpec("K", 0.0, None, 0.0, 1500.0),
    "neel_temperature": PropertySpec("K", 0.0, None, 0.0, 1500.0),
    "magnetic_ordering_temperature": PropertySpec("K", 0.0, None, 0.0, 1500.0),
    "glass_transition_temperature": PropertySpec("K", 0.0, None, 100.0, 2000.0),
    "average_voltage": PropertySpec("V", None, None, -6.0, 6.0),
    "theoretical_capacity": PropertySpec("mAh/g", 0.0, None, 0.0, 4000.0),
    "dielectric_constant": PropertySpec("1", 1.0, None, 1.0, 100000.0,
                                        "The static dielectric constant of a real "
                                        "material is at least 1."),
    "ionic_conductivity": PropertySpec("S/cm", 0.0, None, 0.0, 100.0),
    "bulk_modulus": PropertySpec("GPa", 0.0, None, 0.0, 500.0),
    "shear_modulus": PropertySpec("GPa", 0.0, None, 0.0, 500.0),
    "formation_energy": PropertySpec("eV", None, None, -20.0, 10.0),
}

# Placeholder shapes. Repdigit-nine magnitudes and small round negatives are the
# conventions that show up in generated data; the point is to match the family,
# not the example.
_SENTINEL_MAGNITUDES = {99.0, 999.0, 9999.0, 99999.0, 999999.0, 9999999.0}
_SENTINEL_EXACT = {-1.0, -9.0, -99.0, -999.0, -9999.0, -99999.0}

# Beyond this, arithmetic on the value will overflow to infinity even though the
# value itself is finite and survives a finiteness check.
_OVERFLOW_RISK = 1e30


@dataclass
class UnitNormalization:
    proposal_id: str
    property: str
    from_unit: str
    to_unit: str
    factor: float


@dataclass
class SentinelDetection:
    proposal_id: str
    json_pointer: str
    value: float | str
    rationale: str


@dataclass
class ClaimFinding:
    code: str
    json_pointer: str
    message: str
    observed: object | None = None
    expected: object | None = None
    level: str = "warning"


@dataclass
class ClaimAuditResult:
    entries: list[dict] = field(default_factory=list)
    findings: list[ClaimFinding] = field(default_factory=list)
    unit_normalizations: list[UnitNormalization] = field(default_factory=list)
    sentinels: list[SentinelDetection] = field(default_factory=list)
    evaluated: bool = False
    detail: str = ""

    @property
    def supported_fraction(self) -> float | None:
        if not self.entries:
            return None
        good = sum(1 for e in self.entries
                   if e["assessment"] in ("supported", "unverifiable"))
        return good / len(self.entries)


def _canonical_unit_token(unit: str) -> tuple[str, bool]:
    """Fold a unit string to a comparison token.

    Returns (token, had_non_ascii). A unit differing from the expected one by a
    single non-ASCII character -- a true minus sign instead of a hyphen, a micro
    sign instead of 'u' -- will not match a table of known units by string
    comparison, and that mismatch is a finding rather than something to repair
    into silence.
    """
    if not isinstance(unit, str):
        return "", False
    had_non_ascii = not unit.isascii()
    folded = unicodedata.normalize("NFKC", unit)
    folded = folded.replace("−", "-").replace("·", ".").replace("µ", "u")
    folded = re.sub(r"\s+", "", folded).lower()
    folded = folded.replace("^", "").replace("**", "")
    return folded, had_non_ascii


def _is_sentinel(value: float, spec: PropertySpec | None) -> str | None:
    if value in _SENTINEL_EXACT:
        return (f"{value:g} matches the round-negative placeholder family; it is "
                f"finite and correctly typed, which is exactly why a range check "
                f"written without placeholders in mind lets it through.")
    if abs(value) in _SENTINEL_MAGNITUDES:
        if value < 0:
            return (f"{value:g} is a repdigit-nine magnitude with a negative sign, a "
                    f"common placeholder convention.")
        if spec and spec.plausible_max is not None and value > spec.plausible_max:
            return (f"{value:g} is a repdigit-nine magnitude and is outside the "
                    f"plausible range for this quantity.")
        if abs(value) >= 999.0:
            return (f"{value:g} is a repdigit-nine magnitude, a common placeholder "
                    f"convention in generated data.")
    return None


def audit_claims(proposal: dict, batch_units: dict[str, str] | None = None) -> ClaimAuditResult:
    """Audit every claimed property on one proposal.

    `batch_units` maps property name -> the canonical unit already seen for that
    property elsewhere in the batch, so that units which are individually valid
    but inconsistent across the batch are still caught.
    """
    props = proposal.get("claimed_properties")
    result = ClaimAuditResult()
    if not isinstance(props, list) or not props:
        result.detail = "No claimed_properties supplied."
        return result

    result.evaluated = True
    proposal_id = str(proposal.get("proposal_id", ""))

    for index, prop in enumerate(props):
        if not isinstance(prop, dict):
            continue
        pointer = f"/claimed_properties/{index}"
        name = prop.get("name")
        raw_value = prop.get("value")
        raw_unit = prop.get("unit")
        spec = PROPERTY_SPECS.get(name)

        entry: dict = {
            "name": name if isinstance(name, str) else "",
            "claimed_value": raw_value if isinstance(raw_value, (int, float)) else None,
            "claimed_unit": raw_unit if isinstance(raw_unit, str) else None,
            "normalized_value": None,
            "normalized_unit": None,
            "reference_value": None,
            "reference_source": None,
            "assessment": "unverifiable",
            "detail": "",
        }

        if not isinstance(raw_value, (int, float)) or isinstance(raw_value, bool):
            entry["assessment"] = "unverifiable"
            entry["detail"] = "Claimed value is not a number."
            result.entries.append(entry)
            continue

        value = float(raw_value)

        # --- values that are finite but arithmetically hostile --------------
        if not math.isfinite(value) or abs(value) > _OVERFLOW_RISK:
            entry["assessment"] = "implausible"
            entry["detail"] = (
                f"Value {value:g} is finite but near the top of the double-precision "
                f"range; any arithmetic performed on it overflows to infinity. A "
                f"range check written against physical plausibility catches this, a "
                f"type check does not."
            )
            result.findings.append(ClaimFinding(
                "CLAIM.VALUE.PHYSICALLY_IMPOSSIBLE", f"{pointer}/value",
                entry["detail"], observed=value,
                expected="a magnitude a physical quantity can take", level="error",
            ))
            result.entries.append(entry)
            continue

        # --- unit resolution ------------------------------------------------
        if spec is None:
            entry["assessment"] = "unverifiable"
            entry["detail"] = (
                f"No reference specification for property {name!r} in this build, so "
                f"neither its unit nor its magnitude could be checked. Recorded as "
                f"unverifiable rather than assumed correct."
            )
            result.entries.append(entry)
            continue

        token, had_non_ascii = _canonical_unit_token(raw_unit or "")
        factors = _UNIT_FACTORS.get(spec.canonical_unit, {})

        if had_non_ascii:
            result.findings.append(ClaimFinding(
                "CLAIM.UNITS.INCONSISTENT", f"{pointer}/unit",
                f"Unit string {raw_unit!r} contains a non-ASCII character. It renders "
                f"like the expected unit and compares unequal to it, so a string "
                f"comparison against a table of known units silently fails to match.",
                observed=raw_unit, expected=spec.canonical_unit, level="error",
            ))

        if token not in factors:
            entry["assessment"] = "unit_error"
            entry["detail"] = (
                f"Unit {raw_unit!r} is not a recognised unit for {name}, whose "
                f"canonical unit is {spec.canonical_unit}. Not converted, because "
                f"guessing the intended unit is how a value ends up wrong by three "
                f"orders of magnitude while remaining plausible."
            )
            if not had_non_ascii:
                result.findings.append(ClaimFinding(
                    "CLAIM.UNITS.INCONSISTENT", f"{pointer}/unit",
                    entry["detail"], observed=raw_unit,
                    expected=spec.canonical_unit, level="error",
                ))
            result.entries.append(entry)
            continue

        factor = factors[token]
        offset = _TEMPERATURE_OFFSETS.get(token, 0.0)
        if token == "f" and spec.canonical_unit == "K":
            normalized = (value - 32.0) * 5.0 / 9.0 + 273.15
        else:
            normalized = value * factor + offset

        entry["normalized_value"] = normalized
        entry["normalized_unit"] = spec.canonical_unit

        if factor != 1.0 or offset != 0.0:
            result.unit_normalizations.append(UnitNormalization(
                proposal_id=proposal_id, property=str(name),
                from_unit=str(raw_unit), to_unit=spec.canonical_unit,
                factor=factor,
            ))

        # --- unit consistency across the batch ------------------------------
        if batch_units is not None:
            seen = batch_units.get(str(name))
            if seen is not None and seen != token:
                result.findings.append(ClaimFinding(
                    "CLAIM.UNITS.INCONSISTENT", f"{pointer}/unit",
                    f"Property {name} is expressed as {raw_unit!r} here and as "
                    f"{seen!r} elsewhere in the same batch. Both convert cleanly, so "
                    f"neither is wrong on its own; the inconsistency is the finding.",
                    observed=raw_unit, expected=seen, level="warning",
                ))

        # --- placeholder detection ------------------------------------------
        sentinel_reason = _is_sentinel(value, spec)
        if sentinel_reason:
            result.sentinels.append(SentinelDetection(
                proposal_id=proposal_id, json_pointer=f"{pointer}/value",
                value=value, rationale=sentinel_reason,
            ))
            result.findings.append(ClaimFinding(
                "CLAIM.SENTINEL.SUSPECTED", f"{pointer}/value",
                sentinel_reason, observed=value,
                expected="a measured or predicted value", level="warning",
            ))
            entry["assessment"] = "implausible"
            entry["detail"] = sentinel_reason
            result.entries.append(entry)
            continue

        # --- physical possibility -------------------------------------------
        impossible = None
        if spec.hard_min is not None and normalized < spec.hard_min:
            impossible = (f"{normalized:g} {spec.canonical_unit} is below the "
                          f"physical minimum of {spec.hard_min:g}. "
                          + (spec.note or ""))
        elif spec.hard_max is not None and normalized > spec.hard_max:
            impossible = (f"{normalized:g} {spec.canonical_unit} is above the "
                          f"physical maximum of {spec.hard_max:g}. "
                          + (spec.note or ""))
        if impossible:
            entry["assessment"] = "implausible"
            entry["detail"] = impossible.strip()
            result.findings.append(ClaimFinding(
                "CLAIM.VALUE.PHYSICALLY_IMPOSSIBLE", f"{pointer}/value",
                impossible.strip(), observed=normalized,
                expected=f"[{spec.hard_min}, {spec.hard_max}] {spec.canonical_unit}",
                level="error",
            ))
            result.entries.append(entry)
            continue

        # --- plausibility ----------------------------------------------------
        implausible = None
        if spec.plausible_min is not None and normalized < spec.plausible_min:
            implausible = (f"{normalized:g} {spec.canonical_unit} is below the "
                           f"plausible range [{spec.plausible_min:g}, "
                           f"{spec.plausible_max:g}] for {name}.")
        elif spec.plausible_max is not None and normalized > spec.plausible_max:
            implausible = (f"{normalized:g} {spec.canonical_unit} is above the "
                           f"plausible range [{spec.plausible_min:g}, "
                           f"{spec.plausible_max:g}] for {name}.")
        if implausible:
            entry["assessment"] = "implausible"
            entry["detail"] = implausible
            result.findings.append(ClaimFinding(
                "CLAIM.UNSUPPORTED.PROPERTY_ASSERTION", f"{pointer}/value",
                implausible, observed=normalized,
                expected=f"[{spec.plausible_min}, {spec.plausible_max}] {spec.canonical_unit}",
                level="warning",
            ))
            result.entries.append(entry)
            continue

        # Within range and correctly united. This build has no per-property
        # reference corpus, so the honest assessment is that the claim survives
        # every check that was run -- not that it was corroborated.
        entry["assessment"] = "unsupported"
        entry["reference_source"] = None
        entry["detail"] = (
            f"Value converts to {normalized:g} {spec.canonical_unit} and lies inside "
            f"the plausible range for {name}. No reference corpus of measured "
            f"property values is consulted by this build, so the claim is recorded "
            f"as uncorroborated rather than supported."
        )
        result.findings.append(ClaimFinding(
            "CLAIM.UNSUPPORTED.PROPERTY_ASSERTION", f"{pointer}/value",
            f"No consulted source could corroborate {name} = {normalized:g} "
            f"{spec.canonical_unit}.",
            observed=normalized, expected=None, level="note",
        ))
        result.entries.append(entry)

    result.detail = (f"Audited {len(result.entries)} claimed propert"
                     f"{'y' if len(result.entries) == 1 else 'ies'}.")
    return result


def collect_batch_units(proposals: list[dict]) -> dict[str, str]:
    """First unit token seen for each property name across the batch.

    Deliberately first-wins and computed over the batch in input order, so the
    comparison is stable: a majority vote would move every verdict when one
    record is added.
    """
    seen: dict[str, str] = {}
    for proposal in proposals:
        for prop in proposal.get("claimed_properties") or []:
            if not isinstance(prop, dict):
                continue
            name = prop.get("name")
            token, _ = _canonical_unit_token(prop.get("unit") or "")
            if isinstance(name, str) and token and name not in seen:
                seen[name] = token
    return seen
