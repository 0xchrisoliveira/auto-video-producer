"""
core.selectors
================
ETAPA 4 - Seleção do vídeo de fundo.
ETAPA 9 - Seleção de música ambiente.

Ambas as seleções seguem a mesma lógica: consultar os N assets menos
utilizados (pool_size no config) dentro dos critérios desejados,
escolher um aleatoriamente e atualizar o contador de uso imediatamente
para evitar repetição.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from core.database import Database, MusicAsset, VideoAsset


def select_background_video(db: Database, categories: list[str],
                             orientation: Optional[str] = None) -> VideoAsset:
    video = db.pick_least_used_video(categories, orientation=orientation)
    if video is None:
        raise RuntimeError(
            "Nenhum vídeo disponível no banco. Rode o scan_assets() e "
            "verifique se há arquivos em assets/videos/<categoria>/."
        )
    db.increment_video_usage(video.id)
    logger.info("Vídeo de fundo selecionado: {} (categoria={}, usos anteriores={})",
                video.arquivo, video.categoria, video.usos)
    return video


def select_background_videos(db: Database, categories: list[str], count: int,
                              orientation: Optional[str] = None) -> list[VideoAsset]:
    """Seleciona `count` vídeos de fundo, evitando repetir o mesmo
    arquivo entre os trechos sempre que houver assets suficientes na
    categoria/pool. Usado para compor o vídeo final com vários trechos
    diferentes em vez de um único clipe em loop.

    `orientation` ("vertical"/"horizontal"/None) filtra para só usar
    assets na mesma orientação do vídeo final sendo gerado, permitindo
    guardar vídeos horizontais e verticais juntos na mesma pasta de
    categoria."""
    count = max(count, 1)
    chosen: list[VideoAsset] = []
    used_ids: set[int] = set()

    while len(chosen) < count:
        video = db.pick_least_used_video(categories, exclude_ids=used_ids, orientation=orientation)
        if video is None:
            break
        chosen.append(video)
        used_ids.add(video.id)
        db.increment_video_usage(video.id)

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


def select_ambient_music(db: Database, min_duration: float) -> Optional[MusicAsset]:
    music = db.pick_least_used_music(min_duration=min_duration)
    if music is None:
        logger.warning("Nenhuma música ambiente disponível; vídeo será gerado sem trilha.")
        return None
    db.increment_music_usage(music.id)
    logger.info("Música ambiente selecionada: {} (usos anteriores={})",
                music.arquivo, music.usos)
    return music
