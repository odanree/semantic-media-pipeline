"""
Audio Feature Extractor — DSP feature engineering for video files.

Extracts audio from video using FFmpeg, then computes:
  - MFCC (Mel-Frequency Cepstral Coefficients) — canonical DSP audio features
  - Mel spectrogram — time-frequency representation
  - Chroma — harmonic/pitch content
  - Band-pass filtering + Power Spectral Density via scipy.signal
  - Speech presence detection via energy threshold

Features are stored as Qdrant payload fields for filtered search.
Call extract_audio_features() from tasks.py after frame extraction.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

# Sample rate for all audio analysis — 16kHz is standard for speech/audio ML
TARGET_SR = 16_000


def extract_audio_features(video_path: str) -> Optional[dict]:
    """
    Extract DSP audio features from a video file.

    Returns a dict of Qdrant payload fields, or None if the video
    has no audio track or extraction fails.
    """
    try:
        import ffmpeg
        import librosa
        import soundfile as sf
        from scipy import signal as scipy_signal
    except ImportError as exc:
        log.warning("Audio extraction dependencies missing (%s) — skipping", exc)
        return None

    with tempfile.TemporaryDirectory() as tmpdir:
        wav_path = os.path.join(tmpdir, "audio.wav")

        # --- Extract audio track via FFmpeg ---
        try:
            (
                ffmpeg
                .input(video_path)
                .output(wav_path, ar=TARGET_SR, ac=1, format="wav", loglevel="error")
                .run(overwrite_output=True, capture_stdout=True, capture_stderr=True)
            )
        except Exception as exc:
            log.debug("FFmpeg audio extraction failed for %s: %s", video_path, exc)
            return None  # no audio track

        if not os.path.exists(wav_path) or os.path.getsize(wav_path) < 1024:
            return None  # empty audio

        # --- Load waveform ---
        try:
            y, sr = librosa.load(wav_path, sr=TARGET_SR, mono=True)
        except Exception as exc:
            log.debug("librosa load failed: %s", exc)
            return None

        duration = len(y) / sr
        if duration < 0.5:
            return None  # too short to analyze

        # --- MFCC (13 coefficients — standard for audio ML) ---
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
        mfcc_mean = mfcc.mean(axis=1).tolist()
        mfcc_std = mfcc.std(axis=1).tolist()

        # --- Mel spectrogram (mean energy per band) ---
        mel_spec = librosa.feature.melspectrogram(y=y, sr=sr, n_mels=64)
        mel_mean = float(librosa.power_to_db(mel_spec).mean())

        # --- Chroma (harmonic content) ---
        chroma = librosa.feature.chroma_stft(y=y, sr=sr)
        dominant_pitch_class = int(chroma.mean(axis=1).argmax())

        # --- RMS energy (overall loudness) ---
        rms = float(librosa.feature.rms(y=y).mean())

        # --- scipy.signal: band-pass filter + PSD ---
        # Band-pass: 300Hz–3400Hz (telephone/speech band) — same technique used for
        # biomedical audio (lung sounds, vocal biomarkers)
        nyq = sr / 2
        low, high = 300 / nyq, 3400 / nyq
        sos = scipy_signal.butter(4, [low, high], btype="band", output="sos")
        y_filtered = scipy_signal.sosfiltfilt(sos, y)

        # Welch PSD — power spectral density (same method used for HRV, EEG band powers)
        freqs, psd = scipy_signal.welch(y_filtered, fs=sr, nperseg=512)
        total_power = float(np.trapz(psd, freqs))
        peak_freq = float(freqs[np.argmax(psd)])

        # --- Speech presence (energy-based heuristic) ---
        has_speech = rms > 0.01

        return {
            "audio_mfcc_mean": mfcc_mean,
            "audio_mfcc_std": mfcc_std,
            "audio_mel_mean_db": round(mel_mean, 2),
            "audio_dominant_pitch_class": dominant_pitch_class,
            "audio_rms_energy": round(rms, 6),
            "audio_speech_band_power": round(total_power, 6),
            "audio_peak_frequency_hz": round(peak_freq, 1),
            "audio_has_speech": has_speech,
            "audio_duration_secs": round(duration, 2),
        }
