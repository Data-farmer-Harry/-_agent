from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageDraw, ImageFile

from app.recognition_reconstruction.image_analysis import _decode_image_data_url


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def _load_image(image_data_url: str) -> Image.Image | None:
    raw_bytes = _decode_image_data_url(image_data_url)
    if raw_bytes is None:
        return None
    previous_allow_truncated = ImageFile.LOAD_TRUNCATED_IMAGES
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    try:
        return Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    except Exception:  # noqa: BLE001
        return None
    finally:
        ImageFile.LOAD_TRUNCATED_IMAGES = previous_allow_truncated


def _prepare_image(image: Image.Image, *, max_dimension: int) -> Image.Image:
    width, height = image.size
    if max(width, height) <= max_dimension:
        return image
    scale = max_dimension / float(max(width, height))
    resized = image.resize(
        (max(64, int(round(width * scale))), max(64, int(round(height * scale)))),
        Image.Resampling.LANCZOS,
    )
    return resized.convert("RGB")


def _background_index(palette: list[int], counts: np.ndarray) -> int:
    ranked = np.argsort(counts)[::-1]
    for index in ranked.tolist():
        if counts[index] <= 0:
            continue
        rgb = tuple(int(value) for value in palette[index * 3 : index * 3 + 3])
        if _relative_luminance(rgb) >= 235:
            return int(index)
    return int(ranked[0]) if ranked.size else 0


def _row_run_rects(mask: np.ndarray) -> list[list[int]]:
    height, width = mask.shape
    previous_runs: dict[tuple[int, int], list[int]] = {}
    rectangles: list[list[int]] = []

    for y in range(height):
        row = mask[y]
        x = 0
        current_runs: dict[tuple[int, int], list[int]] = {}
        while x < width:
            if not bool(row[x]):
                x += 1
                continue
            start = x
            while x < width and bool(row[x]):
                x += 1
            end = x
            key = (start, end)
            active = previous_runs.get(key)
            if active is not None:
                active[3] = y - active[1] + 1
                current_runs[key] = active
            else:
                current_runs[key] = [start, y, end - start, 1]
        for key, rect in previous_runs.items():
            if key not in current_runs:
                rectangles.append(rect)
        previous_runs = current_runs

    rectangles.extend(previous_runs.values())
    return rectangles


def build_canvas_vector_scene(
    image_data_url: str,
    *,
    max_colors: int = 32,
    max_dimension: int = 900,
) -> dict[str, object] | None:
    image = _load_image(image_data_url)
    if image is None:
        return None
    prepared = _prepare_image(image, max_dimension=max_dimension)
    width, height = prepared.size
    if width <= 0 or height <= 0:
        return None

    quantized = prepared.quantize(
        colors=max_colors,
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.NONE,
    )
    palette = quantized.getpalette() or [255, 255, 255] * 256
    indices = np.asarray(quantized, dtype=np.uint8)
    counts = np.bincount(indices.reshape(-1), minlength=256)
    background_index = _background_index(palette, counts)
    background_rgb = tuple(int(value) for value in palette[background_index * 3 : background_index * 3 + 3])

    layers: list[dict[str, object]] = []
    total_rectangles = 0

    ranked_indices = np.argsort(counts)[::-1]
    for palette_index in ranked_indices.tolist():
        pixel_count = int(counts[palette_index])
        if pixel_count <= 0 or palette_index == background_index:
            continue
        if pixel_count < 4:
            continue

        rgb = tuple(int(value) for value in palette[palette_index * 3 : palette_index * 3 + 3])
        if _relative_luminance(rgb) >= 251:
            continue

        rects = _row_run_rects(indices == int(palette_index))
        if not rects:
            continue

        total_rectangles += len(rects)
        layers.append(
            {
                "fill": _rgb_to_hex(rgb),
                "stroke": None,
                "strokeWidth": 0.0,
                "opacity": 1.0,
                "pixelCount": pixel_count,
                "rectCount": len(rects),
                "rects": rects,
            }
        )

    return {
        "width": width,
        "height": height,
        "background": _rgb_to_hex(background_rgb),
        "layerCount": len(layers),
        "rectCount": int(total_rectangles),
        "primitiveMode": "merged_rect_runs",
        "originalWidth": image.size[0],
        "originalHeight": image.size[1],
        "rescaled": prepared.size != image.size,
        "layers": layers,
    }


def render_canvas_vector_scene_to_rgb(scene: dict[str, object]) -> np.ndarray:
    width = int(scene.get("width") or 0)
    height = int(scene.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ValueError("invalid scene dimensions")

    background = str(scene.get("background") or "#ffffff")
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)
    for layer in scene.get("layers", []):
        if not isinstance(layer, dict):
            continue
        fill = str(layer.get("fill") or "#000000")
        rects = layer.get("rects", [])
        if isinstance(rects, list) and rects:
            for rect in rects:
                if not isinstance(rect, list) or len(rect) != 4:
                    continue
                x, y, w, h = (int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))
                if w <= 0 or h <= 0:
                    continue
                draw.rectangle([x, y, x + w - 1, y + h - 1], fill=fill)
            continue
        loops = layer.get("loops", [])
        if isinstance(loops, list):
            for loop in loops:
                if not isinstance(loop, list) or len(loop) < 3:
                    continue
                draw.polygon([tuple(point) for point in loop], fill=fill)
    return np.asarray(image, dtype=np.uint8)
