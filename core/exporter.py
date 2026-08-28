"""
core.exporter
===============
Etapa "Compor vídeo" + ETAPA 14 - Exportação.

Combina em uma única passagem final de ffmpeg:
  - o clipe de fundo já processado (movimento + correção de cor)
  - as legendas queimadas (ETAPA 10, via filtro `subtitles=`)
  - o overlay do canal (ETAPA 11)
  - a tela final de CTA nos últimos segundos (ETAPA 12)
  - a trilha de áudio final (narração + música, ETAPA 9)

Exporta em MP4 / H264 High Profile / AAC, 1920x1080, conforme
especificado na ETAPA 14 do planejamento.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from loguru import logger

from core.config import Config
from core.overlay import OverlayPlan, build_logo_filter, build_text_filter
from core.ffmpeg_utils import run_ffmpeg


def _escape_subtitles_path(path: Path) -> str:
    """O filtro `subtitles=` do ffmpeg exige escapar caracteres especiais
    (':' e '\\') no caminho do arquivo, especialmente relevante para
    caminhos absolutos e em diferentes sistemas operacionais."""
    p = str(path).replace("\\", "\\\\").replace(":", "\\:")
    return p


def compose_and_export(
    cfg: Config,
    silent_video_path: Path,
    mixed_audio_path: Path,
    ass_subtitle_path: Optional[Path],
    overlay_plan: OverlayPlan,
    final_screen_image_path: Optional[Path],
    narration_duration: float,
    output_path: Path,
) -> None:
    w = cfg.get("video", "standard_width", default=1920)
    h = cfg.get("video", "standard_height", default=1080)
    codec = cfg.get("video", "codec", default="libx264")
    pix_fmt = cfg.get("video", "pixel_format", default="yuv420p")
    profile = cfg.get("video", "profile", default="high")
    bitrate = cfg.get("video", "export_bitrate", default="18M")
    audio_codec = cfg.get("video", "audio_codec", default="aac")
    audio_bitrate = cfg.get("video", "audio_bitrate", default="192k")
    fps = cfg.get("video", "standard_fps", default=30)

    screen_duration = cfg.get("final_screen", "duration_seconds", default=8)
    fade_seconds = cfg.get("final_screen", "fade_seconds", default=1)

    inputs: list[str] = ["-i", str(silent_video_path), "-i", str(mixed_audio_path)]
    next_input_index = 2
    filter_parts: list[str] = []
    current_label = "0:v"

    # -------- Legendas (ETAPA 10) --------
    if ass_subtitle_path is not None and ass_subtitle_path.exists():
        escaped = _escape_subtitles_path(ass_subtitle_path)
        filter_parts.append(f"[{current_label}]subtitles='{escaped}'[v_sub]")
        current_label = "v_sub"

    # -------- Overlay do canal (ETAPA 11) --------
    if overlay_plan.logo_path is not None:
        inputs += ["-i", str(overlay_plan.logo_path)]
        logo_label = f"{next_input_index}:v"
        next_input_index += 1
        filter_parts.append(
            build_logo_filter(overlay_plan, logo_label, current_label, "v_overlay")
        )
        current_label = "v_overlay"
    else:
        font_file = None
        fontes_dir = cfg.paths["assets_fontes"]
        if fontes_dir.exists():
            ttfs = list(fontes_dir.glob("*.ttf"))
            font_file = ttfs[0] if ttfs else None
        filter_parts.append(
            build_text_filter(overlay_plan, current_label, "v_overlay", font_file)
        )
        current_label = "v_overlay"

    # -------- Tela final de CTA (ETAPA 12) --------
    if final_screen_image_path is not None and final_screen_image_path.exists():
        from core.final_screen import build_final_screen_filter

        inputs += ["-loop", "1", "-i", str(final_screen_image_path)]
        cta_label = f"{next_input_index}:v"
        next_input_index += 1
        filter_parts.append(
            build_final_screen_filter(
                current_label, cta_label, "v_final",
                total_duration=narration_duration,
                screen_duration=screen_duration,
                fade_seconds=fade_seconds,
            )
        )
        current_label = "v_final"

    filter_complex = ";".join(filter_parts)

    args = [*inputs]
    if filter_complex:
        args += ["-filter_complex", filter_complex, "-map", f"[{current_label}]"]
    else:
        args += ["-map", "0:v"]
    args += ["-map", "1:a"]

    args += [
        "-c:v", codec,
        "-profile:v", profile,
        "-pix_fmt", pix_fmt,
        "-r", str(fps),
        "-b:v", bitrate,
        "-c:a", audio_codec,
        "-b:a", audio_bitrate,
        "-shortest",
        "-movflags", "+faststart",
        str(output_path),
    ]

    logger.info("Iniciando exportação final -> {}", output_path)
    run_ffmpeg(args, description="compose_and_export")
    logger.info("Exportação concluída: {}", output_path)
