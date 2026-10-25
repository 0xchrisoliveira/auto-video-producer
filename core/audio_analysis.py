"""
core.audio_analysis
=====================
ETAPA 2 - Análise da narração.
Extrai duração, sample rate, bitrate, canais e loudness (via filtro
loudnorm em modo de medição, padrão EBU R128) usando ffprobe/ffmpeg.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from loguru import logger


@dataclass
class AudioInfo:
    path: Path
    duration: float
    sample_rate: int
    bitrate: int
    channels: int
    integrated_loudness: float | None  # LUFS
    true_peak: float | None
    loudness_range: float | None


def _run_ffprobe(path: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _measure_loudness(path: Path) -> dict:
    """Usa o filtro loudnorm do ffmpeg em modo de análise (print_format json)
    para medir loudness integrada, true peak, loudness range, limiar e
    offset, seguindo o padrão EBU R128. Esses valores alimentam a
    normalização linear aplicada depois na mixagem (ETAPA 9)."""
    cmd = [
        "ffmpeg", "-nostdin", "-i", str(path), "-af",
        "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        logger.warning("Análise de loudness de {} excedeu 600s; seguindo sem normalizar.", path)
        return {}
    stderr = result.stderr

    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr, re.DOTALL)
    if not match:
        logger.warning("Não foi possível medir loudness de {}", path)
        return {}
    try:
        data = json.loads(match.group(0))

        def _num(key: str) -> float | None:
            raw = str(data.get(key, "nan"))
            try:
                value = float(raw)
            except ValueError:
                return None
            return value if math.isfinite(value) else None

        return {
            "integrated_loudness": _num("input_i"),
            "true_peak": _num("input_tp"),
            "loudness_range": _num("input_lra"),
            "threshold": _num("input_thresh"),
            "target_offset": _num("target_offset"),
        }
    except (json.JSONDecodeError, ValueError):
        return {}


def analyze_narration(path: Path) -> AudioInfo:
    logger.info("Analisando narração: {}", path)
    try:
        probe = _run_ffprobe(path)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError,
            json.JSONDecodeError) as e:
        raise RuntimeError(
            f"Não foi possível ler os metadados da narração {path.name}: {e}"
        ) from e
    fmt = probe.get("format", {})
    duration = float(fmt.get("duration", 0.0) or 0.0)
    if duration <= 0:
        raise RuntimeError(
            f"Duração da narração {path.name} é 0s: arquivo vazio ou corrompido."
        )
    bitrate = int(fmt.get("bit_rate", 0) or 0)

    sample_rate = 0
    channels = 0
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == "audio":
            sample_rate = int(stream.get("sample_rate", 0) or 0)
            channels = int(stream.get("channels", 0) or 0)
            break

    loudness = _measure_loudness(path)

    info = AudioInfo(
        path=path,
        duration=duration,
        sample_rate=sample_rate,
        bitrate=bitrate,
        channels=channels,
        integrated_loudness=loudness.get("integrated_loudness"),
        true_peak=loudness.get("true_peak"),
        loudness_range=loudness.get("loudness_range"),
    )
    logger.info(
        "Áudio: {:.2f}s | {} Hz | {} canais | {:.1f} LUFS",
        info.duration, info.sample_rate, info.channels,
        info.integrated_loudness if info.integrated_loudness is not None else -99.0,
    )
    return info
