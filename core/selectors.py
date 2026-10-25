"""
core.selectors
================
ETAPA 4 - Seleção do vídeo de fundo.
ETAPA 9 - Seleção de música ambiente.

Ambas as seleções seguem a mesma lógica: consultar os N assets menos
utilizados (pool_size no config) dentro dos critérios desejados,
escolher aleatoriamente e atualizar o contador de uso imediatamente
para evitar repetição.

Diferenças em relação à versão original:
- aceitam a duração necessária de cada trecho, para preferir assets
  longos o bastante (menos loop de fundo);
- validam se o arquivo ainda existe no disco antes de escolher, marcando
  o registro como inativo quando não existe (um arquivo apagado não pode
  derrubar um render inteiro).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from loguru import logger

from core.config import Config
from core.database import Database, MusicAsset, VideoAsset


def _asset_exists(cfg: Config, relative_path: str) -> bool:
    return (cfg.project_root / relative_path).exists()


def select_background_videos(
    cfg: Config,
    db: Database,
    categories: list[str],
    count: int,
    orientation: Optional[str] = None,
    durations: Optional[list[float]] = None,
) -> list[VideoAsset]:
    """Seleciona `count` vídeos de fundo, evitando repetir o mesmo
    arquivo entre os trechos sempre que houver assets suficientes na
    categoria/pool. Usado para compor o vídeo final com vários trechos
    diferentes em vez de um único clipe em loop.

    `orientation` ("vertical"/"horizontal"/None) filtra para só usar
    assets na mesma orientação do vídeo final, e `durations` informa a
    duração necessária de cada trecho (assets mais curtos só entram por
    último recurso, pois exigem loop)."""
    count = max(count, 1)
    chosen: list[VideoAsset] = []
    used_ids: set[int] = set()
    skipped_missing = 0

    while len(chosen) < count:
        needed = float(durations[len(chosen)]) if durations and len(durations) > len(chosen) else 0.0
        video = db.pick_least_used_video(
            categories, exclude_ids=used_ids, orientation=orientation, min_duration=needed
        )
        if video is None:
            break

        if not _asset_exists(cfg, video.arquivo):
            logger.warning(
                "Arquivo sumiu da biblioteca; marcando como inativo e escolhendo outro: {}",
                video.arquivo,
            )
            db.mark_video_inactive(video.id)
            skipped_missing += 1
            continue

        chosen.append(video)
        used_ids.add(video.id)
        db.increment_video_usage(video.id)

    if skipped_missing:
        logger.warning("{} registro(s) de vídeo apagado(s) do disco foram desativados.", skipped_missing)

    if not chosen:
        raise RuntimeError(
            "Nenhum vídeo disponível no banco. Rode o scan_assets() e "
            "verifique se há arquivos em assets/videos/<categoria>/."
        )

    if len(chosen) < count:
        logger.warning(
            "Só havia {} vídeo(s) distinto(s) disponível(is) para {} trecho(s) "
            "pedidos; alguns vídeos serão reaproveitados para completar a duração.",
            len(chosen), count,
        )
        i = 0
        while len(chosen) < count:
            reused = chosen[i % len(chosen)]
            chosen.append(reused)
            db.increment_video_usage(reused.id)
            i += 1

    logger.info(
        "Selecionados {} trecho(s) de vídeo de fundo: {}",
        len(chosen), ", ".join(v.arquivo for v in chosen),
    )
    return chosen


def select_background_video(cfg: Config, db: Database, categories: list[str],
                            orientation: Optional[str] = None) -> VideoAsset:
    """Seleção de um único vídeo (mantida para uso avulso/CLI)."""
    return select_background_videos(cfg, db, categories, 1, orientation=orientation)[0]


def select_ambient_music(cfg: Config, db: Database, min_duration: float = 0.0) -> Optional[MusicAsset]:
    for _attempt in range(5):  # no máximo 5 tentativas contra arquivo apagado
        music = db.pick_least_used_music(min_duration=min_duration)
        if music is None:
            logger.warning("Nenhuma música ambiente disponível; vídeo será gerado sem trilha.")
            return None
        if _asset_exists(cfg, music.arquivo):
            db.increment_music_usage(music.id)
            logger.info("Música ambiente selecionada: {} (usos anteriores={})",
                        music.arquivo, music.usos)
            return music
        logger.warning("Música sumiu do disco; marcando como inativa: {}", music.arquivo)
        db.mark_music_inactive(music.id)

    logger.warning("Não foi possível encontrar música válida; seguindo sem trilha.")
    return None
