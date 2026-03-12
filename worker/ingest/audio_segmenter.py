"""
Two-Pass Audio Segmentation Pipeline

Pass 1 (Silero VAD): Detect human activity windows in the audio track.
  - Lightweight ~1MB model loaded via torch.hub (no new pip dep — torch already installed)
  - Outputs clean (start_sec, end_sec) tuples with silence gaps merged

Pass 2a (Whisper): Transcribe each VAD segment.
  - Uses faster-whisper with the 'tiny' model by default
  - If word_count >= AUDIO_WORD_THRESHOLD (default 4) → segment_type = "speech"

Pass 2b (AST): Classify non-verbal segments.
  - MIT/ast-finetuned-audioset-10-10-0.4593 via HuggingFace transformers
  - 527 AudioSet classes, pure PyTorch — maps to clean categories (non_verbal, music, event, ...)

Env vars:
  WHISPER_MODEL_SIZE      tiny | base | small (default: tiny)
  AUDIO_WORD_THRESHOLD    min words to classify as speech (default: 4)
  VAD_MERGE_GAP_SECS      silence gap before splitting a VAD segment (default: 2.0)
  AUDIO_SEGMENT_MIN_SECS  discard VAD segments shorter than this (default: 1.0)
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)

TARGET_SR = 16_000  # 16kHz mono — standard for speech ML

# ---------------------------------------------------------------------------
# AudioSet label → clean segment_type category
# ---------------------------------------------------------------------------

_LABEL_TO_TYPE: dict[str, str] = {
    # speech
    "Speech": "speech", "Male speech, man speaking": "speech",
    "Female speech, woman speaking": "speech", "Child speech, kid speaking": "speech",
    "Conversation": "speech", "Narration, monologue": "speech",
    # non-verbal vocals
    "Scream": "non_verbal", "Shout": "non_verbal", "Crying, sobbing": "non_verbal",
    "Laughter": "non_verbal", "Whispering": "non_verbal", "Singing": "non_verbal",
    "Groan": "non_verbal", "Grunt": "non_verbal",
    # music
    "Music": "music", "Musical instrument": "music", "Electronic music": "music",
    "Pop music": "music", "Hip hop music": "music", "Rock music": "music",
    "Background music": "music",
    # crowd / ambient
    "Crowd": "ambient", "Applause": "ambient", "Chatter": "ambient",
    "Hubbub, speech noise, speech babble": "ambient",
    # alert / event
    "Gunshot, gunfire": "event", "Explosion": "event",
    "Alarm": "event", "Siren": "event",
    "Smoke detector, smoke alarm": "event", "Fire alarm": "event",
}


def _label_to_segment_type(top_label: str, all_labels: list[str]) -> str:
    """Map AudioSet top label → clean segment_type. Falls back to 'non_verbal'."""
    for label in [top_label] + all_labels:
        if label in _LABEL_TO_TYPE:
            return _LABEL_TO_TYPE[label]
    return "non_verbal"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class AudioSegment:
    segment_index: int
    start_sec: float
    end_sec: float

    # Classification
    segment_type: str = "unknown"     # speech | non_verbal | music | ambient | event | silence

    # Speech path (Pass 2a)
    transcript: Optional[str] = None
    transcript_words: int = 0

    # Non-verbal path (Pass 2b)
    event_top: Optional[str] = None
    event_labels: List[str] = field(default_factory=list)
    event_scores: List[float] = field(default_factory=list)

    # Per-segment DSP features (mirrors audio_extractor.py fields)
    mfcc_mean: List[float] = field(default_factory=list)
    mfcc_std: List[float] = field(default_factory=list)
    mel_mean_db: float = 0.0
    dominant_pitch_class: int = 0
    rms_energy: float = 0.0
    speech_band_power: float = 0.0
    peak_frequency_hz: float = 0.0
    has_speech: bool = False


def segment_for_timestamp(segments: List[AudioSegment], ts: float) -> Optional[AudioSegment]:
    """
    Binary search for the AudioSegment that contains timestamp ts.
    Falls back to the nearest segment if ts is in a gap.
    O(log n).
    """
    if not segments:
        return None
    lo, hi = 0, len(segments) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        seg = segments[mid]
        if ts < seg.start_sec:
            hi = mid - 1
        elif ts > seg.end_sec:
            lo = mid + 1
        else:
            return seg
    # ts falls in a silence gap — return the segment with the nearest midpoint
    return min(segments, key=lambda s: abs((s.start_sec + s.end_sec) / 2 - ts))


def segment_to_payload(seg: AudioSegment, total_segments: int) -> dict:
    """Convert an AudioSegment to a flat Qdrant payload dict."""
    payload: dict = {
        "audio_segment_index": seg.segment_index,
        "audio_segment_start_sec": seg.start_sec,
        "audio_segment_end_sec": seg.end_sec,
        "audio_segment_type": seg.segment_type,
        "audio_segment_count": total_segments,
        "audio_segment_method": "vad_two_pass",
        # DSP features — per-segment, same field names as audio_extractor.py
        "audio_mfcc_mean": seg.mfcc_mean,
        "audio_mfcc_std": seg.mfcc_std,
        "audio_mel_mean_db": seg.mel_mean_db,
        "audio_dominant_pitch_class": seg.dominant_pitch_class,
        "audio_rms_energy": seg.rms_energy,
        "audio_speech_band_power": seg.speech_band_power,
        "audio_peak_frequency_hz": seg.peak_frequency_hz,
        "audio_has_speech": seg.has_speech,
    }
    if seg.transcript:
        payload["audio_transcript"] = seg.transcript
        payload["audio_transcript_words"] = seg.transcript_words
    if seg.event_top:
        payload["audio_event_top"] = seg.event_top
        payload["audio_event_labels"] = seg.event_labels
        payload["audio_event_scores"] = seg.event_scores
    return payload


# ---------------------------------------------------------------------------
# DSP feature extraction (per-segment slice)
# ---------------------------------------------------------------------------

def _extract_dsp_features(y: np.ndarray, sr: int) -> dict:
    """
    Extract per-segment DSP features using librosa + scipy.
    Same logic as audio_extractor.py — applied to a waveform slice.
    Returns empty dict if the slice is too short.
    """
    if len(y) / sr < 0.1:
        return {}

    try:
        import librosa
        from scipy import signal as scipy_signal
    except ImportError:
        return {}

    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
    mel_spec = librosa.feature.melspectrogram(y=y, sr=sr, n_mels=64)
    chroma = librosa.feature.chroma_stft(y=y, sr=sr)
    rms = float(librosa.feature.rms(y=y).mean())

    nyq = sr / 2
    low, high = 300 / nyq, 3400 / nyq
    sos = scipy_signal.butter(4, [low, high], btype="band", output="sos")
    y_filtered = scipy_signal.sosfiltfilt(sos, y)
    freqs, psd = scipy_signal.welch(y_filtered, fs=sr, nperseg=512)

    return {
        "mfcc_mean": mfcc.mean(axis=1).tolist(),
        "mfcc_std": mfcc.std(axis=1).tolist(),
        "mel_mean_db": round(float(librosa.power_to_db(mel_spec).mean()), 2),
        "dominant_pitch_class": int(chroma.mean(axis=1).argmax()),
        "rms_energy": round(rms, 6),
        "speech_band_power": round(float(np.trapz(psd, freqs)), 6),
        "peak_frequency_hz": round(float(freqs[np.argmax(psd)]), 1),
        "has_speech": rms > 0.01,
    }


# ---------------------------------------------------------------------------
# Pass 1 — Silero VAD
# ---------------------------------------------------------------------------

def _run_vad(
    y_full: np.ndarray,
    sr: int,
    merge_gap_secs: float,
) -> Optional[List[Tuple[float, float]]]:
    """
    Run Silero VAD on a pre-loaded waveform (avoids torchaudio dep).

    Returns list of (start_sec, end_sec) after merging gaps <= merge_gap_secs.
    Returns None on failure (caller falls back to whole-file single segment).
    """
    try:
        import torch
        model, utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            force_reload=False,
            onnx=False,
            verbose=False,
            trust_repo=True,
        )
        get_speech_timestamps = utils[0]
    except Exception as exc:
        log.warning("[VAD] Silero load failed (%s) — treating whole file as one segment", exc)
        return None

    try:
        import torch
        audio_tensor = torch.FloatTensor(y_full)
        timestamps = get_speech_timestamps(
            audio_tensor,
            model,
            sampling_rate=sr,
            threshold=0.5,
            min_speech_duration_ms=250,
            min_silence_duration_ms=int(merge_gap_secs * 1000),
            return_seconds=True,
        )
    except Exception as exc:
        log.warning("[VAD] VAD inference failed (%s) — treating whole file as one segment", exc)
        return None

    if not timestamps:
        return []

    # Merge nearby segments that slipped through the min_silence_duration gap
    merged = [{"start": timestamps[0]["start"], "end": timestamps[0]["end"]}]
    for seg in timestamps[1:]:
        if seg["start"] - merged[-1]["end"] <= merge_gap_secs:
            merged[-1]["end"] = seg["end"]
        else:
            merged.append({"start": seg["start"], "end": seg["end"]})

    return [(s["start"], s["end"]) for s in merged]


# ---------------------------------------------------------------------------
# Pass 2a — Whisper (faster-whisper)
# ---------------------------------------------------------------------------

_whisper_model = None
_whisper_model_size: Optional[str] = None


def _run_whisper(
    y_full: np.ndarray,
    sr: int,
    start: float,
    end: float,
    model_size: str,
) -> Tuple[str, int]:
    """
    Transcribe a waveform slice using faster-whisper.
    Returns (transcript, word_count). Returns ("", 0) on any failure.
    """
    global _whisper_model, _whisper_model_size

    try:
        from faster_whisper import WhisperModel
        import soundfile as sf
    except ImportError:
        log.debug("[Whisper] faster-whisper not installed — skipping transcription")
        return "", 0

    if _whisper_model is None or _whisper_model_size != model_size:
        try:
            _whisper_model = WhisperModel(model_size, device="cpu", compute_type="int8")
            _whisper_model_size = model_size
            log.info("[Whisper] Loaded model: %s", model_size)
        except Exception as exc:
            log.warning("[Whisper] Model load failed (%s) — skipping transcription", exc)
            return "", 0

    try:
        start_sample = int(start * sr)
        end_sample = int(end * sr)
        y_slice = y_full[start_sample:end_sample]

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            sf.write(tmp.name, y_slice, sr)
            tmp_path = tmp.name

        try:
            segments_iter, _ = _whisper_model.transcribe(
                tmp_path,
                beam_size=1,
                language=None,  # auto-detect
                vad_filter=False,  # VAD already done in Pass 1
            )
            transcript = " ".join(seg.text.strip() for seg in segments_iter).strip()
        finally:
            os.unlink(tmp_path)

        word_count = len(transcript.split()) if transcript else 0
        return transcript, word_count

    except Exception as exc:
        log.debug("[Whisper] Transcription failed for slice %.1f-%.1fs: %s", start, end, exc)
        return "", 0


# ---------------------------------------------------------------------------
# Pass 2b — AST Audio Classifier
# ---------------------------------------------------------------------------

_ast_pipeline = None


def _run_ast(
    y_full: np.ndarray,
    sr: int,
    start: float,
    end: float,
) -> Tuple[str, List[str], List[float]]:
    """
    Classify a waveform slice using the Audio Spectrogram Transformer.
    Model: MIT/ast-finetuned-audioset-10-10-0.4593 (527 AudioSet classes, PyTorch)
    Returns (top_label, labels[:3], scores[:3]). Returns ("unknown", [], []) on failure.
    """
    global _ast_pipeline

    try:
        from transformers import pipeline as hf_pipeline
    except ImportError:
        log.debug("[AST] transformers not installed — skipping audio classification")
        return "unknown", [], []

    if _ast_pipeline is None:
        try:
            _ast_pipeline = hf_pipeline(
                "audio-classification",
                model="MIT/ast-finetuned-audioset-10-10-0.4593",
                device="cpu",
                top_k=5,
            )
            log.info("[AST] Loaded MIT/ast-finetuned-audioset-10-10-0.4593")
        except Exception as exc:
            log.warning("[AST] Model load failed (%s) — skipping audio classification", exc)
            return "unknown", [], []

    try:
        start_sample = int(start * sr)
        end_sample = int(end * sr)
        y_slice = y_full[start_sample:end_sample]

        results = _ast_pipeline({"array": y_slice.astype(np.float32), "sampling_rate": sr})

        labels = [r["label"] for r in results]
        scores = [round(float(r["score"]), 4) for r in results]
        top_label = labels[0] if labels else "unknown"

        return top_label, labels[:3], scores[:3]

    except Exception as exc:
        log.debug("[AST] Classification failed for slice %.1f-%.1fs: %s", start, end, exc)
        return "unknown", [], []


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def extract_audio_segments(
    video_path: str,
    whisper_model_size: Optional[str] = None,
    word_threshold: Optional[int] = None,
    vad_merge_gap: Optional[float] = None,
    min_segment_secs: Optional[float] = None,
) -> Optional[List[AudioSegment]]:
    """
    Two-pass audio segmentation pipeline.

    Pass 1: Silero VAD detects human activity windows.
    Pass 2a: Whisper transcribes each window — word_count >= threshold → "speech".
    Pass 2b: AST classifies non-verbal windows → non_verbal / music / ambient / event.

    Args:
        video_path:         Local path to the video file.
        whisper_model_size: faster-whisper model size (default: WHISPER_MODEL_SIZE env or "tiny").
        word_threshold:     Min words for speech classification (default: AUDIO_WORD_THRESHOLD or 4).
        vad_merge_gap:      Seconds of silence to merge across (default: VAD_MERGE_GAP_SECS or 2.0).
        min_segment_secs:   Discard VAD segments shorter than this (default: AUDIO_SEGMENT_MIN_SECS or 1.0).

    Returns:
        List of AudioSegment objects, or None if no audio track / extraction failure.
    """
    whisper_model_size = whisper_model_size or os.getenv("WHISPER_MODEL_SIZE", "tiny")
    word_threshold = word_threshold if word_threshold is not None else int(os.getenv("AUDIO_WORD_THRESHOLD", "4"))
    vad_merge_gap = vad_merge_gap if vad_merge_gap is not None else float(os.getenv("VAD_MERGE_GAP_SECS", "2.0"))
    min_segment_secs = min_segment_secs if min_segment_secs is not None else float(os.getenv("AUDIO_SEGMENT_MIN_SECS", "1.0"))

    try:
        import ffmpeg
        import librosa
        import soundfile as sf
    except ImportError as exc:
        log.warning("[AudioSeg] Missing dependencies (%s) — skipping", exc)
        return None

    with tempfile.TemporaryDirectory() as tmpdir:
        wav_path = os.path.join(tmpdir, "audio.wav")

        # --- Extract audio via FFmpeg ---
        try:
            (
                ffmpeg
                .input(video_path)
                .output(wav_path, ar=TARGET_SR, ac=1, format="wav", loglevel="error")
                .run(overwrite_output=True, capture_stdout=True, capture_stderr=True)
            )
        except Exception as exc:
            log.debug("[AudioSeg] FFmpeg extraction failed for %s: %s", video_path, exc)
            return None

        if not os.path.exists(wav_path) or os.path.getsize(wav_path) < 1024:
            return None

        # --- Load full waveform (needed for slicing in Passes 2a/2b) ---
        try:
            y_full, sr = librosa.load(wav_path, sr=TARGET_SR, mono=True)
        except Exception as exc:
            log.debug("[AudioSeg] librosa load failed: %s", exc)
            return None

        total_duration = len(y_full) / sr
        if total_duration < 0.5:
            return None

        # ---------------------------------------------------------------
        # Pass 1: Silero VAD
        # ---------------------------------------------------------------
        vad_result = _run_vad(y_full, sr, merge_gap_secs=vad_merge_gap)

        if vad_result is None:
            # VAD failed entirely — treat whole file as one segment, fall through to Pass 2
            vad_windows = [(0.0, total_duration)]
        elif len(vad_result) == 0:
            # No human activity — return a single silence segment with DSP features
            log.debug("[AudioSeg] VAD found no human activity in %s", video_path)
            seg = AudioSegment(segment_index=0, start_sec=0.0, end_sec=total_duration, segment_type="silence")
            dsp = _extract_dsp_features(y_full, sr)
            for k, v in dsp.items():
                setattr(seg, k, v)
            return [seg]
        else:
            vad_windows = vad_result

        # Drop segments that are too short (noise bursts)
        vad_windows = [(s, e) for s, e in vad_windows if (e - s) >= min_segment_secs]
        if not vad_windows:
            vad_windows = [(0.0, total_duration)]

        # ---------------------------------------------------------------
        # Pass 2: Per-segment classification
        # ---------------------------------------------------------------
        segments: List[AudioSegment] = []

        for idx, (start, end) in enumerate(vad_windows):
            seg = AudioSegment(segment_index=idx, start_sec=start, end_sec=end)

            # Per-segment DSP features
            start_sample = int(start * sr)
            end_sample = int(min(end, total_duration) * sr)
            y_slice = y_full[start_sample:end_sample]
            if len(y_slice) > 0:
                dsp = _extract_dsp_features(y_slice, sr)
                for k, v in dsp.items():
                    setattr(seg, k, v)

            # Pass 2a: Whisper
            transcript, word_count = _run_whisper(y_full, sr, start, end, model_size=whisper_model_size)

            if word_count >= word_threshold:
                seg.segment_type = "speech"
                seg.transcript = transcript
                seg.transcript_words = word_count
                seg.has_speech = True
            else:
                # Pass 2b: AST classifier
                top_label, ast_labels, ast_scores = _run_ast(y_full, sr, start, end)
                seg.segment_type = _label_to_segment_type(top_label, ast_labels)
                seg.event_top = top_label
                seg.event_labels = ast_labels
                seg.event_scores = ast_scores

            segments.append(seg)
            log.debug(
                "[AudioSeg] seg[%d] %.1f–%.1fs  type=%-10s  words=%d  event=%s",
                idx, start, end, seg.segment_type, seg.transcript_words, seg.event_top,
            )

        log.info(
            "[AudioSeg] %s → %d segments  (speech=%d non_verbal=%d music=%d ambient=%d event=%d silence=%d)",
            video_path, len(segments),
            sum(1 for s in segments if s.segment_type == "speech"),
            sum(1 for s in segments if s.segment_type == "non_verbal"),
            sum(1 for s in segments if s.segment_type == "music"),
            sum(1 for s in segments if s.segment_type == "ambient"),
            sum(1 for s in segments if s.segment_type == "event"),
            sum(1 for s in segments if s.segment_type == "silence"),
        )

        return segments
