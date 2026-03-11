"""
Synthetic Sensor Ingestor — simulates continuous heart-rate / activity streams
synced to media file timestamps.

This demonstrates the sensor-fusion architecture pattern used in medical device
platforms (e.g. Masimo): "how does a visual scene correlate with a physiological
signal?"

The synthetic generator produces plausible HR/activity data co-located with
media capture times. Real sensor data (e.g. from an Apple Watch export CSV,
Garmin FIT file, or HL7 FHIR stream) would slot in at _parse_sensor_csv().

Qdrant payload fields added:
  sensor_avg_hr        — average heart rate during photo/video capture window
  sensor_max_hr        — peak heart rate
  sensor_activity_label — "rest" | "light" | "moderate" | "vigorous"
  sensor_stress_index  — 0.0–1.0 derived from HR variability approximation
"""

from __future__ import annotations

import csv
import logging
import math
import os
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_ACTIVITY_THRESHOLDS = {
    "rest": (40, 70),
    "light": (70, 100),
    "moderate": (100, 140),
    "vigorous": (140, 200),
}


# ---------------------------------------------------------------------------
# Generator: produce synthetic sensor CSV from media file timestamps
# ---------------------------------------------------------------------------

def generate_synthetic_sensor_csv(
    media_timestamps: list[datetime],
    output_path: str,
    seed: int = 42,
) -> str:
    """
    Generate a synthetic heart-rate CSV aligned to media timestamps.

    Args:
        media_timestamps: list of datetime objects from media EXIF/metadata
        output_path: where to write the CSV
        seed: random seed for reproducibility

    Returns:
        Path to the written CSV file.
    """
    random.seed(seed)
    if not media_timestamps:
        raise ValueError("media_timestamps must not be empty")

    start = min(media_timestamps) - timedelta(minutes=5)
    end = max(media_timestamps) + timedelta(minutes=5)

    rows = []
    current = start
    # Simulate walking pattern: HR oscillates with a physiological trend
    base_hr = 75.0
    trend = 0.0

    while current <= end:
        trend += random.gauss(0, 0.5)
        trend = max(-20, min(20, trend))  # clamp drift
        hr = base_hr + trend + random.gauss(0, 3)
        hr = max(40, min(200, hr))

        rows.append({
            "timestamp": current.isoformat(),
            "heart_rate_bpm": round(hr, 1),
            "activity_level": _classify_activity(hr),
            "stress_index": round(_stress_index(hr, base_hr), 3),
        })
        current += timedelta(seconds=5)  # 5-second sample interval

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "heart_rate_bpm", "activity_level", "stress_index"])
        writer.writeheader()
        writer.writerows(rows)

    log.info("Generated %d sensor rows → %s", len(rows), output_path)
    return output_path


def _classify_activity(hr: float) -> str:
    for label, (low, high) in _ACTIVITY_THRESHOLDS.items():
        if low <= hr < high:
            return label
    return "vigorous"


def _stress_index(hr: float, baseline: float) -> float:
    """Simple stress proxy: normalized deviation from baseline."""
    return min(1.0, abs(hr - baseline) / 60.0)


# ---------------------------------------------------------------------------
# Ingestor: read CSV and return payload fields for a given media timestamp
# ---------------------------------------------------------------------------

class SensorPayloadEnricher:
    """
    Loads a sensor CSV and provides lookup_for_timestamp() to get
    the sensor payload fields for a given media capture time.

    Usage in tasks.py:
        enricher = SensorPayloadEnricher("data/sensor_data.csv")
        sensor_fields = enricher.lookup_for_timestamp(file_created_at)
        # merge into Qdrant payload
    """

    def __init__(self, csv_path: str) -> None:
        self._readings: list[dict] = []
        self._load(csv_path)

    def _load(self, csv_path: str) -> None:
        if not os.path.exists(csv_path):
            log.warning("Sensor CSV not found: %s", csv_path)
            return
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                self._readings.append({
                    "ts": datetime.fromisoformat(row["timestamp"]),
                    "hr": float(row["heart_rate_bpm"]),
                    "activity": row["activity_level"],
                    "stress": float(row["stress_index"]),
                })
        log.info("Loaded %d sensor readings from %s", len(self._readings), csv_path)

    def lookup_for_timestamp(
        self,
        media_ts: datetime,
        window_secs: float = 30.0,
    ) -> Optional[dict]:
        """
        Return aggregated sensor payload for a ±window_secs window
        around the media capture timestamp.
        """
        window = [
            r for r in self._readings
            if abs((r["ts"] - media_ts).total_seconds()) <= window_secs
        ]
        if not window:
            return None

        hrs = [r["hr"] for r in window]
        return {
            "sensor_avg_hr": round(sum(hrs) / len(hrs), 1),
            "sensor_max_hr": round(max(hrs), 1),
            "sensor_activity_label": window[len(window) // 2]["activity"],
            "sensor_stress_index": round(
                sum(r["stress"] for r in window) / len(window), 3
            ),
        }
