import os
os.environ["HF_HOME"] = "../model_cache"
os.environ["HF_DATASETS_CACHE"] = "../model_cache/datasets"

from dataclasses import dataclass

import numpy as np
from PIL import Image


MIN_RAW_MASK_PIXELS = 64


class ColorExtractionError(ValueError):
    """Raised when a garment mask is too small to support a trustworthy color estimate."""


@dataclass(frozen=True)
class ColorResult:
    hex_value: str
    rgb: tuple[int, int, int]
    pixel_count: int
    method: str


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def load_rgb_array(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("RGB"), dtype=np.uint8)


def load_mask_array(mask: Image.Image, size: tuple[int, int], threshold: int = 128) -> np.ndarray:
    resized = mask.convert("L").resize(size, Image.Resampling.LANCZOS)
    return np.asarray(resized, dtype=np.uint8) >= threshold


def filter_clothing_pixels(
    image: Image.Image,
    mask: Image.Image,
    mask_threshold: int = 128,
    min_value: int = 35,
    max_value: int = 245,
    min_retained_fraction: float = 0.5,
) -> np.ndarray:
    """Return robust garment pixels without rejecting gray, white, or ivory fabric.

    The binary mask, rather than saturation, defines whether a pixel belongs to the
    garment. This matters for achromatic garments: a saturation cutoff would remove
    gray/ivory pixels preferentially and leave colored noise. Extreme shadow and
    highlight pixels are removed only when at least ``min_retained_fraction`` of the
    masked region survives; otherwise the original pixels are retained so a truly
    black or white garment is not mistaken for an empty one.

    A mask smaller than ``MIN_RAW_MASK_PIXELS`` is rejected before tone filtering,
    making segmentation failure explicit instead of silently emitting ``#000000``.
    """
    rgb = load_rgb_array(image)
    mask_arr = load_mask_array(mask, image.size, threshold=mask_threshold)
    pixels = rgb[mask_arr]
    raw_pixel_count = int(len(pixels))
    if raw_pixel_count < MIN_RAW_MASK_PIXELS:
        raise ColorExtractionError(
            "Garment mask has "
            f"{raw_pixel_count} foreground pixels; at least {MIN_RAW_MASK_PIXELS} are required."
        )

    if not 0.0 <= min_retained_fraction <= 1.0:
        raise ValueError("min_retained_fraction must be between 0.0 and 1.0")

    keep = (
        (pixels.max(axis=1) >= min_value)
        & (pixels.min(axis=1) <= max_value)
    )
    filtered = pixels[keep]
    minimum_survivors = int(np.ceil(raw_pixel_count * min_retained_fraction))
    if len(filtered) < minimum_survivors:
        return pixels
    return filtered


def trim_by_luminance(pixels: np.ndarray, lower_percentile: float = 10.0, upper_percentile: float = 90.0) -> np.ndarray:
    if len(pixels) < 10:
        return pixels
    luminance = (
        0.2126 * pixels[:, 0].astype(np.float32)
        + 0.7152 * pixels[:, 1].astype(np.float32)
        + 0.0722 * pixels[:, 2].astype(np.float32)
    )
    low = np.percentile(luminance, lower_percentile)
    high = np.percentile(luminance, upper_percentile)
    trimmed = pixels[(luminance >= low) & (luminance <= high)]
    if len(trimmed) < 10:
        return pixels
    return trimmed


def trim_by_bright_zone(pixels: np.ndarray, lower_percentile: float = 45.0, upper_percentile: float = 85.0) -> np.ndarray:
    """[조명 필터 1] 어두운 그림자 영역(하위 45%)을 대폭 제거하고 조명을 받은 45%~85% 명도 구간 중심 추출"""
    if len(pixels) < 10:
        return pixels
    luminance = (
        0.2126 * pixels[:, 0].astype(np.float32)
        + 0.7152 * pixels[:, 1].astype(np.float32)
        + 0.0722 * pixels[:, 2].astype(np.float32)
    )
    low = np.percentile(luminance, lower_percentile)
    high = np.percentile(luminance, upper_percentile)
    trimmed = pixels[(luminance >= low) & (luminance <= high)]
    if len(trimmed) < 10:
        return pixels
    return trimmed


