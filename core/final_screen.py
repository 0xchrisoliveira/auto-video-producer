"""
core.final_screen
===================
ETAPA 12 - Tela final (últimos 8 segundos), com chamadas para ação
(inscrever-se, comentar, sino, compartilhar) e animação simples de
fade in.

Gera uma imagem PNG com fundo transparente contendo o texto de CTA
(usando Pillow) que depois é sobreposta ao vídeo apenas no intervalo
final, com fade, pelo exporter.

Renderização de emoji
----------------------
Fontes de texto comuns (as que ficam em assets/fontes/) não têm
glifos de emoji, então desenhá-los direto com a fonte do texto
resulta em retângulos vazios ("tofu"). Para evitar isso, cada linha é
dividida em trechos de texto normal e trechos de emoji:

- o texto usa a fonte de assets/fontes/ normalmente;
- os emojis são renderizados à parte com uma fonte de emoji colorida
  do sistema operacional (Segoe UI Emoji no Windows, Apple Color
  Emoji no macOS, Noto Color Emoji no Linux) e depois coladas na
  imagem como um recorte redimensionado. Isso também contorna uma
  limitação comum de fontes de emoji em bitmap (ex.: Noto Color
  Emoji), que só aceitam alguns tamanhos fixos em vez de qualquer
  tamanho pedido.

Se nenhuma fonte de emoji for encontrada no sistema, os emojis são
simplesmente removidos da linha (em vez de aparecerem como caixas
vazias).
"""

from __future__ import annotations

import re
from pathlib import Path

from loguru import logger
from PIL import Image, ImageDraw, ImageFont

from core.config import Config

# Faixas Unicode que cobrem a grande maioria dos emojis usados em CTAs
# (rostos, símbolos, mãos, objetos, bandeiras, dingbats, variation
# selector de emoji etc.). Não é um regex "perfeito" para todo o
# Unicode, mas cobre bem o uso comum de emojis em texto.
_EMOJI_RE = re.compile(
    "(["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF"
    "\U00002B00-\U00002BFF"
    "\U0001F000-\U0001F0FF"
    "\U0000FE0F"
    "\U0000200D"
    "]+)"
)

# Caminhos comuns de fontes de emoji coloridas por sistema operacional.
_EMOJI_FONT_CANDIDATES = [
    # Windows
    r"C:\Windows\Fonts\seguiemj.ttf",
    # macOS
    "/System/Library/Fonts/Apple Color Emoji.ttc",
    "/System/Library/Fonts/Apple Color Emoji.ttf",
    # Linux (varia bastante conforme a distro/pacote)
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/noto-color-emoji/NotoColorEmoji.ttf",
    "/usr/share/fonts/truetype/noto-emoji/NotoColorEmoji.ttf",
    "/usr/share/fonts/truetype/ancient-scripts/NotoColorEmoji.ttf",
]

# Tamanhos tentados ao carregar a fonte de emoji. Fontes vetoriais
# (ex.: Segoe UI Emoji) aceitam qualquer um deles; fontes em bitmap
# (ex.: Noto Color Emoji) só aceitam alguns tamanhos fixos — por isso
# tentamos vários até um funcionar.
_EMOJI_FONT_TRY_SIZES = [109, 136, 96, 128, 72, 64, 48, 40, 36, 24]

_emoji_font_warning_logged = False


def _load_font(cfg: Config, size: int) -> ImageFont.FreeTypeFont:
    fontes_dir = cfg.paths["assets_fontes"]
    if fontes_dir.exists():
        for candidate in sorted(fontes_dir.glob("*.ttf")):
            if "emoji" in candidate.name.lower():
                continue
            try:
                return ImageFont.truetype(str(candidate), size)
            except Exception:  # noqa: BLE001
                continue
    return ImageFont.load_default()


def _load_emoji_font(cfg: Config) -> tuple[ImageFont.FreeTypeFont, int] | None:
    """Procura uma fonte de emoji colorida: primeiro em assets/fontes/
    (arquivo com "emoji" no nome), depois nos caminhos padrão do
    sistema operacional. Retorna (fonte, tamanho_nativo) ou None se
    nenhuma fonte utilizável for encontrada."""
    global _emoji_font_warning_logged

    candidates: list[str] = []
    fontes_dir = cfg.paths["assets_fontes"]
    if fontes_dir.exists():
        candidates += [
            str(p) for p in sorted(fontes_dir.glob("*.tt*")) if "emoji" in p.name.lower()
        ]
    candidates += _EMOJI_FONT_CANDIDATES

    for path in candidates:
        if not Path(path).exists():
            continue
        for size in _EMOJI_FONT_TRY_SIZES:
            try:
                return ImageFont.truetype(path, size), size
            except Exception:  # noqa: BLE001
                continue

    if not _emoji_font_warning_logged:
        logger.warning(
            "Nenhuma fonte de emoji encontrada no sistema (nem em assets/fontes/). "
            "Os emojis das linhas de 'final_screen' serão removidos do texto para "
            "evitar retângulos vazios. Para ter emojis coloridos, adicione um "
            "arquivo .ttf/.ttc com 'emoji' no nome em assets/fontes/ "
            "(ex.: NotoColorEmoji.ttf), ou garanta que o sistema operacional já "
            "tenha uma fonte de emoji instalada (Segoe UI Emoji / Apple Color "
            "Emoji / Noto Color Emoji)."
        )
        _emoji_font_warning_logged = True
    return None


