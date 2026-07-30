"""
report.html and figures/ (CONTRACT.md §5, PROJECT.md §5.9).

The report is aimed at a materials scientist who has ten minutes and is deciding
whether to trust this run, so it is ordered by that question rather than by the
pipeline's structure: provenance and coverage first, then what is systematically
wrong with the generator, then the per-proposal table, then the caveats. See
ASSUMPTIONS.md §7.10.

Hard constraints, both mechanically checked:

  * **No network reference of any kind.** `report.html` must not contain an
    `http://` or `https://` string anywhere -- not as an asset, not as a
    hyperlink, not in a namespace declaration. A checker reading the file cannot
    tell a fetched asset from a hyperlink, so the rule is the strict one and
    sources are cited by identifier (`exp-00001`, a corpus id) rather than
    linked. Inline SVG is emitted without an `xmlns`, which HTML5 parses
    correctly and which keeps the file URL-free; the standalone `.svg` files
    under `figures/` do carry the namespace, because they are parsed as XML.

  * **Every graded figure ships a JSON companion** carrying the data it draws.
    The companion is what is read. Rendered images are not stable across font
    and library versions, so a specification that graded pixels would fail on
    any machine other than its author's.

Figures are hand-written SVG. No plotting library is imported anywhere in this
build: matplotlib builds a font cache on first import that takes the better part
of a minute, and its output is not byte-stable across versions.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

from .recordcheck import safe_slug

# A fixed, colour-blind-safe palette. Fixed rather than generated because a
# palette derived from the data would move when the data moves.
_INK = "#1a1a1a"
_MUTED = "#6b6b6b"
_GRID = "#d8d8d8"
_ACCENT = "#2f6f9f"
_WARN = "#b8860b"
_BAD = "#a33"
_GOOD = "#3a7d44"

_STATUS_COLOUR = {"accepted": _GOOD, "rejected": _BAD, "unverifiable": _MUTED}


@dataclass
class Figure:
    path: str
    companion_path: str
    kind: str
    media_type: str
    svg: str
    companion: dict


def _esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _bar(fraction: float, colour: str, width: int = 160) -> str:
    filled = max(0, min(width, int(round(fraction * width))))
    return (f'<span class="bar"><span class="fill" style="width:{filled}px;'
            f'background:{colour}"></span></span>')


# --------------------------------------------------------------------------- #
# figures
# --------------------------------------------------------------------------- #

def chemical_context_figure(record: dict, corpus_phases: list[dict],
                            seed: int) -> Figure | None:
    """Where the proposal sits relative to the known phases in its chemical system.

    This is deliberately NOT labelled a phase diagram. No convex hull was
    computed and no energies exist, so drawing a hull would be drawing a claim
    that was never calculated. What it does show is the thermodynamic *context*:
    which phases in this chemical system are already reported, at what
    stoichiometry, and where the proposal falls among them -- which is the
    question "where does it sit relative to the known phases in its chemical
    system" from PROJECT.md §2, answered honestly at the level the evidence
    supports.
    """
    normalized = record.get("normalized") or {}
    chemsys = normalized.get("chemsys")
    counts = normalized.get("_counts")
    if not chemsys or not counts:
        return None

    elements = sorted(counts)
    if len(elements) < 2:
        return None

    # Project onto a single composition axis: fraction of the most
    # electronegative element, which for the oxides, nitrides and halides that
    # dominate this domain is the anion. A ternary is therefore drawn as its
    # anion content rather than not drawn at all, and the projection is stated
    # in the companion so nobody mistakes it for a full phase diagram.
    from . import chemdata
    b = chemdata.most_electronegative(elements) or elements[-1]
    others = [e for e in elements if e != b]
    a = "+".join(others) if others else elements[0]

    total = sum(counts.values())
    if total <= 0:
        return None
    x_self = counts.get(b, 0) / total

    points = []
    for phase in corpus_phases:
        pc = phase["counts"]
        phase_total = sum(pc.values())
        if phase_total <= 0:
            continue
        points.append({
            "identifier": phase["identifier"],
            "reduced_formula": phase["reduced_formula"],
            "provenance": phase["provenance"],
            "x_fraction_b": pc.get(b, 0) / phase_total,
        })
    points.sort(key=lambda p: (p["x_fraction_b"], p["identifier"]))

    width, height = 640, 210
    left, right, axis_y = 60, width - 60, 130
    span = right - left

    def px(fraction: float) -> float:
        return left + fraction * span

    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'role="img" aria-label="Known phases in the {_esc(chemsys)} system">',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{left}" y="26" font-family="sans-serif" font-size="13" '
        f'fill="{_INK}">Known phases in the {_esc(chemsys)} system</text>',
        f'<text x="{left}" y="44" font-family="sans-serif" font-size="10" '
        f'fill="{_MUTED}">Composition axis only. No convex hull was computed; '
        f'vertical position carries no energy information.</text>',
        f'<line x1="{left}" y1="{axis_y}" x2="{right}" y2="{axis_y}" '
        f'stroke="{_INK}" stroke-width="1"/>',
    ]

    for tick in range(11):
        x = px(tick / 10.0)
        parts.append(f'<line x1="{x:.1f}" y1="{axis_y}" x2="{x:.1f}" '
                     f'y2="{axis_y + 5}" stroke="{_GRID}" stroke-width="1"/>')

    parts.append(f'<text x="{left}" y="{axis_y + 22}" font-family="sans-serif" '
                 f'font-size="11" fill="{_INK}" text-anchor="middle">{_esc(a)}</text>')
    parts.append(f'<text x="{right}" y="{axis_y + 22}" font-family="sans-serif" '
                 f'font-size="11" fill="{_INK}" text-anchor="middle">{_esc(b)}</text>')

    for index, point in enumerate(points):
        x = px(point["x_fraction_b"])
        # Alternate label heights so dense systems stay readable. Derived from
        # the point index, not from a random draw, so the layout is identical
        # on every run regardless of --seed.
        offset = 18 + (index % 3) * 13
        parts.append(f'<circle cx="{x:.1f}" cy="{axis_y}" r="4" fill="{_ACCENT}"/>')
        parts.append(
            f'<text x="{x:.1f}" y="{axis_y - offset}" font-family="sans-serif" '
            f'font-size="10" fill="{_MUTED}" text-anchor="middle">'
            f'{_esc(point["reduced_formula"])}</text>')

    x_prop = px(x_self)
    parts.append(f'<polygon points="{x_prop:.1f},{axis_y - 9} '
                 f'{x_prop - 7:.1f},{axis_y - 22} {x_prop + 7:.1f},{axis_y - 22}" '
                 f'fill="{_BAD}"/>')
    parts.append(
        f'<text x="{x_prop:.1f}" y="{axis_y - 26}" font-family="sans-serif" '
        f'font-size="11" font-weight="bold" fill="{_BAD}" text-anchor="middle">'
        f'{_esc(normalized.get("reduced_formula"))}</text>')
    parts.append('</svg>')

    slug = safe_slug(record["proposal_id"])
    companion = {
        "figure_kind": "chemical_context",
        "record_id": record["record_id"],
        "proposal_id": record["proposal_id"],
        "chemsys": chemsys,
        "axis": {
            "left_element": a,
            "right_element": b,
            "quantity": "atomic fraction of the right element",
            "projection": (
                "Compositions with more than two elements are projected onto the "
                "atomic fraction of the most electronegative element. This is a "
                "one-dimensional projection of a higher-dimensional composition "
                "space, not a phase diagram: two different compositions can share "
                "an x position."
            ) if len(elements) > 2 else "none; this is a true binary system",
        },
        "proposal": {
            "reduced_formula": normalized.get("reduced_formula"),
            "x_fraction_b": x_self,
        },
        "known_phases": points,
        "energy_model": None,
        "caveat": (
            "No convex hull, formation energy or energy above hull was computed "
            "for this figure. It shows composition and reported prior art in this "
            "chemical system only. Vertical position in the rendered image is "
            "layout, not energy."
        ),
    }
    return Figure(
        path=f"figures/{slug}.chemical_context.svg",
        companion_path=f"figures/{slug}.chemical_context.json",
        kind="phase_diagram", media_type="image/svg+xml",
        svg="".join(parts), companion=companion,
    )


def hull_figure(record: dict, counts: dict, hull, phases: list, seed: int) -> Figure | None:
    """A real convex-hull diagram: formation energy against composition.

    Only emitted for a binary system with actual energies. A hull drawn without
    energies would be a picture of a claim nobody computed, which is why the
    composition-only figure above is labelled and companioned as what it is
    instead of being dressed up as this.
    """
    elements = sorted(counts)
    if len(elements) != 2 or hull is None or hull.hull_energy_per_atom is None:
        return None

    a, b = elements
    points = []
    for phase in phases:
        if set(phase.counts) - {a, b}:
            continue
        total = sum(phase.counts.values())
        if total <= 0:
            continue
        points.append({
            "formula": phase.formula,
            "x_fraction_b": phase.counts.get(b, 0.0) / total,
            "formation_energy_ev_per_atom": phase.enthalpy_per_atom,
        })
    if len(points) < 2:
        return None
    points.sort(key=lambda p: (p["x_fraction_b"], p["formula"]))

    # Lower convex hull by monotone chain over (x, energy).
    def cross(o, p, q):
        return ((p["x_fraction_b"] - o["x_fraction_b"])
                * (q["formation_energy_ev_per_atom"] - o["formation_energy_ev_per_atom"])
                - (p["formation_energy_ev_per_atom"] - o["formation_energy_ev_per_atom"])
                * (q["x_fraction_b"] - o["x_fraction_b"]))

    lower: list[dict] = []
    for point in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)

    total = sum(counts.values())
    x_self = counts.get(b, 0.0) / total
    y_self = hull.formation_energy_per_atom

    width, height = 660, 330
    left, right, top, bottom = 70, width - 30, 40, height - 46
    energies = [p["formation_energy_ev_per_atom"] for p in points] + [0.0]
    if y_self is not None:
        energies.append(y_self)
    y_min, y_max = min(energies), max(energies + [0.0])
    span = (y_max - y_min) or 1.0
    y_min -= 0.08 * span
    y_max += 0.05 * span

    def px(x: float) -> float:
        return left + x * (right - left)

    def py(y: float) -> float:
        return bottom - (y - y_min) / (y_max - y_min) * (bottom - top)

    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'role="img" aria-label="Convex hull of the {_esc(a)}-{_esc(b)} system">',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{left}" y="24" font-family="sans-serif" font-size="13" '
        f'fill="{_INK}">Convex hull, {_esc(a)}–{_esc(b)} system</text>',
        f'<line x1="{left}" y1="{py(0.0):.1f}" x2="{right}" y2="{py(0.0):.1f}" '
        f'stroke="{_GRID}" stroke-dasharray="3 3"/>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" stroke="{_INK}"/>',
        f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" stroke="{_INK}"/>',
    ]

    for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
        parts.append(f'<text x="{px(fraction):.1f}" y="{bottom + 16}" '
                     f'font-family="sans-serif" font-size="10" fill="{_MUTED}" '
                     f'text-anchor="middle">{fraction:.2f}</text>')
    for step in range(4):
        value = y_min + (y_max - y_min) * step / 3.0
        parts.append(f'<text x="{left - 8}" y="{py(value) + 3:.1f}" '
                     f'font-family="sans-serif" font-size="10" fill="{_MUTED}" '
                     f'text-anchor="end">{value:.2f}</text>')
    parts.append(f'<text x="{left - 52}" y="{(top + bottom) / 2:.0f}" '
                 f'font-family="sans-serif" font-size="10" fill="{_MUTED}" '
                 f'transform="rotate(-90 {left - 52} {(top + bottom) / 2:.0f})" '
                 f'text-anchor="middle">ΔHᶠ (eV/atom)</text>')
    parts.append(f'<text x="{(left + right) / 2:.0f}" y="{height - 8}" '
                 f'font-family="sans-serif" font-size="10" fill="{_MUTED}" '
                 f'text-anchor="middle">atomic fraction {_esc(b)}</text>')

    hull_path = " ".join(
        f'{"M" if i == 0 else "L"}{px(p["x_fraction_b"]):.1f},'
        f'{py(p["formation_energy_ev_per_atom"]):.1f}'
        for i, p in enumerate(lower))
    parts.append(f'<path d="{hull_path}" fill="none" stroke="{_GOOD}" stroke-width="2"/>')

    on_hull = {(p["formula"]) for p in lower}
    for point in points:
        x, y = px(point["x_fraction_b"]), py(point["formation_energy_ev_per_atom"])
        colour = _GOOD if point["formula"] in on_hull else _MUTED
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{colour}"/>')
        parts.append(f'<text x="{x:.1f}" y="{y - 9:.1f}" font-family="sans-serif" '
                     f'font-size="9" fill="{colour}" text-anchor="middle">'
                     f'{_esc(point["formula"])}</text>')

    if y_self is not None:
        parts.append(f'<circle cx="{px(x_self):.1f}" cy="{py(y_self):.1f}" r="6" '
                     f'fill="none" stroke="{_BAD}" stroke-width="2"/>')

    above = hull.energy_above_hull
    caption = ("on the hull" if above is not None and above <= 1e-9
               else f"{above:.3f} eV/atom above the hull" if above is not None
               else "own energy unknown")
    parts.append(f'<text x="{right}" y="24" font-family="sans-serif" font-size="10" '
                 f'fill="{_BAD}" text-anchor="end">'
                 f'{_esc(record["normalized"].get("reduced_formula"))}: {_esc(caption)}</text>')
    parts.append('</svg>')

    slug = safe_slug(record["proposal_id"])
    companion = {
        "figure_kind": "convex_hull",
        "record_id": record["record_id"],
        "proposal_id": record["proposal_id"],
        "chemsys": f"{a}-{b}",
        "axis": {"x": f"atomic fraction {b}", "y": "formation enthalpy eV/atom"},
        "energy_model": "standard formation enthalpies at 298.15 K used as a 0 K proxy",
        "phases": points,
        "hull_vertices": [p["formula"] for p in lower],
        "proposal": {
            "reduced_formula": record["normalized"].get("reduced_formula"),
            "x_fraction_b": x_self,
            "formation_energy_ev_per_atom": y_self,
            "energy_above_hull_ev_per_atom": above,
        },
        "caveat": (
            "The hull is built only from phases present in the committed "
            "thermochemical table; a real competing phase absent from that table "
            "would lower the hull and is not shown. Enthalpies at 298 K stand in "
            "for 0 K formation energies."
        ),
    }
    return Figure(
        path=f"figures/{slug}.hull.svg",
        companion_path=f"figures/{slug}.hull.json",
        kind="phase_diagram", media_type="image/svg+xml",
        svg="".join(parts), companion=companion,
    )


def chempot_figure(record: dict, atmospheres: list, seed: int) -> Figure | None:
    """An oxygen chemical-potential diagram: where this oxide survives.

    One axis, μ_O, with the compound's decomposition boundary marked and each
    screened atmosphere placed on it. This is the open-system half of the
    argument, and it is the figure that makes "reduced by hydrogen, fine in air"
    legible at a glance.
    """
    screened = [a for a in atmospheres if a.mu_oxygen_ev is not None]
    if not screened:
        return None

    potentials = [a.mu_oxygen_ev for a in screened]
    # The boundary: μ_O at which decomposition becomes favourable.
    boundary = None
    for a in screened:
        if a.driving_force_ev_per_atom is not None and a.stable is not None:
            # driving = (n_O·μ − ΔH)/n_atoms, so the zero crossing is μ = ΔH/n_O.
            boundary = a.mu_oxygen_ev - a.driving_force_ev_per_atom * _n_atoms_hint(record) \
                / max(_n_oxygen_hint(record), 1e-9)
            break
    if boundary is not None:
        potentials.append(boundary)

    low, high = min(potentials) - 0.6, max(potentials) + 0.4
    width, height = 660, 190
    left, right, axis_y = 60, width - 40, 110

    def px(mu: float) -> float:
        return left + (mu - low) / (high - low) * (right - left)

    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'role="img" aria-label="Oxygen chemical potential diagram">',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{left}" y="24" font-family="sans-serif" font-size="13" '
        f'fill="{_INK}">Oxygen chemical potential: where '
        f'{_esc(record["normalized"].get("reduced_formula"))} survives</text>',
    ]

    if boundary is not None:
        parts.append(f'<rect x="{px(boundary):.1f}" y="{axis_y - 26}" '
                     f'width="{max(0.0, right - px(boundary)):.1f}" height="26" '
                     f'fill="{_GOOD}" fill-opacity="0.13"/>')
        parts.append(f'<rect x="{left}" y="{axis_y - 26}" '
                     f'width="{max(0.0, px(boundary) - left):.1f}" height="26" '
                     f'fill="{_BAD}" fill-opacity="0.13"/>')
        parts.append(f'<line x1="{px(boundary):.1f}" y1="{axis_y - 30}" '
                     f'x2="{px(boundary):.1f}" y2="{axis_y + 6}" stroke="{_BAD}" '
                     f'stroke-width="2"/>')
        parts.append(f'<text x="{px(boundary):.1f}" y="{axis_y - 34}" '
                     f'font-family="sans-serif" font-size="10" fill="{_BAD}" '
                     f'text-anchor="middle">decomposition boundary '
                     f'{boundary:.2f} eV</text>')
        parts.append(f'<text x="{left + 6}" y="{axis_y - 10}" font-family="sans-serif" '
                     f'font-size="10" fill="{_BAD}">reduced</text>')
        parts.append(f'<text x="{right - 6}" y="{axis_y - 10}" font-family="sans-serif" '
                     f'font-size="10" fill="{_GOOD}" text-anchor="end">survives</text>')

    parts.append(f'<line x1="{left}" y1="{axis_y}" x2="{right}" y2="{axis_y}" '
                 f'stroke="{_INK}"/>')

    for index, a in enumerate(sorted(screened, key=lambda x: x.mu_oxygen_ev)):
        x = px(a.mu_oxygen_ev)
        colour = _GOOD if a.stable else _BAD
        offset = 20 + (index % 2) * 16
        parts.append(f'<line x1="{x:.1f}" y1="{axis_y}" x2="{x:.1f}" '
                     f'y2="{axis_y + offset - 8}" stroke="{colour}"/>')
        parts.append(f'<circle cx="{x:.1f}" cy="{axis_y}" r="4" fill="{colour}"/>')
        parts.append(f'<text x="{x:.1f}" y="{axis_y + offset + 4}" '
                     f'font-family="sans-serif" font-size="10" fill="{colour}" '
                     f'text-anchor="middle">{_esc(a.atmosphere)}</text>')

    parts.append(f'<text x="{(left + right) / 2:.0f}" y="{height - 6}" '
                 f'font-family="sans-serif" font-size="10" fill="{_MUTED}" '
                 f'text-anchor="middle">Δμ_O (eV, relative to ½O₂ at 1 bar)</text>')
    parts.append('</svg>')

    slug = safe_slug(record["proposal_id"])
    companion = {
        "figure_kind": "oxygen_chemical_potential",
        "record_id": record["record_id"],
        "proposal_id": record["proposal_id"],
        "axis": {"quantity": "delta_mu_O", "unit": "eV",
                 "reference": "half of O2 at 1 bar"},
        "decomposition_boundary_ev": boundary,
        "atmospheres": [{
            "atmosphere": a.atmosphere,
            "mu_oxygen_ev": a.mu_oxygen_ev,
            "stable": a.stable,
            "driving_force_ev_per_atom": a.driving_force_ev_per_atom,
            "products": a.products,
        } for a in sorted(screened, key=lambda x: x.atmosphere)],
        "caveat": (
            "Decomposition is evaluated to the elements only; an intermediate "
            "suboxide absent from the thermochemical table would move the boundary. "
            "Reducing atmospheres are placed by the H2/H2O gas equilibrium at an "
            "assumed water-to-hydrogen ratio, and kinetics are not modelled."
        ),
    }
    return Figure(
        path=f"figures/{slug}.chempot.svg",
        companion_path=f"figures/{slug}.chempot.json",
        kind="chempot_diagram", media_type="image/svg+xml",
        svg="".join(parts), companion=companion,
    )


def _n_atoms_hint(record: dict) -> float:
    from .normalize import normalize_formula
    parsed = normalize_formula((record.get("normalized") or {}).get("reduced_formula") or "")
    return sum(parsed.counts.values()) if parsed.parsed_ok else 1.0


def _n_oxygen_hint(record: dict) -> float:
    from .normalize import normalize_formula
    parsed = normalize_formula((record.get("normalized") or {}).get("reduced_formula") or "")
    return parsed.counts.get("O", 0.0) if parsed.parsed_ok else 0.0


def route_figure(record: dict, seed: int) -> Figure | None:
    """The best-ranked synthesis route, drawn as precursors -> target."""
    routes = (record.get("route") or {}).get("routes") or []
    if not routes:
        return None
    route = routes[0]
    reaction = route["reaction"]

    reactants = reaction["reactants"]
    products = reaction["products"]
    width = 680
    height = 90 + 26 * max(len(reactants), len(products))
    mid_y = height // 2

    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'role="img" aria-label="Synthesis route">',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="24" y="24" font-family="sans-serif" font-size="13" '
        f'fill="{_INK}">Route {_esc(route["route_id"])} '
        f'(rank {_esc(route["rank"])})</text>',
        f'<text x="24" y="41" font-family="sans-serif" font-size="10" '
        f'fill="{_MUTED}">Scale fixed by: {_esc(reaction.get("normalization"))} '
        f'&#183; driving force not computed</text>',
    ]

    for index, term in enumerate(reactants):
        y = 70 + index * 26
        parts.append(
            f'<text x="24" y="{y}" font-family="monospace" font-size="12" '
            f'fill="{_INK}">{term["coefficient"]:g} {_esc(term["formula"])}</text>')

    arrow_x = 300
    parts.append(f'<line x1="{arrow_x}" y1="{mid_y}" x2="{arrow_x + 60}" '
                 f'y2="{mid_y}" stroke="{_INK}" stroke-width="1.5"/>')
    parts.append(f'<polygon points="{arrow_x + 60},{mid_y - 5} '
                 f'{arrow_x + 72},{mid_y} {arrow_x + 60},{mid_y + 5}" fill="{_INK}"/>')

    for index, term in enumerate(products):
        y = 70 + index * 26
        colour = _ACCENT if index == 0 else _MUTED
        parts.append(
            f'<text x="{arrow_x + 90}" y="{y}" font-family="monospace" '
            f'font-size="12" fill="{colour}">{term["coefficient"]:g} '
            f'{_esc(term["formula"])}</text>')

    parts.append(
        f'<text x="24" y="{height - 12}" font-family="sans-serif" font-size="10" '
        f'fill="{_WARN}">balanced: {str(reaction["balanced"]).lower()} '
        f'&#183; verified by re-summing element counts on both sides</text>')
    parts.append('</svg>')

    slug = safe_slug(record["proposal_id"])
    companion = {
        "figure_kind": "synthesis_route",
        "record_id": record["record_id"],
        "proposal_id": record["proposal_id"],
        "route_id": route["route_id"],
        "rank": route["rank"],
        "reaction": reaction,
        "driving_force_ev_per_atom": route.get("driving_force_ev_per_atom"),
        "conditions": route.get("conditions"),
        "assumptions": route.get("assumptions"),
        "caveat": (
            "Driving force is null: this build computes no formation energies, so "
            "the route is reported as balanced chemistry without a thermodynamic "
            "ranking. Rank reflects precursor family preference only."
        ),
    }
    return Figure(
        path=f"figures/{slug}.route.svg",
        companion_path=f"figures/{slug}.route.json",
        kind="reaction_network", media_type="image/svg+xml",
        svg="".join(parts), companion=companion,
    )


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #

_CSS = """
:root{color-scheme:light}
*{box-sizing:border-box}
body{margin:0;padding:28px 32px 64px;font:14px/1.55 -apple-system,BlinkMacSystemFont,
"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:#1a1a1a;background:#fbfbfa;
max-width:1080px}
h1{font-size:21px;margin:0 0 4px}
h2{font-size:15px;margin:32px 0 10px;padding-bottom:5px;border-bottom:1px solid #e2e2e2}
p{margin:8px 0}
.sub{color:#6b6b6b;font-size:12px;margin:0 0 18px}
table{border-collapse:collapse;width:100%;font-size:12.5px;margin:8px 0}
th,td{text-align:left;padding:6px 9px;border-bottom:1px solid #ececec;
vertical-align:top}
th{font-weight:600;color:#444;background:#f4f4f2;white-space:nowrap}
td.num{text-align:right;font-variant-numeric:tabular-nums}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
font-size:12px}
.kv{display:grid;grid-template-columns:200px 1fr;gap:3px 14px;font-size:12.5px}
.kv dt{color:#6b6b6b}
.kv dd{margin:0}
.bar{display:inline-block;width:160px;height:9px;background:#e8e8e6;
border-radius:2px;overflow:hidden;vertical-align:middle}
.fill{display:block;height:100%}
.pill{display:inline-block;padding:1px 8px;border-radius:9px;font-size:11px;
font-weight:600;color:#fff}
.note{background:#fff8e6;border-left:3px solid #b8860b;padding:9px 13px;
margin:12px 0;font-size:12.5px}
.caveat{background:#f4f6f8;border-left:3px solid #2f6f9f;padding:9px 13px;
margin:8px 0;font-size:12.5px}
figure{margin:16px 0;padding:10px;background:#fff;border:1px solid #e6e6e6;
border-radius:4px;overflow-x:auto}
figcaption{font-size:11.5px;color:#6b6b6b;margin-top:6px}
"""


def _status_pill(status: str) -> str:
    return (f'<span class="pill" style="background:'
            f'{_STATUS_COLOUR.get(status, _MUTED)}">{_esc(status)}</span>')


def render_report(*, manifest: dict, verdicts: list[dict], violations: list[dict],
                  data_quality: dict, feedback: dict, figures: list[Figure]) -> str:
    counts = manifest["counts"]
    aggregates = manifest["aggregates"]
    reference = manifest.get("reference_data", [])

    unavailable_queries = sum(
        1 for record in verdicts
        for query in (record.get("evidence") or {}).get("queries", [])
        if query.get("status") == "unavailable")

    out: list[str] = [
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">",
        f"<title>Crucible run {_esc(manifest['run_id'])}</title>",
        f"<style>{_CSS}</style></head><body>",
        f"<h1>Crucible verification report</h1>",
        f"<p class=\"sub\">Run <code>{_esc(manifest['run_id'])}</code> &#183; "
        f"logical time {_esc(manifest['provenance']['logical_time'])} &#183; "
        f"crucible {_esc(manifest['crucible_version'])} &#183; "
        f"taxonomy {_esc(manifest['taxonomy_version'])} &#183; "
        f"reward {_esc(manifest['reward_fn_id'])}</p>",
    ]

    # --- 1. can this run be trusted -------------------------------------- #
    out.append("<h2>1. Can this run be trusted?</h2>")
    status = manifest.get("status")
    out.append("<dl class=\"kv\">")
    out.append(f"<dt>Run status</dt><dd><strong>{_esc(status)}</strong></dd>")
    out.append(f"<dt>Proposals in</dt><dd>{counts['proposals_in']}</dd>")
    out.append(f"<dt>Verdicts out</dt><dd>{counts['verdicts_out']}</dd>")
    out.append(f"<dt>Quarantined</dt><dd>{counts['quarantined']}</dd>")
    out.append(
        f"<dt>Count identity</dt><dd>"
        f"{counts['proposals_in']} = {counts['verdicts_out']} + "
        f"{counts['quarantined']} &#183; "
        f"<strong>{'holds' if counts['proposals_in'] == counts['verdicts_out'] + counts['quarantined'] else 'FAILS'}"
        f"</strong></dd>")
    out.append(f"<dt>Unanswerable lookups</dt><dd>{unavailable_queries}</dd>")
    out.append(f"<dt>Float precision</dt><dd>"
               f"{_esc(manifest.get('config', {}).get('resolved', {}).get('output', {}).get('float_significant_digits'))}"
               f" significant digits</dd>")
    out.append("</dl>")

    if unavailable_queries:
        out.append(
            f"<div class=\"note\"><strong>{unavailable_queries} evidence lookups "
            f"could not be answered.</strong> Those are recorded as "
            f"<code>unavailable</code>, not as misses, and the affected records "
            f"carry novelty tier <code>unknown</code> rather than a negative "
            f"result. This run is a degraded success by design, not a clean bill "
            f"of health.</div>")

    out.append("<h2>2. What was consulted</h2>")
    if reference:
        out.append("<table><thead><tr><th>Corpus</th><th>Version</th><th>Kind</th>"
                   "<th class=\"num\">Entries</th><th>Coverage</th></tr></thead><tbody>")
        for row in reference:
            out.append(
                f"<tr><td><code>{_esc(row['provider'])}</code></td>"
                f"<td><code>{_esc(row['snapshot'])}</code></td>"
                f"<td>{_esc(row['kind'])}</td>"
                f"<td class=\"num\">{_esc(row.get('n_entries'))}</td>"
                f"<td>{_esc(row.get('coverage_note'))}</td></tr>")
        out.append("</tbody></table>")
    else:
        out.append("<p>No reference corpus was consulted in this run.</p>")

    # --- 3. what is wrong with the generator ------------------------------ #
    out.append("<h2>3. What is systematically wrong with the generator</h2>")
    code_by_field = aggregates.get("code_by_field") or {}
    if code_by_field:
        ranked = sorted(code_by_field.items(), key=lambda kv: (-kv[1], kv[0]))[:20]
        peak = ranked[0][1]
        out.append("<table><thead><tr><th>Violation code</th><th>Field</th>"
                   "<th class=\"num\">Count</th><th></th></tr></thead><tbody>")
        for combined, count in ranked:
            code, _, pointer = combined.partition("|")
            out.append(
                f"<tr><td><code>{_esc(code)}</code></td>"
                f"<td><code>{_esc(pointer)}</code></td>"
                f"<td class=\"num\">{count}</td>"
                f"<td>{_bar(count / peak, _ACCENT, 120)}</td></tr>")
        out.append("</tbody></table>")
    else:
        out.append("<p>No violations were raised in this run.</p>")

    recommendations = feedback.get("recommendations") or []
    if recommendations:
        out.append("<h2>4. Recommended changes, most impactful first</h2>")
        out.append("<table><thead><tr><th class=\"num\">#</th><th>Target</th>"
                   "<th>Change</th><th class=\"num\">Records</th></tr></thead><tbody>")
        for rec in recommendations:
            evidence = rec["evidence"]
            out.append(
                f"<tr><td class=\"num\">{rec['priority']}</td>"
                f"<td><code>{_esc(rec['target'])}</code></td>"
                f"<td>{_esc(rec['statement'])}<br><span class=\"mono\" "
                f"style=\"color:#6b6b6b\">{_esc(', '.join(evidence['codes']))}</span></td>"
                f"<td class=\"num\">{evidence['n_affected']} "
                f"({evidence['fraction_affected'] * 100:.0f}%)</td></tr>")
        out.append("</tbody></table>")

    # --- 5. per-proposal -------------------------------------------------- #
    out.append("<h2>5. Per-proposal disposition</h2>")
    out.append("<table><thead><tr><th>Proposal</th><th>Formula</th><th>Status</th>"
               "<th class=\"num\">Reward</th><th>Novelty</th><th>Feasibility</th>"
               "<th>Findings</th></tr></thead><tbody>")
    for record in verdicts:
        normalized = record.get("normalized") or {}
        verdict = record["verdict"]
        novelty = (record.get("novelty") or {}).get("tier", "—")
        feasibility = (record.get("feasibility") or {}).get("verdict", "—")
        codes = sorted({v["code"] for v in record.get("violations", [])})
        out.append(
            f"<tr><td><code>{_esc(record['proposal_id'])}</code></td>"
            f"<td><code>{_esc(normalized.get('reduced_formula') or normalized.get('formula_raw'))}</code></td>"
            f"<td>{_status_pill(verdict['status'])}</td>"
            f"<td class=\"num\">{verdict['reward']:.3f} "
            f"{_bar(verdict['reward'], _GOOD if verdict['reward'] > 0.5 else _WARN, 60)}</td>"
            f"<td>{_esc(novelty)}</td><td>{_esc(feasibility)}</td>"
            f"<td class=\"mono\">{_esc(', '.join(codes)) or '—'}</td></tr>")
    out.append("</tbody></table>")

    quarantined = data_quality.get("quarantined") or []
    if quarantined:
        out.append("<h2>6. Quarantined records</h2>")
        out.append("<table><thead><tr><th>Locator</th><th>Code</th><th>Reason</th>"
                   "</tr></thead><tbody>")
        for row in quarantined:
            out.append(
                f"<tr><td><code>{_esc(row['locator'])}</code></td>"
                f"<td><code>{_esc(row['violation_code'])}</code></td>"
                f"<td>{_esc(row['reason'])}</td></tr>")
        out.append("</tbody></table>")

    # --- figures ----------------------------------------------------------- #
    if figures:
        out.append("<h2>7. Figures</h2>")
        for figure in figures:
            out.append("<figure>")
            out.append(figure.svg)
            out.append(
                f"<figcaption>{_esc(figure.path)} &#183; data companion: "
                f"<code>{_esc(figure.companion_path)}</code>. The companion is the "
                f"graded artifact; the image is for humans.</figcaption>")
            out.append("</figure>")

    # --- caveats ----------------------------------------------------------- #
    out.append("<h2>8. What this run cannot tell you</h2>")
    for caveat in feedback.get("caveats", []):
        out.append(f"<div class=\"caveat\">{_esc(caveat)}</div>")

    out.append(
        "<h2>9. Provenance</h2><p class=\"sub\">Every figure and table above is "
        "derived from <code>verdicts.jsonl</code>, <code>violations.jsonl</code>, "
        "<code>run_manifest.json</code> and <code>data_quality.json</code> in this "
        "directory, and contains no fact that is not in them. Sources are cited by "
        "identifier rather than linked, because this file is required to render "
        "with no network access whatsoever. Re-check it with "
        "<code>crucible verify --out .</code>.</p>")

    out.append("</body></html>")
    return "".join(out) + "\n"


def standalone_svg(svg: str) -> str:
    """Wrap an inline SVG fragment as a standalone file.

    The namespace declaration is added HERE and not in the inline copy: a
    standalone .svg is parsed as XML and needs it, while report.html must not
    contain the string at all.
    """
    return svg.replace(
        "<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', 1) + "\n"
