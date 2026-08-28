"""
core.ffmpeg_utils
===================
Funções utilitárias para chamar ffmpeg/ffprobe via subprocess com
logging e tratamento de erro consistentes.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from loguru import logger


class FFmpegError(RuntimeError):
    pass


def run_ffmpeg(args: list[str], description: str = "") -> None:
    """Executa um comando ffmpeg (a lista NÃO deve incluir o executável
    'ffmpeg', apenas os argumentos). Sempre inclui -y (overwrite) e
    -hide_banner -loglevel error para saída limpa."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args]
    logger.debug("Executando: {}", " ".join(shlex.quote(c) for c in cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("Falha no ffmpeg ({}): {}", description, result.stderr[-4000:])
        raise FFmpegError(f"ffmpeg falhou em '{description}': {result.stderr[-2000:]}")


def ffprobe_duration(path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0
