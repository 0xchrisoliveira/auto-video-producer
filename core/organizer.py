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


def move_processed_audio(cfg: Config, audio_path: Path) -> Path:
    dest_dir = cfg.paths["processed_audio"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / audio_path.name
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
