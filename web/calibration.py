"""The calibration curve — PRD §4.3, the answer to the accuracy objection.

Inline SVG, hand-built. No chart library and no build step, matching spec §1,
and the geometry is simple enough that a dependency would cost more than it
saves.

Design decisions that are not taste:

- **One y-axis.** Both series are percentages, so they share a scale. A
  dual-axis version would invent a correlation that is not in the data.
- **Two categorical hues, validated.** `#388bfd` / `#db6d28` clear the
  colourblind-separation check at ΔE 29.3 (protan) against this surface. The
  first pair tried — blue and purple — looked fine and scored 2.7, which is
  indistinguishable under deuteranopia.
- **The lane colours are deliberately not used.** Green, red and amber mean
  Approved, Bounced and Pen everywhere else in this app; reusing them for a
  series would overload them.
- Identity is never colour alone: a legend, plus a direct label at each line's
  end, plus a table below with every number.
"""

import json
from pathlib import Path

from fasthtml.common import (
    NotStr,
    Table,
    Tbody,
    Td,
    Th,
    Thead,
    Tr,
)

import config

CURVE_PATH = Path("calibrate/curve.json")

AUTO = "#388bfd"
AGREE = "#db6d28"
SURFACE = "#161922"

W, H = 820, 396
LEFT, RIGHT, TOP, BOTTOM = 64, 786, 34, 316  # plot box; axis band lives below


def load() -> dict | None:
    if not CURVE_PATH.exists():
        return None
    return json.loads(CURVE_PATH.read_text(encoding="utf-8"))


def _x(floor: float, floors: list[float]) -> float:
    lo, hi = floors[0], floors[-1]
    return LEFT + (floor - lo) / (hi - lo) * (RIGHT - LEFT)


def _y(share: float) -> float:
    return BOTTOM - share * (BOTTOM - TOP)


