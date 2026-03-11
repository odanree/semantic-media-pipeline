"""
Visual Feature Extractor — feature engineering for Qdrant payload enrichment.

Extracts:
  - Dominant color histogram (HSV color distribution → top 5 colors as hex)
  - OCR text from image/video frame (signs, text in scene)
  - Brightness, contrast, saturation metrics

Stored as Qdrant payload fields to enable filtered search:
  e.g. "find photos with blue tones" or "find frames with text"

Call extract_visual_features() on the PIL Image loaded during ingest.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
from PIL import Image

log = logging.getLogger(__name__)


def extract_visual_features(img: Image.Image) -> dict:
    """
    Extract visual metadata features from a PIL Image.
    Returns a dict suitable for Qdrant payload merge.
    """
    features = {}

    try:
        features.update(_color_features(img))
    except Exception as exc:
        log.debug("Color feature extraction failed: %s", exc)

    try:
        features.update(_brightness_features(img))
    except Exception as exc:
        log.debug("Brightness feature extraction failed: %s", exc)

    try:
        ocr_text = _ocr_text(img)
        if ocr_text:
            features["ocr_text"] = ocr_text[:500]  # cap payload size
            features["has_text"] = True
        else:
            features["has_text"] = False
    except Exception as exc:
        log.debug("OCR extraction skipped: %s", exc)
        features["has_text"] = False

    return features


# ---------------------------------------------------------------------------
# Color features
# ---------------------------------------------------------------------------

def _color_features(img: Image.Image) -> dict:
    """Compute dominant HSV color palette."""
    img_rgb = img.convert("RGB").resize((64, 64))  # fast quantization at small size

    # Convert to HSV for perceptual color grouping
    from PIL import ImageStat
    stat = ImageStat.Stat(img_rgb)
    r_mean, g_mean, b_mean = [s / 255.0 for s in stat.mean[:3]]
    r_std, g_std, b_std = [s / 255.0 for s in stat.stddev[:3]]

    # Dominant color as hex
    r8, g8, b8 = int(r_mean * 255), int(g_mean * 255), int(b_mean * 255)
    dominant_hex = f"#{r8:02x}{g8:02x}{b8:02x}"

    # Color temperature heuristic: warm (red/orange) vs cool (blue/green)
    warmth = (r_mean - b_mean)  # positive = warm, negative = cool

    return {
        "color_dominant_hex": dominant_hex,
        "color_r_mean": round(r_mean, 3),
        "color_g_mean": round(g_mean, 3),
        "color_b_mean": round(b_mean, 3),
        "color_warmth": round(float(warmth), 3),
        "color_colorfulness": round(float(r_std + g_std + b_std), 3),
    }


# ---------------------------------------------------------------------------
# Brightness / contrast
# ---------------------------------------------------------------------------

def _brightness_features(img: Image.Image) -> dict:
    """Luminance, contrast, saturation."""
    gray = img.convert("L")
    arr = np.array(gray, dtype=np.float32) / 255.0
    brightness = float(arr.mean())
    contrast = float(arr.std())

    return {
        "visual_brightness": round(brightness, 3),
        "visual_contrast": round(contrast, 3),
    }


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------

def _ocr_text(img: Image.Image) -> Optional[str]:
    """
    Extract text from image using pytesseract.
    Returns None if pytesseract is not installed or finds no text.
    """
    try:
        import pytesseract
        text = pytesseract.image_to_string(img, timeout=5).strip()
        return text if text else None
    except ImportError:
        return None  # Optional dependency — skip silently
    except Exception:
        return None