def _render_emoji_glyph(
    text: str,
    font: ImageFont.FreeTypeFont,
    native_size: int,
    target_size: int,
) -> Image.Image | None:
    """Renderiza um trecho de emoji numa imagem RGBA própria (no
    tamanho nativo aceito pela fonte) e redimensiona para o tamanho
    alvo, compatível com o tamanho do texto ao redor."""
    pad = max(native_size // 4, 8)
    canvas_w = native_size * max(len(text), 1) + pad * 2
    canvas_h = native_size + pad * 2
    tmp = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    tdraw = ImageDraw.Draw(tmp)
    try:
        tdraw.text((pad, pad), text, font=font, embedded_color=True)
    except Exception as e:  # noqa: BLE001
        logger.debug("Falha ao renderizar emoji '{}': {}", text, e)
        return None

    bbox = tmp.getbbox()
    if bbox is None:
        return None
    cropped = tmp.crop(bbox)
    if cropped.height == 0 or cropped.width == 0:
        return None

    scale = target_size / cropped.height
    new_w = max(1, round(cropped.width * scale))
    new_h = max(1, round(cropped.height * scale))
    try:
        return cropped.resize((new_w, new_h), Image.LANCZOS)
    except Exception as e:  # noqa: BLE001
        logger.debug("Falha ao redimensionar emoji '{}': {}", text, e)
        return None


def _safe_text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int | None:
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]
    except Exception:  # noqa: BLE001
        return None


def _split_runs(line: str) -> list[tuple[str, bool]]:
    """Divide a linha em trechos (texto, is_emoji), alternando entre
    texto normal e sequências de emoji."""
    parts = _EMOJI_RE.split(line)
    runs: list[tuple[str, bool]] = []
    for part in parts:
        if not part:
            continue
        is_emoji = bool(_EMOJI_RE.fullmatch(part))
        runs.append((part, is_emoji))
    return runs


def _build_line_layout(
    draw: ImageDraw.ImageDraw,
    line: str,
    text_font: ImageFont.FreeTypeFont,
    text_size: int,
    emoji_font_info: tuple[ImageFont.FreeTypeFont, int] | None,
) -> list[tuple[str, object, int]]:
    """Monta a lista de itens prontos para desenhar lado a lado.
    Cada item é ("text", (texto, fonte), largura) ou
    ("emoji", imagem_pil, largura). Trechos de emoji sem fonte
    disponível (ou que falhem ao renderizar) são descartados."""
    layout: list[tuple[str, object, int]] = []

    for text, is_emoji in _split_runs(line):
        if is_emoji:
            if emoji_font_info is None:
                continue
            emoji_font, native_size = emoji_font_info
            glyph = _render_emoji_glyph(text, emoji_font, native_size, text_size)
            if glyph is None:
                continue
            layout.append(("emoji", glyph, glyph.width))
        else:
            width = _safe_text_width(draw, text, text_font)
            if width is None:
                continue
            layout.append(("text", (text, text_font), width))

    # remove espaços "sobrando" nas pontas quando um emoji foi descartado
    while layout and layout[0][0] == "text" and layout[0][1][0].strip() == "":
        layout.pop(0)
    while layout and layout[-1][0] == "text" and layout[-1][1][0].strip() == "":
        layout.pop()

    return layout


def build_final_screen_image(cfg: Config, video_width: int, video_height: int,
                              output_path: Path) -> Path:
    lines: list[str] = cfg.get("final_screen", "lines", default=[])

    img = Image.new("RGBA", (video_width, video_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    text_size = 46
    text_font = _load_font(cfg, text_size)
    emoji_font_info = _load_emoji_font(cfg)

    # painel semitransparente atrás do texto para legibilidade
    panel_height = 90 * max(len(lines), 1) + 100
    panel_top = (video_height - panel_height) // 2
    draw.rectangle(
        [(video_width * 0.1, panel_top), (video_width * 0.9, panel_top + panel_height)],
        fill=(0, 0, 0, 140),
    )

    y = panel_top + 50
    for line in lines:
        layout = _build_line_layout(draw, line, text_font, text_size, emoji_font_info)
        total_w = sum(item[2] for item in layout)
        x = (video_width - total_w) / 2
        for kind, payload, width in layout:
            try:
                if kind == "emoji":
                    glyph_img = payload
                    img.paste(glyph_img, (round(x), round(y)), glyph_img)
                else:
                    text, font = payload
                    draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))
            except Exception as e:  # noqa: BLE001
                logger.debug("Não foi possível desenhar um trecho da linha: {}", e)
            x += width
        y += 90

    img.save(output_path)
    logger.info("Imagem da tela final gerada em {}", output_path)
    return output_path


def build_final_screen_filter(
    video_label: str,
    image_input_label: str,
    out_label: str,
    total_duration: float,
    screen_duration: float,
    fade_seconds: float,
) -> str:
    """Filtro para sobrepor a tela final somente durante os últimos
    `screen_duration` segundos, com um fade-in suave."""
    start = max(total_duration - screen_duration, 0.0)
    # a imagem é aberta em loop desde t=0 do processo ffmpeg, na mesma
    # linha do tempo do vídeo principal; por isso o fade usa `st=start`
    # (tempo absoluto) e não st=0, para só clarear nos últimos segundos.
    return (
        f"[{image_input_label}]format=rgba,"
        f"fade=t=in:st={start:.3f}:d={fade_seconds}:alpha=1[cta];"
        f"[{video_label}][cta]overlay=(W-w)/2:(H-h)/2:"
        f"enable='gte(t,{start:.3f})':format=auto[{out_label}]"
    )