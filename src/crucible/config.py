"""
Resolved run configuration.

Every number in this module is a decision someone will want to change without
editing a source file, which is the definition of configuration. The fully
resolved mapping -- defaults overlaid with the user's file and the CLI -- is
hashed into `run_manifest.config.sha256` and echoed into
`run_manifest.config.resolved`, so two runs that differ materially cannot look
identical in the manifest.

Secret redaction (CONTRACT.md §10) happens here, once, on the resolved mapping,
with the exact spellings the contract names: `<redacted>` when a value was
supplied and `<unset>` when it was not.
"""

from __future__ import annotations

import json
from typing import Any

SECRET_KEY_MARKERS = ("key", "secret", "token", "password")

DEFAULTS: dict[str, Any] = {
    "schema_version": "1-0-0",

    # --- ingest bounds: enforced before semantic interpretation -------------
    "ingest": {
        # 32 MiB. Enforced on the raw byte stream before a parser is handed it,
        # so an oversize file costs a stat() rather than its own size in RAM.
        "max_input_bytes": 33_554_432,
        "max_depth": 32,
        "max_nodes": 200_000,
        "max_string_length": 10_000,
        "max_array_length": 2_000,
        "reject_duplicate_keys": True,
        "reject_non_finite": True,
        "require_timezone_aware_timestamps": True,
        "supported_schema_model": "1",
    },

    # --- formula normalisation ---------------------------------------------
    "normalisation": {
        "unicode_form": "NFKC",
        "ascii_only_after_normalisation": True,
        "reject_format_category_characters": True,
        "formula_pattern": r"^[A-Za-z0-9()\[\]\.\*\+-]{1,200}$",
    },

    # --- evidence providers -------------------------------------------------
    # Order here does not imply precedence. Precedence is by evidence class and
    # is stated in ASSUMPTIONS.md §7.2.
    "evidence": {
        "providers": [
            {"name": "experimental_snapshot", "enabled": True, "mode": "snapshot",
             "evidence_class": "experimental", "formula_convention": "reduced_hill"},
            {"name": "computed_snapshot", "enabled": True, "mode": "snapshot",
             "evidence_class": "computed", "formula_convention": "reduced_alphabetical"},
            {"name": "literature_cassette", "enabled": True, "mode": "replay",
             "evidence_class": "literature", "formula_convention": "reduced_spaced"},
        ],
        "precedence": ["experimental", "computed", "literature"],
        "conflict_policy": "highest_evidence_class_wins",
        # No graded command may open a socket -- graded runs pass --offline, and
        # --offline is fail-closed at the single chokepoint in net.py. These
        # bounds govern the one case where a socket is attempted at all: an
        # online run probing whether the live evidence it was asked for is even
        # obtainable.
        "request": {
            "connect_timeout_seconds": 5,
            "read_timeout_seconds": 20,
            "max_retries": 2,
            "allowed_schemes": ["https"],
            "follow_redirects": False,
            # The endpoint a live literature provider would use. This build does
            # not implement one; the probe exists so that an online run cannot
            # report success while silently having consulted nothing live.
            "reachability_endpoints": ["api.crossref.org:443"],
        },
    },

    # --- validity thresholds; every one appears in the assumption stack -----
    "validity": {
        # 0.5 A is the convention established by the generative-crystal
        # literature and is explicitly a weak criterion; some later benchmarks
        # use 0.7. Recorded rather than assumed (PROJECT.md §6.2).
        "min_interatomic_distance_angstrom": 0.5,
        "lattice_bounds_angstrom": [1.0, 100.0],
        "lattice_angle_bounds_degrees": [15.0, 165.0],
        "density_bounds_g_per_cm3": [0.5, 25.0],
        "occupancy_tolerance": 0.02,
        # Used only for the crystal-family consistency check; this build does
        # not run a full symmetry determination (see LIMITATIONS.md).
        "lattice_metric_tolerance_angstrom": 0.01,
        "lattice_metric_tolerance_degrees": 0.5,
        "charge_neutrality_severity": "warning",
        "max_oxidation_state_combinations": 20_000,
    },

    # --- feasibility --------------------------------------------------------
    "feasibility": {
        # 0.067 eV/atom: the 90th percentile of the metastability distribution of
        # experimentally observed compounds. Chosen over a round 0.05 because the
        # round cutoffs in common use are conventional rather than derived, and
        # are known to exclude 26-63% of polymorphs that had already been
        # synthesised. This is a descriptive percentile of a positive-only
        # sample, NOT a classifier -- see ASSUMPTIONS.md §7.4.
        "stability_threshold_ev_per_atom": 0.067,
        "threshold_basis": (
            "Ninetieth percentile of the metastability distribution of "
            "experimentally observed compounds. A descriptive percentile of a "
            "positive-only sample, not a classifier: the distribution overlaps "
            "heavily with that of never-observed hypothetical polymorphs, so a "
            "value above it is a prompt for review rather than a rejection. "
            "Chosen over the conventional round cutoffs because those are "
            "convention rather than derivation."
        ),
        # Applied on top of the threshold rather than instead of it.
        "marginal_multiple_of_threshold": 2.0,
        # Screened for every oxide; the generator's own proposed atmosphere is
        # added to this set automatically, because that is the one it is
        # asserting will work.
        "atmospheres_to_screen": ["air", "H2"],
        "screen_temperature_k": 1273.15,
        # Above this, a closed 0 K argument stops settling anything, which is
        # what FEAS.FRAME.INAPPROPRIATE reports on.
        "frame_validity_max_temperature_k": 600.0,
        "reactive_atmospheres": ["H2", "forming_gas", "NH3", "O2", "air", "CO2"],
    },

    # --- synthesis routes ---------------------------------------------------
    "routes": {
        "max_routes_per_proposal": 3,
        "precursor_families": ["binary_oxide", "carbonate", "element"],
        "reaction_normalisation": "one_formula_unit_of_target",
        # Below this magnitude a route is flagged as slow-kinetics-prone.
        "low_driving_force_threshold_ev_per_atom": 0.05,
    },

    # --- reward: gates multiply, non-gates are a weighted mean --------------
    "reward": {
        "id": "reward.crucible@1.0.0",
        "components": {
            "parse_ok":          {"weight": 0.0,  "gate": True},
            "schema_valid":      {"weight": 0.0,  "gate": True},
            "formula_valid":     {"weight": 0.0,  "gate": True},
            "composition_valid": {"weight": 0.25, "gate": False},
            "structure_valid":   {"weight": 0.20, "gate": False},
            "novelty":           {"weight": 0.25, "gate": False},
            "feasibility":       {"weight": 0.15, "gate": False},
            "claims_supported":  {"weight": 0.15, "gate": False},
        },
        "unknown_component_value": 0.5,
    },

    # --- output -------------------------------------------------------------
    "output": {
        # Six significant digits: enough to carry an interatomic distance to
        # well past experimental precision, few enough that no float written
        # here depends on the last bits of a platform's repr. Recorded in the
        # manifest because the contract requires the number to be stated.
        "float_significant_digits": 6,
        "figures": True,
        "report": True,
    },

    "runtime": {
        "max_workers": 1,
        "timeout_seconds": 1800.0,
    },
}


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _redact(obj: Any) -> Any:
    """Replace every secret-shaped value with the contract's exact spellings.

    Applies to name-only fields such as `api_key_env` too: the contract asks for
    `<redacted>`/`<unset>` there as well, because a redaction marker that varies
    per implementation cannot be told apart from a leaked value.
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if any(marker in k.lower() for marker in SECRET_KEY_MARKERS):
                out[k] = "<unset>" if v in (None, "", [], {}) else "<redacted>"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


class Config:
    """The resolved configuration for one run."""

    def __init__(self, resolved: dict[str, Any]):
        self._resolved = resolved

    def __getitem__(self, key: str) -> Any:
        return self._resolved[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._resolved.get(key, default)

    @property
    def resolved(self) -> dict[str, Any]:
        return self._resolved

    @property
    def redacted(self) -> dict[str, Any]:
        return _redact(self._resolved)

    @property
    def sha256(self) -> str:
        """Hash of the FULLY RESOLVED configuration, after defaults and any
        overrides. Hashing only the user's file would make two materially
        different runs look identical."""
        import hashlib
        canonical = json.dumps(self.redacted, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_config(path: str | None, *, overrides: dict[str, Any] | None = None) -> Config:
    """Resolve defaults <- optional file <- CLI overrides.

    The file may be JSON, or the trivial subset of YAML that is also JSON. A
    full YAML parser is not a dependency this build is willing to take for a
    config file nobody is required to supply; an unparseable file is a usage
    error (exit 2), never a silent fallback to defaults.
    """
    resolved = json.loads(json.dumps(DEFAULTS))  # deep copy via round-trip

    if path:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        try:
            user = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"config file {path!r} is not valid JSON: {exc}. This build "
                f"accepts JSON (or the JSON-compatible subset of YAML) only."
            ) from exc
        if not isinstance(user, dict):
            raise ValueError(f"config file {path!r} must contain a JSON object at the top level")
        resolved = _deep_merge(resolved, user)

    if overrides:
        resolved = _deep_merge(resolved, overrides)

    return Config(resolved)
