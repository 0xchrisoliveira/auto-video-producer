"""
core.organizer
================
ETAPA 15 - Organização pós-produção.

Move o áudio processado para processados/audio/, registra o projeto
no banco (vídeo, música, duração, data) e garante que os contadores de
uso já foram incrementados nas etapas de seleção (core.selectors já
faz isso no momento da escolha, conforme o planejamento pede
atualização "imediata").
"""

from __future__ import annotations

import shutil
from pathlib import Path

from loguru import logger

from core.config import Config
from core.database import Database


def _unique_path(path: Path) -> Path:
    """Evita sobrescrever um arquivo já existente (mesmo nome de roteiro
    processado em outro dia), acrescentando 001, 002..."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(1, 1000):
        candidate = path.with_name(f"{stem}{i:03d}{suffix}")
        if not candidate.exists():
            return candidate
    return path


def move_processed_audio(cfg: Config, audio_path: Path) -> Path:
    dest_dir = cfg.paths["processed_audio"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = _unique_path(dest_dir / audio_path.name)
    shutil.move(str(audio_path), str(dest_path))
    logger.info("Áudio movido para {}", dest_path)
    return dest_path


def register_project_in_db(
    db: Database,
    titulo: str,
    audio: str,
    video: str,
    musica: str,
    thumbnail: str,
    duracao: float,
    status: str,
) -> int:
    project_id = db.register_project(
        titulo=titulo, audio=audio, video=video, musica=musica,
        thumbnail=thumbnail, duracao=duracao, status=status,
    )
    logger.info("Projeto registrado no banco (id={}, status={})", project_id, status)
    return project_id
