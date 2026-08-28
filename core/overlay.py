"""
core.overlay
=============
ETAPA 11 - Overlay discreto da marca (logo em assets/logo/ ou texto
configurado em overlay.text), no canto inferior direito, com opacidade
reduzida (20-30%). Whitelabel: o que aparece vem 100% do config.yaml.

Este módulo apenas monta os fragmentos de filtro/entrada que serão
combinados pelo exporter (core.exporter) na passagem final de
composição, evitando reencodes intermediários desnecessários.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

from core.config import Config


@dataclass
class OverlayPlan:
    """Descreve como aplicar o overlay do canal.
    Se `logo_path` estiver definido, usa-se a imagem (overlay filter);
    caso contrário usa-se drawtext com o texto do canal."""
    logo_path: Optional[Path]
    text: str
    opacity: float
    margin: int
    font_size: int


def find_logo(cfg: Config) -> Optional[Path]:
    logo_dir = cfg.paths["assets_logo"]
    if not logo_dir.exists():
        return None
    for ext in (".png", ".webp"):
        for candidate in logo_dir.glob(f"*{ext}"):
            return candidate
    return None


def build_overlay_plan(cfg: Config) -> OverlayPlan:
    brand_name = str(cfg.get("brand", "name", default="Auto Video Producer"))
    # vazio/ausente -> usa o nome da marca (seção `brand`)
    text = str(cfg.get("overlay", "text", default="") or brand_name)
    opacity = cfg.get("overlay", "opacity", default=0.25)
    margin = cfg.get("overlay", "margin", default=40)
    font_size = cfg.get("overlay", "font_size", default=28)

    logo_path = find_logo(cfg)
    if logo_path:
        logger.info("Overlay: usando logo do canal ({})", logo_path.name)
    else:
        logger.info("Overlay: nenhum logo encontrado, usando texto '{}'.", text)

    return OverlayPlan(logo_path=logo_path, text=text, opacity=opacity,
                        margin=margin, font_size=font_size)


def build_logo_filter(plan: OverlayPlan, logo_input_label: str, video_label: str,
                       out_label: str) -> str:
    """Filtro para compor um logo PNG semitransparente sobre o vídeo,
    posicionado no canto inferior direito."""
    return (
        f"[{logo_input_label}]format=rgba,"
        f"colorchannelmixer=aa={plan.opacity}[logo_rgba];"
        f"[{video_label}][logo_rgba]overlay="
        f"W-w-{plan.margin}:H-h-{plan.margin}:format=auto[{out_label}]"
    )


def build_text_filter(plan: OverlayPlan, video_label: str, out_label: str,
                       font_file: Optional[Path]) -> str:
    """Filtro drawtext para o texto do canal semitransparente."""
    safe_text = plan.text.replace(":", "\\:").replace("'", "\\'")
    font_clause = f":fontfile='{font_file}'" if font_file else ""
    return (
        f"[{video_label}]drawtext=text='{safe_text}'{font_clause}:"
        f"fontsize={plan.font_size}:fontcolor=white@{plan.opacity}:"
        f"x=w-tw-{plan.margin}:y=h-th-{plan.margin}[{out_label}]"
    )
