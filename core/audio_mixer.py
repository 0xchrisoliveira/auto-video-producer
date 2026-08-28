"""
core.audio_mixer
==================
ETAPA 9 (parte 2) - Mixagem de música ambiente com a narração.

- Faz loop automático da música caso ela seja mais curta que a narração.
- Aplica os volumes configurados (narrador 0 dB, música -28 dB por padrão).
- Aplica fade in/out na música.
- Caso não haja música disponível, apenas normaliza a narração sozinha.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from loguru import logger

from core.config import Config
from core.ffmpeg_utils import run_ffmpeg


def mix_narration_with_music(
    cfg: Config,
    narration_path: Path,
    music_path: Optional[Path],
    narration_duration: float,
    output_path: Path,
) -> None:
    narrator_db = cfg.get("audio_mix", "narrator_db", default=0)
    music_db = cfg.get("audio_mix", "music_db", default=-28)
    fade_in = cfg.get("audio_mix", "fade_in_seconds", default=2)
    fade_out = cfg.get("audio_mix", "fade_out_seconds", default=3)

    if music_path is None:
        logger.info("Sem música ambiente disponível; exportando apenas a narração.")
        run_ffmpeg(
            [
                "-i", str(narration_path),
                "-af", f"volume={narrator_db}dB",
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
        f"[0:a]volume={narrator_db}dB[narr];"
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
