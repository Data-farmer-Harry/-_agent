from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
from PIL import Image
from scipy import ndimage

from app.recognition_reconstruction.image_analysis import estimate_plot_region_from_image_data_url
from app.recognition_reconstruction.image_analysis import _decode_image_data_url
from app.recognition_reconstruction.schema import ReconstructionGeometry, ReconstructionSchema


@dataclass
class ImageTraceResult:
    traced_from_image: bool
    confidence: float
    anchor_plot_x: float | None
    anchor_plot_y: float | None
    plot_left_ratio: float | None
    plot_top_ratio: float | None
    plot_right_ratio: float | None
    plot_bottom_ratio: float | None
    liquidus_left: list[list[float]]
    liquidus_right: list[list[float]]
    solidus_left: list[list[float]]
    solidus_right: list[list[float]]
    contours: list[list[list[float]]]


def _clamp_ratio(value: float) -> float:
    return max(0.0, min(value, 1.0))


def _load_image(image_data_url: str) -> Image.Image | None:
    raw_bytes = _decode_image_data_url(image_data_url)
    if raw_bytes is None:
        return None
    try:
        return Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    except Exception:  # noqa: BLE001
        return None


def _valid_plot_region(left: float, top: float, right: float, bottom: float) -> bool:
    return right - left >= 0.20 and bottom - top >= 0.20


def _plot_region_candidates(schema: ReconstructionSchema, image_data_url: str | None) -> list[tuple[float, float, float, float]]:
    candidates: list[tuple[float, float, float, float]] = []

    def push(left: float, top: float, right: float, bottom: float) -> None:
        region = (
            _clamp_ratio(left),
            _clamp_ratio(top),
            _clamp_ratio(right),
            _clamp_ratio(bottom),
        )
        if not _valid_plot_region(*region):
            return
        if region not in candidates:
            candidates.append(region)

    push(
        schema.plot_region.left if schema.plot_region.left is not None else 0.14,
        schema.plot_region.top if schema.plot_region.top is not None else 0.12,
        schema.plot_region.right if schema.plot_region.right is not None else 0.89,
        schema.plot_region.bottom if schema.plot_region.bottom is not None else 0.84,
    )

    if image_data_url:
        heuristic = estimate_plot_region_from_image_data_url(image_data_url)
        if heuristic.left is not None and heuristic.top is not None and heuristic.right is not None and heuristic.bottom is not None:
            push(heuristic.left, heuristic.top, heuristic.right, heuristic.bottom)
            pad_x = (heuristic.right - heuristic.left) * 0.025
            pad_y = (heuristic.bottom - heuristic.top) * 0.025
            push(heuristic.left - pad_x, heuristic.top - pad_y, heuristic.right + pad_x, heuristic.bottom + pad_y)
            push(heuristic.left + pad_x, heuristic.top + pad_y, heuristic.right - pad_x, heuristic.bottom - pad_y)

    return candidates


def _crop_plot_region(image: Image.Image, schema: ReconstructionSchema) -> tuple[np.ndarray, float, float, float, float] | None:
    width, height = image.size
    if width < 24 or height < 24:
        return None
    left = schema.plot_region.left if schema.plot_region.left is not None else 0.14
    top = schema.plot_region.top if schema.plot_region.top is not None else 0.12
    right = schema.plot_region.right if schema.plot_region.right is not None else 0.89
    bottom = schema.plot_region.bottom if schema.plot_region.bottom is not None else 0.84
    left = _clamp_ratio(left)
    top = _clamp_ratio(top)
    right = _clamp_ratio(right)
    bottom = _clamp_ratio(bottom)
    if right - left < 0.20 or bottom - top < 0.20:
        return None

    x0 = max(0, min(width - 2, int(round(left * width))))
    y0 = max(0, min(height - 2, int(round(top * height))))
    x1 = max(x0 + 2, min(width, int(round(right * width))))
    y1 = max(y0 + 2, min(height, int(round(bottom * height))))
    crop = np.asarray(image.crop((x0, y0, x1, y1)).convert("RGB"), dtype=np.float32)
    return crop, left, top, right, bottom