def apply_gray_world_wb(pixels: np.ndarray) -> np.ndarray:
    """[조명 필터 2] Gray World 알고리즘 기반 화이트 밸런스(조명 색온도 제거) 1차 보정"""
    if len(pixels) < 10:
        return pixels
    pixels_f = pixels.astype(np.float32)
    mean_r, mean_g, mean_b = np.mean(pixels_f, axis=0)
    if mean_r < 1e-5 or mean_g < 1e-5 or mean_b < 1e-5:
        return pixels
    mean_gray = (mean_r + mean_g + mean_b) / 3.0
    scale_r = mean_gray / mean_r
    scale_g = mean_gray / mean_g
    scale_b = mean_gray / mean_b
    wb_pixels = pixels_f * np.array([scale_r, scale_g, scale_b], dtype=np.float32)
    return np.clip(wb_pixels, 0, 255).astype(np.uint8)


def apply_red_chroma_virtual_lighting(pixels: np.ndarray, light_boost: float = 0.45) -> np.ndarray:
    """Apply legacy virtual lighting with one channel-neutral gain.

    The function name remains for compatibility with older callers, but the same
    per-pixel gain is now applied to R, G, and B. This avoids suppressing green or
    overweighting red when the garment itself is not red.
    """
    if len(pixels) < 10:
        return pixels
    pixels_f = pixels.astype(np.float32)
    r, g, b = pixels_f[:, 0], pixels_f[:, 1], pixels_f[:, 2]

    luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
    virtual_gain = 1.0 + ((1.0 - luminance) ** 2) * light_boost

    lit_pixels = pixels_f * virtual_gain[:, np.newaxis]
    return np.clip(np.rint(lit_pixels), 0, 255).astype(np.uint8)


def build_lighting_corrected_white_composite(
    image: Image.Image,
    mask: Image.Image,
    method: str = "adaptive_virtual_light_median",
) -> Image.Image:
    """Preserve garment texture while applying the selected lighting correction.

    Only pixels inside the garment mask are corrected. The corrected garment is
    then composited on pure white so the FashionCLIP color-name input keeps the
    real folds, texture, and local shading without reintroducing the room
    background. The current server method uses a channel-neutral, per-pixel gain;
    it is intentionally not a color-chart white-balance calibration.
    """
    if method != "adaptive_virtual_light_median":
        raise ValueError(
            "Lighting-corrected CLIP input currently supports only "
            "'adaptive_virtual_light_median'."
        )

    rgb = load_rgb_array(image)
    resized_mask = mask.convert("L").resize(image.size, Image.Resampling.LANCZOS)
    mask_arr = np.asarray(resized_mask, dtype=np.uint8) >= 128
    foreground_count = int(mask_arr.sum())
    if foreground_count < MIN_RAW_MASK_PIXELS:
        raise ColorExtractionError(
            "Garment mask has "
            f"{foreground_count} foreground pixels; at least {MIN_RAW_MASK_PIXELS} are required."
        )

    corrected_rgb = rgb.copy()
    corrected_rgb[mask_arr] = apply_red_chroma_virtual_lighting(
        rgb[mask_arr],
        light_boost=0.45,
    )
    corrected_garment = Image.fromarray(corrected_rgb, mode="RGB")
    white_background = Image.new("RGB", image.size, (255, 255, 255))
    return Image.composite(corrected_garment, white_background, resized_mask)


def extract_red_saturation_weighted_rgb(pixels: np.ndarray) -> tuple[int, int, int]:
    """Return a channel-neutral median under the legacy helper name.

    Older code may import this helper directly. Keeping it as a compatibility alias
    prevents that path from reintroducing red-saturation weighting.
    """
    if len(pixels) == 0:
        raise ColorExtractionError("Cannot estimate color from an empty pixel array.")
    rgb = np.median(pixels, axis=0)

    rgb = np.clip(np.rint(rgb), 0, 255).astype(np.uint8)
    return int(rgb[0]), int(rgb[1]), int(rgb[2])


