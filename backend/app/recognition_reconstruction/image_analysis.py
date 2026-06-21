from __future__ import annotations

import base64
import io
import re

from PIL import Image

from app.state import PlotRegionHint


DATA_URL_PATTERN = re.compile(r"^data:image/[a-zA-Z0-9.+-]+;base64,(?P<data>.+)$")


def _decode_image_data_url(image_data_url: str) -> bytes | None:
    if not image_data_url:
        return None
    match = DATA_URL_PATTERN.match(image_data_url.strip())
    if not match:
        return None
    try:
        return base64.b64decode(match.group("data"), validate=False)
    except Exception:  # noqa: BLE001
        return None


def _clamp_ratio(value: float) -> float:
    return max(0.0, min(value, 1.0))


def estimate_plot_region_from_image_data_url(image_data_url: str) -> PlotRegionHint:
    raw_bytes = _decode_image_data_url(image_data_url)
    if raw_bytes is None:
        return PlotRegionHint()

    try:
        image = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    except Exception:  # noqa: BLE001
        return PlotRegionHint()

    width, height = image.size
    if width <= 8 or height <= 8:
        return PlotRegionHint()

    max_dim = 720
    if max(width, height) > max_dim:
        scale = max_dim / float(max(width, height))
        resized = image.resize((max(16, int(width * scale)), max(16, int(height * scale))))
    else:
        resized = image

    w, h = resized.size
    pixels = resized.load()
    dark_mask = [[False] * w for _ in range(h)]
    col_counts = [0] * w
    row_counts = [0] * h

    for y in range(h):
        for x in range(w):
            r, g, b = pixels[x, y]
            luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
            chroma = max(r, g, b) - min(r, g, b)
            is_dark = luminance < 132 or (luminance < 196 and chroma < 28)
            dark_mask[y][x] = is_dark
            if is_dark:
                col_counts[x] += 1
                row_counts[y] += 1

    left_search_end = max(int(w * 0.48), 1)
    bottom_search_start = min(max(int(h * 0.42), 0), h - 1)
    left_idx = max(range(left_search_end), key=lambda index: col_counts[index])
    bottom_idx = max(range(bottom_search_start, h), key=lambda index: row_counts[index])

    if col_counts[left_idx] < h * 0.08 or row_counts[bottom_idx] < w * 0.08:
        return PlotRegionHint()

    axis_band = range(max(0, left_idx - 2), min(w, left_idx + 3))
    top_idx = h
    bottom_from_left = 0
    for x in axis_band:
        dark_rows = [y for y in range(h) if dark_mask[y][x]]
        if dark_rows:
            top_idx = min(top_idx, dark_rows[0])
            bottom_from_left = max(bottom_from_left, dark_rows[-1])

    bottom_band = range(max(0, bottom_idx - 2), min(h, bottom_idx + 3))
    right_idx = left_idx
    for y in bottom_band:
        dark_cols = [x for x in range(w) if dark_mask[y][x]]
        if dark_cols:
            right_idx = max(right_idx, dark_cols[-1])

    if top_idx >= bottom_from_left or right_idx <= left_idx:
        return PlotRegionHint()

    left = _clamp_ratio(left_idx / w)
    top = _clamp_ratio(max(0, top_idx - 4) / h)
    right = _clamp_ratio(min(w, right_idx + 4) / w)
    bottom = _clamp_ratio(min(h, max(bottom_idx, bottom_from_left) + 4) / h)

    if right - left < 0.32 or bottom - top < 0.24:
        return PlotRegionHint()

    col_strength = min(1.0, col_counts[left_idx] / max(h * 0.30, 1.0))
    row_strength = min(1.0, row_counts[bottom_idx] / max(w * 0.30, 1.0))
    confidence = max(0.36, min(0.72, (col_strength + row_strength) / 2.0))

    return PlotRegionHint(
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        confidence=confidence,
        source="image_axis_scan_fallback",
    )