def _candidate_mask(crop: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    grayscale = 0.2126 * crop[:, :, 0] + 0.7152 * crop[:, :, 1] + 0.0722 * crop[:, :, 2]
    saturation = crop.max(axis=2) - crop.min(axis=2)
    smooth = ndimage.gaussian_filter(grayscale, sigma=1.0)
    grad_x = ndimage.sobel(smooth, axis=1)
    grad_y = ndimage.sobel(smooth, axis=0)
    channel_spread = ndimage.gaussian_filter(saturation, sigma=0.8)
    edge_strength = np.hypot(grad_x, grad_y) + channel_spread * 0.55
    edge_threshold = float(np.percentile(edge_strength, 84.5))
    support_threshold = float(np.percentile(edge_strength, 61.0))
    dark_mask = grayscale < 210.0
    colorful_edge_mask = (saturation > 26.0) & (edge_strength >= support_threshold)
    edge_mask = edge_strength >= edge_threshold
    mask = edge_mask | ((dark_mask | colorful_edge_mask) & (edge_strength >= support_threshold))

    row_density = mask.mean(axis=1)
    col_density = mask.mean(axis=0)
    mask[row_density > 0.68, :] = False
    mask[:, col_density > 0.68] = False
    mask = ndimage.binary_dilation(mask, iterations=1)
    mask = ndimage.binary_erosion(mask, iterations=1)

    height, width = mask.shape
    margin_x = max(2, int(width * 0.015))
    margin_y = max(2, int(height * 0.015))
    trimmed = np.zeros_like(mask, dtype=bool)
    trimmed[margin_y : height - margin_y, margin_x : width - margin_x] = mask[margin_y : height - margin_y, margin_x : width - margin_x]
    return trimmed, edge_strength


def _column_candidates(mask: np.ndarray, edge_strength: np.ndarray) -> list[list[tuple[int, float]]]:
    height, width = mask.shape
    candidates: list[list[tuple[int, float]]] = []
    for x in range(width):
        ys = np.where(mask[:, x])[0]
        column: list[tuple[int, float]] = []
        for y in ys.tolist():
            strength = float(edge_strength[y, x])
            column.append((y, strength))
        candidates.append(column)
    return candidates


def _nearest_seed(candidates_by_column: list[list[tuple[int, float]]], x0: int, y0: int, height: int, width: int) -> tuple[int, int] | None:
    best: tuple[float, int, int] | None = None
    x_radius = max(4, int(width * 0.03))
    y_radius = max(10, int(height * 0.05))
    for x in range(max(0, x0 - x_radius), min(width, x0 + x_radius + 1)):
        for y, strength in candidates_by_column[x]:
            if abs(y - y0) > y_radius:
                continue
            score = abs(x - x0) * 0.8 + abs(y - y0) - strength * 0.015
            if best is None or score < best[0]:
                best = (score, x, y)
    if best is None:
        return None
    return best[1], best[2]


def _trace_branch(
    candidates_by_column: list[list[tuple[int, float]]],
    *,
    start_x: int,
    start_y: int,
    direction: int,
    branch_kind: str,
    width: int,
    height: int,
) -> list[tuple[int, int]]:
    points: list[tuple[int, int]] = [(start_x, start_y)]
    prev_y = start_y
    prev_delta = 0.0
    missing = 0
    for x in range(start_x + direction, width if direction > 0 else -1, direction):
        column = candidates_by_column[x]
        if not column:
            missing += 1
            if missing > 14:
                break
            continue
        search_radius = max(10, int(height * 0.08) + missing * 2)
        branch_candidates = [
            (y, strength)
            for y, strength in column
            if abs(y - prev_y) <= search_radius
        ]
        if not branch_candidates:
            missing += 1
            if missing > 10:
                break
            continue

        best_score: float | None = None
        best_y: int | None = None
        for y, strength in branch_candidates:
            delta = y - prev_y
            trend_penalty = abs(delta - prev_delta) * 0.35
            continuity_penalty = abs(delta) * 1.05
            if branch_kind == "upper":
                directional_penalty = max(0.0, float(delta)) * 1.4
            else:
                directional_penalty = max(0.0, float(-delta)) * 1.4
            score = continuity_penalty + trend_penalty + directional_penalty - strength * 0.02
            if best_score is None or score < best_score:
                best_score = score
                best_y = y
        if best_y is None:
            missing += 1
            if missing > 10:
                break
            continue
        missing = 0
        prev_delta = float(best_y - prev_y)
        prev_y = best_y
        points.append((x, best_y))

    return points


def _to_plot_points(
    points: list[tuple[int, int]],
    *,
    width: int,
    height: int,
) -> list[list[float]]:
    if len(points) < 6:
        return []
    ordered = sorted(points, key=lambda item: item[0])
    compact: list[list[float]] = []
    sample_stride = max(1, len(ordered) // 28)
    for index, (x, y) in enumerate(ordered):
        if index not in {0, len(ordered) - 1} and index % sample_stride != 0:
            continue
        compact.append([
            _clamp_ratio(x / max(width - 1, 1)),
            _clamp_ratio(y / max(height - 1, 1)),
        ])
    if len(compact) >= 2 and compact[0] == compact[-1]:
        compact = compact[:-1]
    return compact


def _component_path_points(
    xs: np.ndarray,
    ys: np.ndarray,
) -> tuple[list[tuple[int, int]], str]:
    x_span = int(xs.max() - xs.min() + 1)
    y_span = int(ys.max() - ys.min() + 1)
    points: list[tuple[int, int]] = []
    major_axis = "x" if x_span >= y_span else "y"
    if x_span >= y_span:
        unique_x = np.unique(xs)
        stride = max(1, int(round(unique_x.size / 42)))
        for index, x in enumerate(unique_x.tolist()):
            if index not in {0, unique_x.size - 1} and index % stride != 0:
                continue
            y_values = ys[xs == x]
            if y_values.size == 0:
                continue
            points.append((int(x), int(round(float(np.median(y_values))))))
    else:
        unique_y = np.unique(ys)
        stride = max(1, int(round(unique_y.size / 42)))
        for index, y in enumerate(unique_y.tolist()):
            if index not in {0, unique_y.size - 1} and index % stride != 0:
                continue
            x_values = xs[ys == y]
            if x_values.size == 0:
                continue
            points.append((int(round(float(np.median(x_values)))), int(y)))
    return points, major_axis


def _component_polyline(
    xs: np.ndarray,
    ys: np.ndarray,
    *,
    width: int,
    height: int,
) -> tuple[list[list[float]], list[tuple[int, int]], str]:
    points, major_axis = _component_path_points(xs, ys)
    return _to_plot_points(points, width=width, height=height), points, major_axis


def _polyline_statistics(points: list[tuple[int, int]], *, major_axis: str) -> tuple[float, float, int]:
    if len(points) < 3:
        return 0.0, 0.0, 0
    coords = np.asarray(points, dtype=np.float32)
    deltas = np.diff(coords, axis=0)
    lengths = np.hypot(deltas[:, 0], deltas[:, 1])
    total_length = max(float(lengths.sum()), 1e-6)
    dx = float(coords[-1, 0] - coords[0, 0])
    dy = float(coords[-1, 1] - coords[0, 1])
    direct_length = float(np.hypot(dx, dy))
    path_efficiency = min(direct_length / total_length, 1.0)
    secondary_deltas = deltas[:, 1] if major_axis == "x" else deltas[:, 0]
    secondary_progress = abs(dy) if major_axis == "x" else abs(dx)
    secondary_total = max(float(np.abs(secondary_deltas).sum()), 1.0)
    monotonicity = min(float(secondary_progress) / secondary_total, 1.0)

    turn_count = 0
    previous_sign = 0
    for delta in secondary_deltas.tolist():
        if abs(delta) < 1.25:
            continue
        sign = 1 if delta > 0 else -1
        if previous_sign and sign != previous_sign:
            turn_count += 1
        previous_sign = sign
    return path_efficiency, monotonicity, turn_count


def _build_legend_cluster_bounds(
    components: list[dict[str, float | int | bool | list[list[float]]]],
    *,
    width: int,
    height: int,
) -> tuple[int, int, int, int] | None:
    candidates: list[dict[str, float | int | bool | list[list[float]]]] = []
    max_area = float(width * height)
    for component in components:
        if component["near_anchor"]:
            continue
        if float(component["center_y"]) > height * 0.32:
            continue
        if float(component["major_span_ratio"]) > 0.26:
            continue
        if float(component["minor_span_ratio"]) > 0.12:
            continue
        if float(component["area"]) > max_area * 0.045:
            continue
        if (
            float(component["fill_ratio"]) < 0.05
            and int(component["turn_count"]) < 2
            and float(component["aspect_ratio"]) < 5.0
        ):
            continue
        if float(component["center_x"]) > width * 0.72 and float(component["center_y"]) > height * 0.18:
            continue
        candidates.append(component)

    if len(candidates) < 3:
        return None

    pad_x = int(round(width * 0.03))
    pad_y = int(round(height * 0.03))
    x0 = max(0, min(int(component["x_min"]) for component in candidates) - pad_x)
    y0 = max(0, min(int(component["y_min"]) for component in candidates) - pad_y)
    x1 = min(width - 1, max(int(component["x_max"]) for component in candidates) + pad_x)
    y1 = min(height - 1, max(int(component["y_max"]) for component in candidates) + pad_y)
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _component_intersects_bounds(
    component: dict[str, float | int | bool | list[list[float]]],
    bounds: tuple[int, int, int, int] | None,
) -> bool:
    if bounds is None:
        return False
    x0, y0, x1, y1 = bounds
    return not (
        int(component["x_max"]) < x0
        or int(component["x_min"]) > x1
        or int(component["y_max"]) < y0
        or int(component["y_min"]) > y1
    )


def _contour_span(contour: list[list[float]]) -> float:
    if len(contour) < 2:
        return 0.0
    xs = [point[0] for point in contour]
    return max(xs) - min(xs)


def _zone_fraction(
    contour: list[list[float]],
    *,
    x_max: float | None = None,
    y_max: float | None = None,
    y_min: float | None = None,
) -> float:
    if not contour:
        return 0.0
    hits = 0
    for x, y in contour:
        if x_max is not None and x > x_max:
            continue
        if y_max is not None and y > y_max:
            continue
        if y_min is not None and y < y_min:
            continue
        hits += 1
    return hits / max(len(contour), 1)


def _contour_center(contour: list[list[float]]) -> tuple[float, float]:
    if not contour:
        return 0.0, 0.0
    xs = [point[0] for point in contour]
    ys = [point[1] for point in contour]
    return float(sum(xs) / len(xs)), float(sum(ys) / len(ys))


def _select_render_contours(
    extracted_contours: list[list[list[float]]],
    *,
    fallback_paths: list[list[list[float]]],
) -> list[list[list[float]]]:
    filtered: list[list[list[float]]] = []
    for contour in extracted_contours:
        span = _contour_span(contour)
        top_left_fraction = _zone_fraction(contour, x_max=0.38, y_max=0.26)
        bottom_fraction = _zone_fraction(contour, y_min=0.90)
        center_x, center_y = _contour_center(contour)
        if span < 0.08:
            continue
        if top_left_fraction > 0.18 and span < 0.42:
            continue
        if bottom_fraction > 0.28 and span < 0.35:
            continue
        if span < 0.16 and center_x > 0.80 and 0.22 < center_y < 0.72:
            continue
        filtered.append(contour)
        if len(filtered) >= 8:
            break

    major_span = max((_contour_span(contour) for contour in filtered), default=0.0)
    if len(filtered) >= 2 and major_span >= 0.30:
        return filtered

    branch_contours: list[list[list[float]]] = []
    for path in fallback_paths:
        if len(path) < 6:
            continue
        if _contour_span(path) < 0.06:
            continue
        branch_contours.append(path)

    if not branch_contours:
        return filtered

    for contour in filtered:
        if len(branch_contours) >= 8:
            break
        if _contour_span(contour) >= 0.18:
            branch_contours.append(contour)
    return branch_contours[:8]


def _extract_major_contours(
    mask: np.ndarray,
    edge_strength: np.ndarray,
    *,
    anchor_x: int,
    anchor_y: int,
) -> list[list[list[float]]]:
    height, width = mask.shape
    working = ndimage.binary_closing(mask, structure=np.ones((2, 2), dtype=bool))
    labels, count = ndimage.label(working, structure=np.ones((3, 3), dtype=np.uint8))
    strength_ref = max(float(np.percentile(edge_strength, 95.0)), 1e-6)
    components: list[dict[str, float | int | bool | list[list[float]]]] = []
    for component_id in range(1, count + 1):
        ys, xs = np.where(labels == component_id)
        if xs.size < 18:
            continue
        x_min = int(xs.min())
        x_max = int(xs.max())
        y_min = int(ys.min())
        y_max = int(ys.max())
        x_span = x_max - x_min + 1
        y_span = y_max - y_min + 1
        center_x = float(xs.mean())
        center_y = float(ys.mean())
        near_anchor = abs(center_x - anchor_x) <= width * 0.22 or abs(center_y - anchor_y) <= height * 0.18
        in_upper_left_legend = (
            (x_max < width * 0.56 and y_max < height * 0.29)
            or (center_y < height * 0.24 and x_min < width * 0.12 and x_max < width * 0.72)
        ) and not near_anchor
        in_lower_left_logo = x_min < width * 0.14 and y_min > height * 0.86
        in_left_margin_annotation = x_max < width * 0.08 and not near_anchor
        in_bottom_margin_annotation = y_min > height * 0.90 and not near_anchor
        if in_upper_left_legend or in_lower_left_logo or in_left_margin_annotation or in_bottom_margin_annotation:
            continue
        if x_span < width * 0.03 and y_span < height * 0.08:
            continue
        if max(x_span / max(width, 1), y_span / max(height, 1)) < 0.085 and xs.size < 60 and not near_anchor:
            continue
        contour, sample_points, major_axis = _component_polyline(xs, ys, width=width, height=height)
        if len(contour) < 5:
            continue
        mean_strength = float(edge_strength[ys, xs].mean()) if xs.size else 0.0
        bbox_area = max(float(x_span * y_span), 1.0)
        fill_ratio = min(float(xs.size) / bbox_area, 1.0)
        aspect_ratio = max(float(x_span) / max(y_span, 1), float(y_span) / max(x_span, 1))
        major_span_ratio = max(float(x_span) / max(width, 1), float(y_span) / max(height, 1))
        minor_span_ratio = min(float(x_span) / max(width, 1), float(y_span) / max(height, 1))
        path_efficiency, monotonicity, turn_count = _polyline_statistics(sample_points, major_axis=major_axis)
        components.append(
            {
                "contour": contour,
                "area": int(xs.size),
                "x_min": x_min,
                "x_max": x_max,
                "y_min": y_min,
                "y_max": y_max,
                "x_span": x_span,
                "y_span": y_span,
                "center_x": center_x,
                "center_y": center_y,
                "near_anchor": near_anchor,
                "mean_strength": mean_strength,
                "fill_ratio": fill_ratio,
                "aspect_ratio": aspect_ratio,
                "major_span_ratio": major_span_ratio,
                "minor_span_ratio": minor_span_ratio,
                "path_efficiency": path_efficiency,
                "monotonicity": monotonicity,
                "turn_count": turn_count,
            }
        )

    legend_cluster_bounds = _build_legend_cluster_bounds(components, width=width, height=height)
    scored: list[tuple[float, list[list[float]]]] = []
    for component in components:
        near_anchor = bool(component["near_anchor"])
        major_span_ratio = float(component["major_span_ratio"])
        minor_span_ratio = float(component["minor_span_ratio"])
        fill_ratio = float(component["fill_ratio"])
        aspect_ratio = float(component["aspect_ratio"])
        path_efficiency = float(component["path_efficiency"])
        monotonicity = float(component["monotonicity"])
        turn_count = int(component["turn_count"])
        x_max = int(component["x_max"])
        y_min = int(component["y_min"])
        center_y = float(component["center_y"])
        mean_strength = float(component["mean_strength"])
        area = int(component["area"])

        axis_annotation = (
            (x_max < width * 0.10 and major_span_ratio < 0.22)
            or (y_min > height * 0.89 and major_span_ratio < 0.26)
            or (center_y < height * 0.08 and major_span_ratio < 0.18)
        ) and not near_anchor
        strict_upper_left_annotation = (
            center_y < height * 0.26
            and x_max < width * 0.50
            and major_span_ratio < 0.30
            and not near_anchor
        )
        dense_small_component = fill_ratio > 0.28 and major_span_ratio < 0.18 and not near_anchor
        text_like_component = (
            major_span_ratio < 0.22
            and (
                (fill_ratio > 0.18 and aspect_ratio < 7.0)
                or (turn_count >= 4)
                or (path_efficiency < 0.52 and monotonicity < 0.48)
            )
            and not near_anchor
        )
        label_like_component = (
            fill_ratio > 0.08
            and major_span_ratio < 0.20
            and minor_span_ratio < 0.10
            and path_efficiency < 0.82
            and turn_count >= 1
            and not near_anchor
        )
        vertical_text_like = (
            aspect_ratio > 4.0
            and minor_span_ratio < 0.06
            and fill_ratio > 0.10
            and turn_count >= 1
            and major_span_ratio < 0.42
            and not near_anchor
        )
        merged_network_component = (
            major_span_ratio > 0.45
            and (
                turn_count >= 8
                or (turn_count >= 5 and monotonicity < 0.18 and path_efficiency < 0.70)
            )
        )
        short_extreme_bar = aspect_ratio > 11.0 and major_span_ratio < 0.16 and not near_anchor
        legend_cluster_hit = _component_intersects_bounds(component, legend_cluster_bounds)
        legend_like_component = (
            legend_cluster_hit
            and major_span_ratio < 0.34
            and (
                fill_ratio > 0.05
                or turn_count >= 2
                or aspect_ratio > 4.5
                or path_efficiency < 0.78
            )
            and not near_anchor
        )
        if (
            axis_annotation
            or strict_upper_left_annotation
            or dense_small_component
            or text_like_component
            or label_like_component
            or vertical_text_like
            or merged_network_component
            or short_extreme_bar
            or legend_like_component
        ):
            continue

        text_like_penalty = 0.0
        if fill_ratio > 0.15 and major_span_ratio < 0.26:
            text_like_penalty += 0.18
        if turn_count >= 3 and major_span_ratio < 0.30:
            text_like_penalty += 0.14
        if path_efficiency < 0.60:
            text_like_penalty += 0.10
        if legend_cluster_hit:
            text_like_penalty += 0.12
        if axis_annotation:
            text_like_penalty += 0.12

        score = (
            major_span_ratio * 0.50
            + minor_span_ratio * 0.10
            + min(mean_strength / strength_ref, 1.0) * 0.16
            + monotonicity * 0.12
            + path_efficiency * 0.08
            + (0.12 if near_anchor else 0.0)
            - text_like_penalty
        )
        if area < 64 and major_span_ratio < 0.14:
            score -= 0.15
        if score <= 0.08:
            continue
        scored.append((score, component["contour"]))  # type: ignore[list-item]
    scored.sort(key=lambda item: item[0], reverse=True)
    contours: list[list[list[float]]] = []
    for _, contour in scored:
        if len(contours) >= 18:
            break
        contours.append(contour)
    return contours


def trace_phase_boundaries_from_image(
    schema: ReconstructionSchema,
    geometry: ReconstructionGeometry,
    *,
    image_data_url: str | None,
) -> ImageTraceResult:
    if not image_data_url:
        return ImageTraceResult(False, 0.0, None, None, None, None, None, None, [], [], [], [], [])

    image = _load_image(image_data_url)
    if image is None:
        return ImageTraceResult(False, 0.0, None, None, None, None, None, None, [], [], [], [], [])

    best_result = ImageTraceResult(False, 0.0, None, None, None, None, None, None, [], [], [], [], [])
    for left, top, right, bottom in _plot_region_candidates(schema, image_data_url):
        candidate_schema = schema.model_copy(
            update={
                "plot_region": schema.plot_region.model_copy(
                    update={
                        "left": left,
                        "top": top,
                        "right": right,
                        "bottom": bottom,
                    }
                )
            }
        )
        cropped = _crop_plot_region(image, candidate_schema)
        if cropped is None:
            continue
        crop, _, _, _, _ = cropped
        target_width = 320
        scale = target_width / max(float(crop.shape[1]), 1.0)
        target_height = max(120, int(round(crop.shape[0] * scale)))
        resized = np.asarray(
            Image.fromarray(crop.astype(np.uint8)).resize((target_width, target_height), Image.Resampling.BILINEAR),
            dtype=np.float32,
        )
        mask, edge_strength = _candidate_mask(resized)
        height, width = mask.shape
        candidates_by_column = _column_candidates(mask, edge_strength)

        plot_width = max(right - left, 1e-6)
        plot_height = max(bottom - top, 1e-6)
        relative_anchor_x = (geometry.base_cp_x_ratio - left) / plot_width
        relative_anchor_y = (geometry.base_cp_y_ratio - top) / plot_height
        anchor_x0 = int(round(_clamp_ratio(relative_anchor_x) * (width - 1)))
        anchor_y0 = int(round(_clamp_ratio(relative_anchor_y) * (height - 1)))

        seed = _nearest_seed(candidates_by_column, anchor_x0, anchor_y0, height, width)
        if seed is None:
            continue
        anchor_x, anchor_y = seed
        extracted_contours = _extract_major_contours(mask, edge_strength, anchor_x=anchor_x, anchor_y=anchor_y)

        upper_seed_y = anchor_y
        lower_seed_y = anchor_y
        local_candidates = candidates_by_column[anchor_x]
        upper_candidates = [y for y, _ in local_candidates if y <= anchor_y + max(4, int(height * 0.02))]
        lower_candidates = [y for y, _ in local_candidates if y >= anchor_y - max(4, int(height * 0.02))]
        if upper_candidates:
            upper_seed_y = min(upper_candidates, key=lambda value: abs(value - anchor_y))
        if lower_candidates:
            lower_seed_y = min(lower_candidates, key=lambda value: abs(value - anchor_y))

        left_upper = _trace_branch(candidates_by_column, start_x=anchor_x, start_y=upper_seed_y, direction=-1, branch_kind="upper", width=width, height=height)
        right_upper = _trace_branch(candidates_by_column, start_x=anchor_x, start_y=upper_seed_y, direction=1, branch_kind="upper", width=width, height=height)
        left_lower = _trace_branch(candidates_by_column, start_x=anchor_x, start_y=lower_seed_y, direction=-1, branch_kind="lower", width=width, height=height)
        right_lower = _trace_branch(candidates_by_column, start_x=anchor_x, start_y=lower_seed_y, direction=1, branch_kind="lower", width=width, height=height)

        liquidus_left = _to_plot_points(left_upper, width=width, height=height)
        liquidus_right = _to_plot_points(right_upper, width=width, height=height)
        solidus_left = _to_plot_points(left_lower, width=width, height=height)
        solidus_right = _to_plot_points(right_lower, width=width, height=height)
        contours = _select_render_contours(
            extracted_contours,
            fallback_paths=[liquidus_left, liquidus_right, solidus_left, solidus_right],
        )

        valid_path_lengths = [len(path) for path in (liquidus_left, liquidus_right, solidus_left, solidus_right) if path]
        if len(valid_path_lengths) < 3 and len(contours) < 3:
            continue

        coverage_score = min(sum(valid_path_lengths) / 80.0, 1.0)
        contour_score = min(sum(len(path) for path in contours[:8]) / 140.0, 1.0)
        anchor_strength = 1.0
        confidence = max(0.0, min(0.95, 0.22 + 0.34 * coverage_score + 0.24 * contour_score + 0.15 * anchor_strength))
        candidate_result = ImageTraceResult(
            traced_from_image=True,
            confidence=confidence,
            anchor_plot_x=_clamp_ratio(anchor_x / max(width - 1, 1)),
            anchor_plot_y=_clamp_ratio(anchor_y / max(height - 1, 1)),
            plot_left_ratio=left,
            plot_top_ratio=top,
            plot_right_ratio=right,
            plot_bottom_ratio=bottom,
            liquidus_left=liquidus_left,
            liquidus_right=liquidus_right,
            solidus_left=solidus_left,
            solidus_right=solidus_right,
            contours=contours,
        )
        if candidate_result.confidence > best_result.confidence:
            best_result = candidate_result

    return best_result
