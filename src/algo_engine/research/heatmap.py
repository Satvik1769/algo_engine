"""Parameter-sensitivity heatmap as a self-contained SVG (no plotting dependency)."""
from __future__ import annotations

import math
from html import escape


def _color(value: float, lo: float, hi: float) -> str:
    if math.isnan(value):
        return "#dddddd"
    mid = 0.0 if lo < 0 < hi else (lo + hi) / 2
    if value >= mid:
        t = 0.0 if hi == mid else (value - mid) / (hi - mid)
        r, g, b = (255 - int(155 * t), 255 - int(55 * t), 255 - int(155 * t))
    else:
        t = 0.0 if lo == mid else (mid - value) / (mid - lo)
        r, g, b = (255 - int(55 * t), 255 - int(155 * t), 255 - int(155 * t))
    return f"#{r:02x}{g:02x}{b:02x}"


def heatmap_svg(title: str, x_label: str, y_label: str, xs: list, ys: list,
                values: dict[tuple, float], cell: int = 64) -> str:
    """`values[(x, y)]` is the metric for each cell; missing cells (invalid combinations) are grey and empty."""
    finite = [v for v in values.values() if v is not None and not math.isnan(v)]
    lo, hi = (min(finite), max(finite)) if finite else (0.0, 0.0)
    left, top = 90, 50
    width, height = left + cell * len(xs) + 20, top + cell * len(ys) + 50
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" font-family="sans-serif" '
           f'font-size="12">', f'<text x="{left}" y="20" font-size="14" font-weight="bold">{escape(title)}</text>']
    for i, x in enumerate(xs):
        out.append(f'<text x="{left + i * cell + cell / 2}" y="{top - 8}" text-anchor="middle">{escape(str(x))}</text>')
    for j, y in enumerate(ys):
        out.append(f'<text x="{left - 8}" y="{top + j * cell + cell / 2 + 4}" text-anchor="end">{escape(str(y))}</text>')
        for i, x in enumerate(xs):
            v = values.get((x, y))
            has = v is not None and not math.isnan(v)
            fill = _color(v, lo, hi) if has else "#dddddd"
            out.append(f'<rect x="{left + i * cell}" y="{top + j * cell}" width="{cell}" height="{cell}" '
                       f'fill="{fill}" stroke="#ffffff"/>')
            if has:
                out.append(f'<text x="{left + i * cell + cell / 2}" y="{top + j * cell + cell / 2 + 4}" '
                           f'text-anchor="middle">{v:.2f}</text>')
    out.append(f'<text x="{left + cell * len(xs) / 2}" y="{height - 12}" text-anchor="middle">{escape(x_label)}</text>')
    out.append(f'<text x="12" y="{top + cell * len(ys) / 2}" transform="rotate(-90 12 {top + cell * len(ys) / 2})" '
               f'text-anchor="middle">{escape(y_label)}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"
