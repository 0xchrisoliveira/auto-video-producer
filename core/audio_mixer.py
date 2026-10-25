"""
core.audio_mixer
==================
ETAPA 9 (parte 2) - Mixagem de música ambiente com a narração.

- Faz loop automático da música caso ela seja mais curta que a narração.
- Aplica os volumes configurados (narrador 0 dB, música -28 dB por padrão).
- Aplica fade in/out na música.
- Normaliza a loudness da narração para o alvo configurado (LUFS),
  usando a medição da ETAPA 2 (loudnorm de medição) para aplicar um
  ganho linear — sem o "pumping" do loudnorm em dois passos.
- Caso não haja música disponível, apenas normaliza a narração sozinha.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

from loguru import logger

from core.audio_analysis import AudioInfo
from core.config import Config
from core.ffmpeg_utils import run_ffmpeg

# Ganho máximo aplicado automaticamente: evita amplificar silêncio/ruído
# de um TTS mal calibrado até o ponto de distorcer.
_MAX_AUTO_GAIN_DB = 24.0


def _loudness_gain_db(cfg: Config, narration_info: Optional[AudioInfo]) -> Optional[float]:
    """Calcula o ganho linear (dB) para levar a narração ao LUFS alvo,
    respeitando também o teto de true peak. Retorna None quando não há
    medição confiável ou quando a normalização está desligada."""
    if not cfg.get("audio_mix", "normalize_loudness", default=True):
        return None
    if narration_info is None:
        return None

    measured = narration_info.integrated_loudness
    if measured is None or not math.isfinite(measured):
        logger.info("Loudness da narração não medido; seguindo sem normalização.")
        return None

    target = cfg.get_float("audio_mix", "target_lufs", default=-16.0)
    gain = target - measured

    true_peak = narration_info.true_peak
    if true_peak is not None and math.isfinite(true_peak):
        max_tp = cfg.get_float("audio_mix", "target_tp", default=-1.5)
        gain = min(gain, max_tp - true_peak)

    gain = max(-_MAX_AUTO_GAIN_DB, min(_MAX_AUTO_GAIN_DB, gain))
    if abs(gain) < 0.1:
        logger.debug("Narração já está no alvo ({:.1f} LUFS); sem ajuste.", measured)
        return None

    logger.info(
        "Normalizando narração: {:.1f} LUFS -> alvo {:.1f} LUFS (ganho {:+.1f} dB)",
        measured, target, gain,
    )
    return gain


def mix_narration_with_music(
    cfg: Config,
    narration_path: Path,
    music_path: Optional[Path],
    narration_duration: float,
    output_path: Path,
    narration_info: Optional[AudioInfo] = None,
) -> None:
    narrator_db = cfg.get_float("audio_mix", "narrator_db", default=0.0)
    music_db = cfg.get_float("audio_mix", "music_db", default=-28.0)
    fade_in = cfg.get_float("audio_mix", "fade_in_seconds", default=2.0)
    fade_out = cfg.get_float("audio_mix", "fade_out_seconds", default=3.0)

    gain = _loudness_gain_db(cfg, narration_info)
    narr_chain = f"volume={narrator_db}dB"
    if gain is not None:
        narr_chain += f",volume={gain:.2f}dB"

    if music_path is None:
        logger.info("Sem música ambiente disponível; exportando apenas a narração.")
        run_ffmpeg(
            [
                "-i", str(narration_path),
                "-af", narr_chain,
                "-t", f"{narration_duration:.3f}",
                str(output_path),
            ],
            description="mix_narration_only",
        )
        return

    fade_out_start = max(narration_duration - fade_out, 0.0)

    # -stream_loop -1 garante que a música cubra qualquer duração da
    # narração; o -t final corta no tamanho exato.
    filter_complex = (
        f"[0:a]{narr_chain}[narr];"
        f"[1:a]volume={music_db}dB,"
        f"afade=t=in:st=0:d={fade_in},"
        f"afade=t=out:st={fade_out_start:.3f}:d={fade_out}[music];"
        f"[narr][music]amix=inputs=2:duration=first:dropout_transition=0[out]"
    )

    run_ffmpeg(
        [
            "-i", str(narration_path),
            "-stream_loop", "-1", "-i", str(music_path),
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-t", f"{narration_duration:.3f}",
            str(output_path),
        ],
        description="mix_narration_with_music",
    )
    logger.info("Áudio final (narração + música) gerado em {}", output_path)
