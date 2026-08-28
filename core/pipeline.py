"""
core.pipeline
==============
Orquestra as ETAPAS do pipeline para geração de vídeos.

- run_pipeline_for_audio: processa áudio existente (.wav) -> vídeo
- run_pipeline_for_text: processa texto (.txt) -> TTS -> vídeo
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from loguru import logger

from core.audio_analysis import analyze_narration
from core.audio_mixer import mix_narration_with_music
from core.classification import classify_theme_hybrid, find_text_for_audio
from core.config import Config
from core.database import Database
from core.exporter import compose_and_export
from core.final_screen import build_final_screen_image
from core.logger_setup import ProjectReport
from core.organizer import move_processed_audio, register_project_in_db
from core.overlay import build_overlay_plan
from core.selectors import select_ambient_music, select_background_videos
from core.thumbnail import generate_thumbnail
from core.tts_engine import synthesize_text_to_audio
from core.video_processor import plan_segment_durations, prepare_background_sequence


def _next_project_id(cfg: Config) -> str:
    today = datetime.now().strftime("%Y_%m_%d")
    logs_dir = cfg.paths["logs"]
    existing = list(logs_dir.glob(f"{today}_*.log.json"))
    seq = len(existing) + 1
    return f"{today}_{seq:03d}"


def run_pipeline_for_audio(cfg: Config, db: Database, audio_path: Path) -> Path | None:
    """Executa o pipeline completo para uma narração e retorna o caminho
    do vídeo final exportado (ou None em caso de falha)."""
    project_id = _next_project_id(cfg)
    report = ProjectReport(cfg, project_id)
    report.set(audio=str(audio_path))
    logger.info("=== Iniciando projeto {} para {} ===", project_id, audio_path.name)

    cache_dir = cfg.paths["cache"] / project_id
    cache_dir.mkdir(parents=True, exist_ok=True)

    try:
        # ETAPA 2 - Análise da narração
        audio_info = analyze_narration(audio_path)
        narration_duration = audio_info.duration
        if narration_duration <= 0:
            raise RuntimeError("Duração da narração inválida (0s). Arquivo corrompido?")

        w = cfg.get("video", "standard_width", default=1080)
        h = cfg.get("video", "standard_height", default=1920)
        is_vertical = h >= w

        # ETAPA 3 - Classificação automática
        text = find_text_for_audio(cfg, audio_path)
        available_categories = db.get_active_categories()
        classification = classify_theme_hybrid(cfg, text, available_categories)
        report.set(classificacao=f"método={classification.method}, tema={classification.theme}, "
                                  f"categorias={classification.categories}")

        # ETAPA 4 - Seleção do(s) vídeo(s) de fundo
        orientation = "vertical" if h > w else ("horizontal" if w > h else None)
        segment_durations = plan_segment_durations(cfg, narration_duration)
        video_assets = select_background_videos(
            db, classification.categories, len(segment_durations), orientation=orientation,
        )
        video_paths = [cfg.project_root / v.arquivo for v in video_assets]
        video_list_str = ", ".join(v.arquivo for v in video_assets)
        report.set(video=video_list_str)

        # ETAPAS 5-8 - Padronização, trecho(s), movimento e cor
        silent_clip_path = cache_dir / "background_processed.mp4"
        prepare_background_sequence(cfg, video_paths, segment_durations, cache_dir, silent_clip_path)

        # ETAPA 9 - Música ambiente + mixagem
        music_asset = select_ambient_music(db, min_duration=0.0)
        music_source_path = (
            cfg.project_root / music_asset.arquivo if music_asset else None
        )
        report.set(musica=music_asset.arquivo if music_asset else "Nenhuma")

        mixed_audio_path = cache_dir / "audio_mixed.wav"
        mix_narration_with_music(
            cfg, audio_path, music_source_path, narration_duration, mixed_audio_path
        )

        # ETAPA 11 - Overlay do canal
        overlay_plan = build_overlay_plan(cfg)
        report.set(overlay="OK")

        # ETAPA 12 - Tela final de CTA
        cta_image_path = cache_dir / "tela_final.png"
        build_final_screen_image(cfg, w, h, cta_image_path)

        # Define pasta de saída: vídeos verticais (Shorts) vão para
        # output/shorts/, vídeos horizontais para output/videos/.
        output_dir = cfg.paths["output_shorts"] if is_vertical else cfg.paths["output_videos"]
        output_dir.mkdir(parents=True, exist_ok=True)

        # ETAPA 13 - Thumbnail
        thumb_output_path = output_dir / f"{project_id}_thumb.jpg"
        title_text = (text.strip().splitlines()[0][:60] if text else "Auto Video Producer")
        try:
            generate_thumbnail(
                cfg, audio_path.stem, silent_clip_path, title_text,
                cache_dir, thumb_output_path,
            )
            report.set(thumbnail="Gerada")
        except Exception as e:  # noqa: BLE001
            logger.warning("Geração de thumbnail falhou: {}", e)
            report.set(thumbnail=f"FALHOU ({e})")

        # ETAPA 14 - Composição final (overlay + CTA) e exportação
        output_path = output_dir / f"{project_id}.mp4"
        compose_and_export(
            cfg, silent_clip_path, mixed_audio_path, None, overlay_plan,
            cta_image_path, narration_duration, output_path,
        )

        # ETAPA 15 - Organização (mover áudio, registrar no banco)
        processed_audio_path = move_processed_audio(cfg, audio_path)
        register_project_in_db(
            db,
            titulo=project_id,
            audio=str(processed_audio_path),
            video=video_list_str,
            musica=music_asset.arquivo if music_asset else "",
            thumbnail=str(thumb_output_path),
            duracao=narration_duration,
            status="concluido",
        )

        report.set(video=video_list_str)
        report.finalize(success=True)
        logger.success("=== Projeto {} concluído: {} ===", project_id, output_path)
        return output_path

    except Exception as e:  # noqa: BLE001
        logger.exception("Falha no pipeline do projeto {}: {}", project_id, e)
        report.set(erro=str(e))
        report.finalize(success=False)
        return None


def run_pipeline_for_text(cfg: Config, db: Database, text_path: Path) -> Path | None:
    """Executa o pipeline completo para um texto, gerando áudio via TTS."""
    project_id = _next_project_id(cfg)
    report = ProjectReport(cfg, project_id)
    report.set(texto=str(text_path))
    logger.info("=== Iniciando projeto {} para {} ===", project_id, text_path.name)

    cache_dir = cfg.paths["cache"] / project_id
    cache_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Lê o texto
        text = text_path.read_text(encoding="utf-8").strip()
        if not text:
            raise RuntimeError("Arquivo de texto vazio")

        # ETAPA 1 - Geração de áudio via TTS
        audio_path = cache_dir / f"{project_id}.wav"
        synthesize_text_to_audio(cfg, text, audio_path)
        report.set(audio=str(audio_path))

        # Continua com o pipeline normal (ETAPA 2-14)
        return run_pipeline_for_audio(cfg, db, audio_path)

    except Exception as e:  # noqa: BLE001
        logger.exception("Falha no pipeline do projeto {}: {}", project_id, e)
        report.set(erro=str(e))
        report.finalize(success=False)
        return None