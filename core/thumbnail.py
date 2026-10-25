"""
core.thumbnail
================
ETAPA 13 - Geração de thumbnail (opcional).

Se o usuário já colocou uma thumbnail pronta em input/thumbs/<nome>.jpg,
ela é usada diretamente. Caso contrário, extrai vários frames candidatos
do clipe de fundo já processado, escolhe o mais nítido (maior variância
do Laplaciano, um proxy comum de foco/nitidez) e sobrepõe a primeira
linha do texto/roteiro como título.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from loguru import logger
from PIL import Image, ImageDraw, ImageFont

from core.config import Config
from core.ffmpeg_utils import ffprobe_duration, run_ffmpeg


def find_ready_thumbnail(cfg: Config, audio_stem: str) -> Optional[Path]:
    thumbs_dir = cfg.paths["input_thumbs"]
    for ext in (".jpg", ".jpeg", ".png"):
        candidate = thumbs_dir / f"{audio_stem}{ext}"
        if candidate.exists():
            return candidate
    return None


def _sharpness_score(image_path: Path) -> float:
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return -1.0
    # variância do Laplaciano: quanto maior, mais nítida/detalhada a imagem
    return float(cv2.Laplacian(img, cv2.CV_64F).var())


def _brightness_score(image_path: Path) -> float:
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return 0.0
    mean = float(np.mean(img))
    # penaliza extremos (muito escuro ou estourado), prioriza "boa iluminação"
    return 1.0 - abs(mean - 130.0) / 130.0


def extract_best_frame(cfg: Config, video_path: Path, cache_dir: Path) -> Path:
    n_candidates = cfg.get("thumbnail", "candidate_frames", default=8)
    duration = ffprobe_duration(video_path)
    if duration <= 0:
        duration = 1.0

    candidates: list[Path] = []
    for i in range(n_candidates):
        # evita os primeiros/últimos 5% do vídeo (transições de movimento)
        t = duration * (0.05 + 0.90 * i / max(n_candidates - 1, 1))
        frame_path = cache_dir / f"thumb_candidate_{i:02d}.jpg"
        run_ffmpeg(
            ["-ss", f"{t:.3f}", "-i", str(video_path), "-frames:v", "1",
             "-q:v", "2", str(frame_path)],
            description=f"extract_frame_{i}",
        )
        candidates.append(frame_path)

    scored = []
    for frame in candidates:
        score = 0.7 * _sharpness_score(frame) / 500.0 + 0.3 * _brightness_score(frame)
        scored.append((score, frame))

    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0][1]
    logger.info("Frame escolhido para thumbnail: {} (score={:.3f})", best.name, scored[0][0])
    return best


def _load_font(cfg: Config, size: int) -> ImageFont.FreeTypeFont:
    fontes_dir = cfg.paths["assets_fontes"]
    if fontes_dir.exists():
        for candidate in fontes_dir.glob("*.ttf"):
            try:
                return ImageFont.truetype(str(candidate), size)
            except Exception:  # noqa: BLE001
                continue
    return ImageFont.load_default()


def compose_thumbnail(cfg: Config, frame_path: Path, title_text: str,
                       output_path: Path) -> Path:
    img = Image.open(frame_path).convert("RGB")
    w, h = img.size

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # gradiente escuro na base para dar contraste ao texto
    gradient_height = int(h * 0.4)
    for i in range(gradient_height):
        alpha = int(180 * (i / gradient_height))
        y = h - gradient_height + i
        draw.line([(0, y), (w, y)], fill=(0, 0, 0, alpha))

    font_size = int(h * 0.08)
    font = _load_font(cfg, font_size)
    # reduz a fonte até o título caber na largura da imagem (títulos
    # longos estouravam a margem e ficavam ilegíveis/cortados)
    while font_size > 12:
        bbox = draw.textbbox((0, 0), title_text, font=font)
        if (bbox[2] - bbox[0]) <= w * 0.9:
            break
        font_size = int(font_size * 0.9)
        font = _load_font(cfg, font_size)
    else:
        # fonte bitmap (load_default) não escala: corta o texto mesmo
        while title_text and (draw.textbbox((0, 0), title_text, font=font)[2] > w * 0.9):
            title_text = title_text[:-1]

    bbox = draw.textbbox((0, 0), title_text, font=font)
    text_w = bbox[2] - bbox[0]
    x = (w - text_w) / 2
    y = h - gradient_height * 0.6

    # contorno preto + texto branco para legibilidade
    stroke = max(2, font_size // 12)
    for dx in (-stroke, 0, stroke):
        for dy in (-stroke, 0, stroke):
            draw.text((x + dx, y + dy), title_text, font=font, fill=(0, 0, 0, 255))
    draw.text((x, y), title_text, font=font, fill=(255, 255, 255, 255))

    combined = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    combined.save(output_path, quality=92)
    logger.info("Thumbnail final gerada em {}", output_path)
    return output_path


def generate_thumbnail(
    cfg: Config,
    audio_stem: str,
    processed_video_path: Path,
    title_text: str,
    cache_dir: Path,
    output_path: Path,
) -> Path:
    ready = find_ready_thumbnail(cfg, audio_stem)
    if ready is not None:
        logger.info("Usando thumbnail pronta fornecida pelo usuário: {}", ready)
        Image.open(ready).convert("RGB").save(output_path, quality=92)
        return output_path

    if not cfg.get("thumbnail", "enabled", default=True):
        logger.info("Geração de thumbnail desabilitada no config.")
        return output_path

    best_frame = extract_best_frame(cfg, processed_video_path, cache_dir)
    return compose_thumbnail(cfg, best_frame, title_text, output_path)