def apply_hsv_gamma_boost(rgb: tuple[int, int, int], gamma: float = 0.85) -> tuple[int, int, int]:
    """[조명 필터 3] 인간의 시각 인지 특성에 맞춰 명도(Value) 감마 업리프트(V' = V^gamma) 적용"""
    r, g, b = [c / 255.0 for c in rgb]
    max_c = max(r, g, b)
    min_c = min(r, g, b)
    delta = max_c - min_c
    if max_c == 0:
        return 0, 0, 0
    val = max_c
    sat = delta / max_c if max_c > 0 else 0
    val_boosted = min(1.0, val ** gamma)

    if delta == 0:
        hue = 0.0
    elif max_c == r:
        hue = (60 * ((g - b) / delta) + 360) % 360
    elif max_c == g:
        hue = 60 * ((b - r) / delta + 2)
    else:
        hue = 60 * ((r - g) / delta + 4)

    c = val_boosted * sat
    x = c * (1 - abs((hue / 60) % 2 - 1))
    m = val_boosted - c

    if 0 <= hue < 60:
        r_new, g_new, b_new = c, x, 0
    elif 60 <= hue < 120:
        r_new, g_new, b_new = x, c, 0
    elif 120 <= hue < 180:
        r_new, g_new, b_new = 0, c, x
    elif 180 <= hue < 240:
        r_new, g_new, b_new = 0, x, c
    elif 240 <= hue < 300:
        r_new, g_new, b_new = x, 0, c
    else:
        r_new, g_new, b_new = c, 0, x

    rgb_new = np.clip(np.rint(np.array([r_new + m, g_new + m, b_new + m]) * 255.0), 0, 255).astype(np.uint8)
    return int(rgb_new[0]), int(rgb_new[1]), int(rgb_new[2])


def select_kmeans_bright_cluster(pixels: np.ndarray, k: int = 3) -> np.ndarray:
    """[조명 필터 4] 명도 기반 픽셀 분위 분할로 그림자 그룹을 배제하고 조명을 잘 받은 주요 클러스터 픽셀 선택"""
    if len(pixels) < 30:
        return pixels
    luminance = (
        0.2126 * pixels[:, 0].astype(np.float32)
        + 0.7152 * pixels[:, 1].astype(np.float32)
        + 0.0722 * pixels[:, 2].astype(np.float32)
    )
    q33 = np.percentile(luminance, 33.3)
    bright_cluster = pixels[(luminance >= q33) & (luminance <= np.percentile(luminance, 85))]
    if len(bright_cluster) < 10:
        return pixels
    return bright_cluster


def representative_rgb(pixels: np.ndarray, method: str = "trimmed_median") -> tuple[int, int, int]:
    """Estimate representative RGB while preserving neutral and saturated hues."""
    if len(pixels) == 0:
        raise ColorExtractionError("Cannot estimate color from an empty pixel array.")
    if method == "mean":
        rgb = np.mean(pixels, axis=0)
    elif method == "median":
        rgb = np.median(pixels, axis=0)
    elif method == "trimmed_median":
        rgb = np.median(trim_by_luminance(pixels), axis=0)
    elif method == "bright_boosted_median":
        rgb = np.median(trim_by_bright_zone(pixels), axis=0)
    elif method == "adaptive_virtual_light_median":
        # Backward-compatible name; both lighting and aggregation are channel-neutral.
        lit_pixels = apply_red_chroma_virtual_lighting(pixels, light_boost=0.45)
        trimmed_lit = trim_by_luminance(lit_pixels)
        return extract_red_saturation_weighted_rgb(trimmed_lit)
    elif method == "auto_white_balance_gray_world":
        wb_pixels = apply_gray_world_wb(pixels)
        rgb = np.median(trim_by_bright_zone(wb_pixels), axis=0)
    elif method == "hsv_gamma_boost":
        base_rgb = np.median(trim_by_luminance(pixels), axis=0)
        base_tuple = (int(base_rgb[0]), int(base_rgb[1]), int(base_rgb[2]))
        return apply_hsv_gamma_boost(base_tuple, gamma=0.85)
    elif method == "kmeans_bright_cluster":
        cluster_pixels = select_kmeans_bright_cluster(pixels)
        rgb = np.median(cluster_pixels, axis=0)
    else:
        raise ValueError(f"Unknown color extraction method: {method}")
    rgb = np.clip(np.rint(rgb), 0, 255).astype(np.uint8)
    return int(rgb[0]), int(rgb[1]), int(rgb[2])


def extract_dominant_hex(image: Image.Image, mask: Image.Image, method: str = "trimmed_median") -> ColorResult:
    """Extract one HEX value from a sufficiently large garment mask.

    ``ColorResult`` is unchanged on success. Empty/tiny masks raise
    ``ColorExtractionError`` so segmentation failure cannot masquerade as black.
    A genuinely black mask region with at least 64 foreground pixels remains valid
    and therefore correctly produces ``#000000``.
    """
    pixels = filter_clothing_pixels(image, mask)
    rgb = representative_rgb(pixels, method=method)
    return ColorResult(
        hex_value=rgb_to_hex(rgb),
        rgb=rgb,
        pixel_count=int(len(pixels)),
        method=method,
    )


