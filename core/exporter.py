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

Exporta em MP4 / H264 High Profile / AAC, na resolução configurada em
`video.*` (padrão 1080x1920), conforme especificado na ETAPA 14.
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
    w = cfg.get("video", "standard_width", default=1080)
    h = cfg.get("video", "standard_height", default=1920)
    codec = cfg.get("video", "codec", default="libx264")
    pix_fmt = cfg.get("video", "pixel_format", default="yuv420p")
    profile = cfg.get("video", "profile", default="high")
    bitrate = cfg.get("video", "export_bitrate", default="18M")
    bitrate_min = cfg.get("video", "export_bitrate_min", default="")
    bitrate_max = cfg.get("video", "export_bitrate_max", default="")
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
    # Sem logo e sem texto -> não desenha nada (evita drawtext vazio).
    overlay_text_file: Optional[Path] = None
    if overlay_plan.logo_path is not None:
        inputs += ["-i", str(overlay_plan.logo_path)]
        logo_label = f"{next_input_index}:v"
        next_input_index += 1
        filter_parts.append(
            build_logo_filter(overlay_plan, logo_label, current_label, "v_overlay")
        )
        current_label = "v_overlay"
    elif overlay_plan.text.strip():
        font_file = None
        fontes_dir = cfg.paths["assets_fontes"]
        if fontes_dir.exists():
            ttfs = sorted(fontes_dir.glob("*.ttf"))
            font_file = ttfs[0] if ttfs else None
        # texto vai em arquivo: vírgulas, aspas, %, ':' e emojis não
        # quebram a sintaxe do filtro drawtext
        overlay_text_file = output_path.with_name(output_path.stem + "_overlay.txt")
        overlay_text_file.write_text(overlay_plan.text, encoding="utf-8")
        filter_parts.append(
            build_text_filter(overlay_plan, current_label, "v_overlay",
                              font_file, text_file=overlay_text_file)
        )
        current_label = "v_overlay"
    else:
        logger.info("Overlay desabilitado; seguindo sem marca no vídeo.")

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
        # Duração exata da narração: a imagem da tela final entra com
        # `-loop 1` (stream infinito) e, sem um -t explícito, o ffmpeg
        # encerrava o vídeo mais longo que o áudio.
        "-t", f"{narration_duration:.3f}",
        "-movflags", "+faststart",
        str(output_path),
    ]
    # limites de bitrate (config opcionais). Observação: em conteúdo muito
    # comprimível o encoder pode ficar abaixo de -minrate — é um limite
    # suave, não uma garantia de tamanho mínimo.
    if str(bitrate_max).strip():
        args[-1:-1] = ["-maxrate", str(bitrate_max).strip()]
    if str(bitrate_min).strip():
        args[-1:-1] = ["-minrate", str(bitrate_min).strip()]
    bufsize = str(cfg.get("video", "export_bitrate_bufsize", default="") or "").strip()
    if bufsize:
        args[-1:-1] = ["-bufsize", bufsize]
    elif str(bitrate_max).strip():
        # sem -bufsize, -maxrate tem efeito limitado: usa o nominal
        # como janela de viés (VBV)
        args[-1:-1] = ["-bufsize", str(bitrate)]

    logger.info("Iniciando exportação final -> {}", output_path)
    try:
        run_ffmpeg(args, description="compose_and_export")
    finally:
        if overlay_text_file is not None:
            overlay_text_file.unlink(missing_ok=True)
    logger.info("Exportação concluída: {}", output_path)
