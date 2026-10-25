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


# ffprobe só lê metadados: qualquer coisa acima disso indica arquivo
# problemático e não deve prender o pipeline (nem a GUI) para sempre.
_PROBE_TIMEOUT_SECONDS = 60


def run_ffmpeg(args: list[str], description: str = "") -> None:
    """Executa um comando ffmpeg (a lista NÃO deve incluir o executável
    'ffmpeg', apenas os argumentos). Sempre inclui -y (overwrite),
    -nostdin (nunca fica esperando input do terminal) e
    -hide_banner -loglevel error para saída limpa."""
    cmd = ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error", *args]
    logger.debug("Executando: {}", " ".join(shlex.quote(c) for c in cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("Falha no ffmpeg ({}): {}", description, result.stderr[-4000:])
        raise FFmpegError(f"ffmpeg falhou em '{description}': {result.stderr[-2000:]}")


def ffprobe_duration(path: Path) -> float:
    """Duração em segundos (0.0 se não for possível medir). O probe tem
    timeout curto: um arquivo de mídia corrompido/inalcançável não pode
    travar o pipeline para sempre."""
    cmd = [
        "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                check=True, timeout=_PROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        raise FFmpegError(
            f"ffprobe demorou mais que {_PROBE_TIMEOUT_SECONDS}s em {path.name} "
            "(arquivo corrompido ou inacessível?)."
        )
    except subprocess.CalledProcessError as e:
        raise FFmpegError(f"ffprobe falhou em {path.name}: {e.stderr[-500:] if e.stderr else ''}")
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0