def classify_color_name(rgb: tuple[int, int, int]) -> str:
    r, g, b = [channel / 255.0 for channel in rgb]
    max_c = max(r, g, b)
    min_c = min(r, g, b)
    delta = max_c - min_c
    value = max_c
    saturation = 0.0 if max_c == 0 else delta / max_c

    if value <= 0.16:
        return "검은색"
    if saturation <= 0.13:
        if value >= 0.82:
            return "흰색"
        return "회색"

    if delta == 0:
        hue = 0.0
    elif max_c == r:
        hue = (60 * ((g - b) / delta) + 360) % 360
    elif max_c == g:
        hue = 60 * ((b - r) / delta + 2)
    else:
        hue = 60 * ((r - g) / delta + 4)

    if 18 <= hue < 48:
        if value >= 0.62 and saturation <= 0.45:
            return "베이지색"
        return "갈색"
    if 48 <= hue < 72:
        if saturation <= 0.45 and value <= 0.68:
            return "카키색"
        return "노란색"
    if 72 <= hue < 160:
        if saturation <= 0.42 and value <= 0.66:
            return "카키색"
        return "초록색"
    if 160 <= hue < 245:
        if value <= 0.42:
            return "네이비색"
        return "파란색"
    if 245 <= hue < 285:
        return "보라색"
    if 285 <= hue < 340:
        return "핑크색" if value >= 0.45 else "보라색"
    return "빨간색"


def srgb_to_linear(channel: np.ndarray) -> np.ndarray:
    channel = channel / 255.0
    return np.where(channel <= 0.04045, channel / 12.92, ((channel + 0.055) / 1.055) ** 2.4)


def rgb_to_xyz(rgb: tuple[int, int, int]) -> np.ndarray:
    linear = srgb_to_linear(np.asarray(rgb, dtype=np.float64))
    matrix = np.asarray([
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ])
    return matrix @ linear


def xyz_to_lab(xyz: np.ndarray) -> np.ndarray:
    white = np.asarray([0.95047, 1.00000, 1.08883])
    normalized = xyz / white
    epsilon = 216 / 24389
    kappa = 24389 / 27
    f = np.where(normalized > epsilon, np.cbrt(normalized), (kappa * normalized + 16) / 116)
    l = 116 * f[1] - 16
    a = 500 * (f[0] - f[1])
    b = 200 * (f[1] - f[2])
    return np.asarray([l, a, b], dtype=np.float64)


def delta_e_76(rgb_a: tuple[int, int, int], rgb_b: tuple[int, int, int]) -> float:
    lab_a = xyz_to_lab(rgb_to_xyz(rgb_a))
    lab_b = xyz_to_lab(rgb_to_xyz(rgb_b))
    return float(np.linalg.norm(lab_a - lab_b))


def rgb_distance(rgb_a: tuple[int, int, int], rgb_b: tuple[int, int, int]) -> float:
    return float(np.linalg.norm(np.asarray(rgb_a, dtype=np.float64) - np.asarray(rgb_b, dtype=np.float64)))


def rgb_to_hsv(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    arr = np.asarray(rgb, dtype=np.float64) / 255.0
    max_c = float(arr.max())
    min_c = float(arr.min())
    delta = max_c - min_c
    if delta == 0:
        hue = 0.0
    elif max_c == arr[0]:
        hue = (60 * ((arr[1] - arr[2]) / delta) + 360) % 360
    elif max_c == arr[1]:
        hue = 60 * ((arr[2] - arr[0]) / delta + 2)
    else:
        hue = 60 * ((arr[0] - arr[1]) / delta + 4)
    saturation = 0.0 if max_c == 0 else delta / max_c
    return float(hue), float(saturation), max_c


def hsv_distance(rgb_a: tuple[int, int, int], rgb_b: tuple[int, int, int]) -> tuple[float, float, float]:
    hue_a, sat_a, val_a = rgb_to_hsv(rgb_a)
    hue_b, sat_b, val_b = rgb_to_hsv(rgb_b)
    hue_diff = abs(hue_a - hue_b)
    hue_diff = min(hue_diff, 360 - hue_diff)
    return float(hue_diff), float(abs(sat_a - sat_b)), float(abs(val_a - val_b))
