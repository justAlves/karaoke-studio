"""Separação em três fontes e estimativa de tonalidade."""

from __future__ import annotations

import gc
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parent
STEM_DIR = ROOT / "stems"
MODEL_DIR = ROOT / "models"
VOCAL_MODEL = "UVR-MDX-NET-Inst_HQ_5.onnx"
BACKING_MODEL = "UVR-BVE-4B_SN-44100-2.pth"
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")

PITCH_NAMES = ("Dó", "Dó♯", "Ré", "Mi♭", "Mi", "Fá", "Fá♯", "Sol", "Lá♭", "Lá", "Si♭", "Si")
MAJOR_PROFILE = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
MINOR_PROFILE = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)
MAJOR_INTERVALS = (0, 2, 4, 5, 7, 9, 11)
MINOR_INTERVALS = (0, 2, 3, 5, 7, 8, 10)


def _complete(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _separator(output_dir: Path, vr: bool = False):
    from audio_separator.separator import Separator

    MODEL_DIR.mkdir(exist_ok=True)
    options = {
        "model_file_dir": str(MODEL_DIR),
        "output_dir": str(output_dir),
        "output_format": "FLAC",
        "use_soundfile": True,
        "log_level": logging.WARNING,
    }
    if vr:
        options["vr_params"] = {
            "batch_size": 1,
            "window_size": 512,
            "aggression": 0,
            "enable_tta": False,
            "enable_post_process": False,
            "post_process_threshold": 0.2,
            "high_end_process": False,
        }
    return Separator(**options)


def separate_and_analyze(source: Path, video_id: str, on_stage: Callable[[str], None]) -> dict:
    """Gera instrumental, voz principal e apoio em FLAC, depois estima a escala."""
    if not VIDEO_ID.fullmatch(video_id) or not _complete(source):
        raise ValueError("Arquivo de áudio inválido para processamento.")

    target = STEM_DIR / video_id
    target.mkdir(parents=True, exist_ok=True)
    instrumental = target / "instrumental.flac"
    vocals = target / "vocals.flac"
    lead = target / "lead_vocals.flac"
    backing = target / "backing_vocals.flac"

    if not (_complete(instrumental) and _complete(vocals)):
        on_stage("preparing_audio")
        prepared = target / "source.wav"
        temporary = target / "source.tmp.wav"
        if not _complete(prepared):
            command = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(temporary),
            ]
            process = subprocess.run(command, capture_output=True, timeout=600, check=False)
            if process.returncode or not _complete(temporary):
                raise RuntimeError(f"Não foi possível converter o áudio: {process.stderr.decode(errors='replace')[-300:]}")
            os.replace(temporary, prepared)
        on_stage("separating_instrumental")
        separator = _separator(target)
        try:
            separator.load_model(VOCAL_MODEL)
            separator.separate(str(prepared), {"Instrumental": "instrumental", "Vocals": "vocals"})
        finally:
            del separator
            gc.collect()
        if not (_complete(instrumental) and _complete(vocals)):
            raise RuntimeError("A separação de instrumental e vocais não gerou os arquivos esperados.")
        prepared.unlink(missing_ok=True)

    if not (_complete(lead) and _complete(backing)):
        on_stage("separating_backing")
        separator = _separator(target, vr=True)
        try:
            separator.load_model(BACKING_MODEL)
            # Neste modelo BVE, a saída rotulada Vocals contém o apoio;
            # a saída complementar Instrumental contém a voz principal.
            separator.separate(str(vocals), {"Vocals": "backing_vocals", "Instrumental": "lead_vocals"})
        finally:
            del separator
            gc.collect()
        if not (_complete(lead) and _complete(backing)):
            raise RuntimeError("A separação de voz principal e apoio não gerou os arquivos esperados.")

    on_stage("analyzing_key")
    key = estimate_key(instrumental)
    return {
        "stems": {
            "instrumental": f"{video_id}/instrumental.flac",
            "lead_vocals": f"{video_id}/lead_vocals.flac",
            "backing_vocals": f"{video_id}/backing_vocals.flac",
        },
        "key": key,
    }


def prepare_karaoke_audio(video_id: str) -> str:
    """Mistura o instrumental e as vozes de apoio para reprodução no navegador."""
    if not VIDEO_ID.fullmatch(video_id):
        raise ValueError("Vídeo inválido.")
    target = STEM_DIR / video_id
    instrumental = target / "instrumental.flac"
    backing = target / "backing_vocals.flac"
    output = target / "karaoke.m4a"
    temporary = target / "karaoke.tmp.m4a"
    if not (_complete(instrumental) and _complete(backing)):
        raise RuntimeError("As faixas necessárias para o karaokê não estão disponíveis.")
    if not _complete(output):
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(instrumental), "-i", str(backing),
            "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.95[a]",
            "-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(temporary),
        ]
        process = subprocess.run(command, capture_output=True, timeout=600, check=False)
        if process.returncode or not _complete(temporary):
            raise RuntimeError(f"Não foi possível montar o áudio do karaokê: {process.stderr.decode(errors='replace')[-300:]}")
        os.replace(temporary, output)
    return f"{video_id}/karaoke.m4a"


def estimate_key(audio_path: Path) -> dict | None:
    """Compara o cromagrama com perfis de tonalidades maiores e menores."""
    import librosa
    import numpy as np

    signal, sample_rate = librosa.load(audio_path, sr=22050, mono=True)
    if signal.size < sample_rate * 4 or np.max(np.abs(signal)) < 1e-4:
        return None

    frame_length, hop_length = 4096, 2048
    chroma = librosa.feature.chroma_stft(
        y=signal, sr=sample_rate, n_fft=frame_length, hop_length=hop_length, tuning=0.0
    )
    energy = librosa.feature.rms(y=signal, frame_length=frame_length, hop_length=hop_length)[0]
    frames = min(chroma.shape[1], energy.size)
    chroma, energy = chroma[:, :frames], energy[:frames]
    mask = energy > max(float(np.percentile(energy, 30)), 1e-4)
    if not np.any(mask):
        return None

    pitch_energy = np.average(chroma[:, mask], axis=1, weights=energy[mask])
    centered = pitch_energy - pitch_energy.mean()
    magnitude = np.linalg.norm(centered)
    if magnitude < 1e-5:
        return None

    candidates = []
    for mode, profile in (("maior", MAJOR_PROFILE), ("menor", MINOR_PROFILE)):
        for tonic in range(12):
            rotated = np.roll(np.asarray(profile, dtype=float), tonic)
            rotated -= rotated.mean()
            score = float(np.dot(centered, rotated) / (magnitude * np.linalg.norm(rotated)))
            candidates.append((score, tonic, mode))
    candidates.sort(reverse=True)
    best, second = candidates[:2]
    if best[0] < 0.2:
        return None

    _, tonic, mode = best
    intervals = MAJOR_INTERVALS if mode == "maior" else MINOR_INTERVALS
    return {
        "tonic": PITCH_NAMES[tonic],
        "mode": mode,
        "label": f"{PITCH_NAMES[tonic]} {mode}",
        "scale": [PITCH_NAMES[(tonic + interval) % 12] for interval in intervals],
        "certainty": "baixa" if best[0] - second[0] < 0.05 else "moderada",
        "alternative": f"{PITCH_NAMES[second[1]]} {second[2]}",
    }
