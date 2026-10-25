"""
core.overlay
=============
ETAPA 11 - Overlay discreto da marca (logo em assets/logo/ ou texto
configurado em overlay.text), com opacidade reduzida (20-30%) e posição
configurável (overlay.position). Whitelabel: o que aparece vem 100% do
config.yaml.

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

# Posições suportadas -> expressões de canto (overlay usa W/H maiúsculos,
# drawtext usa w/h minúsculos para a dimensão do próprio texto).
_POSITIONS = ("top-left", "top-center", "top-right",
              "center-left", "center", "center-right",
              "bottom-left", "bottom-center", "bottom-right")


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
    position: str = "bottom-right"


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
    opacity = cfg.get_float("overlay", "opacity", default=0.25)
    margin = int(cfg.get_float("overlay", "margin", default=40.0))
    font_size = int(cfg.get_float("overlay", "font_size", default=28.0))

    # aceita "bottom_right" (configs antigas) e "bottom-right"
    position = str(cfg.get("overlay", "position", default="bottom-right")).strip().lower().replace("_", "-")
    if position not in _POSITIONS:
        logger.warning(
            "overlay.position='{}' desconhecida (válidas: {}); usando 'bottom-right'.",
            position, ", ".join(_POSITIONS),
        )
        position = "bottom-right"
    opacity = max(0.0, min(1.0, opacity))

    # sem logo E sem texto -> não há o que desenhar
    logo_path = find_logo(cfg)
    if logo_path:
        logger.info("Overlay: usando logo do canal ({})", logo_path.name)
    elif text.strip():
        logger.info("Overlay: usando texto '{}'.", text)
    else:
        logger.info("Overlay desabilitado (sem logo e sem texto).")

    return OverlayPlan(logo_path=logo_path, text=text, opacity=float(opacity),
                        margin=int(margin), font_size=int(font_size),
                        position=position)


def _xy_expressions(position: str, margin: int, *, upper: bool,
                    own_w: str, own_h: str) -> tuple[str, str]:
    """Devolve (x, y) para a posição pedida.
    `upper` = True para o filtro overlay (dimensões do vídeo em W/H),
    False para drawtext (dimensões do vídeo em w/h)."""
    W, H = ("W", "H") if upper else ("w", "h")
    m = int(margin)
    vertical, _, horizontal = position.partition("-")
    if position == "center":
        horizontal, vertical = "center", "center"
    elif not horizontal:          # "top" / "bottom" sozinho
        horizontal, vertical = "center", vertical

    if horizontal == "left":
        x = str(m)
    elif horizontal == "right":
        x = f"{W}-{own_w}-{m}"
    else:
        x = f"({W}-{own_w})/2"

    if vertical == "top":
        y = str(m)
    elif vertical == "bottom":
        y = f"{H}-{own_h}-{m}"
    else:
        y = f"({H}-{own_h})/2"
    return x, y


def build_logo_filter(plan: OverlayPlan, logo_input_label: str, video_label: str,
                      out_label: str) -> str:
    """Filtro para compor um logo PNG semitransparente sobre o vídeo,
    na posição definida por overlay.position."""
    x, y = _xy_expressions(plan.position, plan.margin, upper=True,
                           own_w="w", own_h="h")
    return (
        f"[{logo_input_label}]format=rgba,"
        f"colorchannelmixer=aa={plan.opacity}[logo_rgba];"
        f"[{video_label}][logo_rgba]overlay={x}:{y}:format=auto[{out_label}]"
    )


def build_text_filter(plan: OverlayPlan, video_label: str, out_label: str,
                      font_file: Optional[Path],
                      text_file: Optional[Path] = None) -> str:
    """Filtro drawtext para o texto do canal semitransparente.

    Quando `text_file` é informado, o texto vem de um arquivo
    (`textfile=` + `expansion=none`): vírgulas, aspas, `%`, `:` e
    emojis no nome do canal não quebram mais a sintaxe do filtro."""
    if text_file is not None:
        text_clause = f"textfile='{text_file}':expansion=none"
    else:
        safe_text = plan.text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        text_clause = f"text='{safe_text}'"

    x, y = _xy_expressions(plan.position, plan.margin, upper=False,
                           own_w="tw", own_h="th")
    font_clause = f":fontfile='{font_file}'" if font_file else ""
    return (
        f"[{video_label}]drawtext={text_clause}{font_clause}:"
        f"fontsize={plan.font_size}:fontcolor=white@{plan.opacity}:"
        f"x={x}:y={y}[{out_label}]"
    )