def chart(curve: dict, gate: str) -> NotStr:
    series = curve["gates"][gate]
    floors = curve["floors"]
    points = [p for p in series if p["agreement"] is not None]
    parts: list[str] = []

    # Grid and y axis. Solid hairlines one shade off the surface — dashed grid
    # reads as a threshold when it is only a grid.
    for pct in range(0, 101, 25):
        y = _y(pct / 100)
        parts.append(
            f'<line x1="{LEFT}" y1="{y:.1f}" x2="{RIGHT}" y2="{y:.1f}" '
            f'stroke="#232838" stroke-width="1"/>'
            f'<text x="{LEFT - 10}" y="{y + 4:.1f}" text-anchor="end" '
            f'class="tick">{pct}%</text>'
        )

    # X axis ticks at the floors we actually sampled.
    for point in series:
        x = _x(point["floor"], floors)
        parts.append(
            f'<text x="{x:.1f}" y="{BOTTOM + 22}" text-anchor="middle" '
            f'class="tick">{point["floor"]:.2f}</text>'
        )
    parts.append(
        f'<text x="{(LEFT + RIGHT) / 2:.0f}" y="{BOTTOM + 48}" text-anchor="middle" '
        f'class="axis-title">confidence floor</text>'
    )

    # The operating point the app actually ships, so the curve connects to the
    # wall rather than floating beside it.
    shipped = curve.get("shipped", {})
    if shipped.get("gate") == gate:
        x = _x(shipped["floor"], floors)
        parts.append(
            f'<line x1="{x:.1f}" y1="{TOP}" x2="{x:.1f}" y2="{BOTTOM}" '
            f'stroke="#d29922" stroke-width="1" stroke-opacity="0.5"/>'
            f'<text x="{x + 6:.1f}" y="{TOP + 12}" class="shipped">shipped</text>'
        )

    for key, colour, label in (
        ("auto", AUTO, "auto-handled"),
        ("agreement", AGREE, "agreement on those"),
    ):
        path = " ".join(
            f"{'M' if i == 0 else 'L'}{_x(p['floor'], floors):.1f},{_y(p[key]):.1f}"
            for i, p in enumerate(points)
        )
        parts.append(
            f'<path d="{path}" fill="none" stroke="{colour}" stroke-width="2" '
            f'stroke-linejoin="round" stroke-linecap="round"/>'
        )
        # 2px surface ring so overlapping markers stay separable.
        for p in points:
            parts.append(
                f'<circle cx="{_x(p["floor"], floors):.1f}" cy="{_y(p[key]):.1f}" '
                f'r="4.5" fill="{colour}" stroke="{SURFACE}" stroke-width="2"/>'
            )
        last = points[-1]
        parts.append(
            f'<text x="{_x(last["floor"], floors) - 6:.1f}" '
            f'y="{_y(last[key]) - 12:.1f}" text-anchor="end" '
            f'class="endlabel">{label} {last[key]:.0%}</text>'
        )

    # Hover: a transparent column per sampled floor, revealing a crosshair and
    # readout. Pure CSS, and the values are also in the table below — a tooltip
    # is never the only way to read a number.
    step = (RIGHT - LEFT) / max(len(series) - 1, 1)
    for point in series:
        x = _x(point["floor"], floors)
        agree = (
            f'{point["agreement"]:.1%}' if point["agreement"] is not None else "—"
        )
        box_x = min(x + 10, RIGHT - 150)
        parts.append(
            f'<g class="col">'
            f'<rect x="{x - step / 2:.1f}" y="{TOP}" width="{step:.1f}" '
            f'height="{BOTTOM - TOP}" fill="transparent"/>'
            f'<g class="col-chrome">'
            f'<line x1="{x:.1f}" y1="{TOP}" x2="{x:.1f}" y2="{BOTTOM}" '
            f'stroke="#8b93a7" stroke-width="1"/>'
            f'<rect x="{box_x:.1f}" y="{TOP + 6}" width="146" height="54" rx="4" '
            f'fill="#0d1016" stroke="#232838"/>'
            f'<text x="{box_x + 10:.1f}" y="{TOP + 24}" class="tip">'
            f'floor {point["floor"]:.2f} · n={point["n"]}</text>'
            f'<text x="{box_x + 10:.1f}" y="{TOP + 40}" class="tip auto">'
            f'auto-handled {point["auto"]:.0%}</text>'
            f'<text x="{box_x + 10:.1f}" y="{TOP + 54}" class="tip agree">'
            f'agreement {agree}</text>'
            f'</g></g>'
        )

    return NotStr(
        f'<svg viewBox="0 0 {W} {H}" class="curve" role="img" '
        f'aria-label="Agreement with the human label rises as the confidence '
        f'floor rises, while the auto-handled share falls.">'
        + "".join(parts)
        + "</svg>"
    )


def table(curve: dict, gate: str) -> Table:
    """The table-view twin. Every value on the chart is readable here, so the
    hover layer enhances rather than gates."""
    rows = []
    for point in curve["gates"][gate]:
        rows.append(
            Tr(
                Td(f"{point['floor']:.2f}"),
                Td(f"{point['auto']:.0%}"),
                Td(f"{point['agreement']:.3f}" if point["agreement"] is not None else "—"),
                Td(f"{point['precision']:.2f}" if point["precision"] is not None else "—"),
                Td(f"{point['recall']:.2f}" if point["recall"] is not None else "—"),
                Td(str(point["n"])),
            )
        )
    return Table(
        Thead(
            Tr(
                Th("floor"), Th("auto-handled"), Th("agreement"),
                Th("precision"), Th("recall"), Th("n"),
            )
        ),
        Tbody(*rows),
        cls="curve-table",
    )


GATE_BLURB = {
    "min2": "Both severity axes must clear the floor. The conservative reading.",
    "mean2": "Their average must clear the floor.",
    "decision": (
        f"Probability mass on one side of the line — the gate the app ships, at "
        f"floor {config.CONFIDENCE_FLOOR:.2f}."
    ),
}
