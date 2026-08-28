"""
core.video_processor
======================
ETAPA 5 - Verificação técnica / padronização do vídeo.
ETAPA 6 - Escolha do trecho (nunca usar o vídeo desde o início).
ETAPA 7 - Movimento cinematográfico (zoom/pan suave).
ETAPA 8 - Correção de cor (identidade visual consistente).

Todas as etapas de manipulação puramente visual do vídeo de fundo são
resolvidas em uma única passagem de filtro do ffmpeg por eficiência.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from core.config import Config
from core.ffmpeg_utils import ffprobe_duration, run_ffmpeg


@dataclass
class ClipPlan:
    source: Path
    start: float
    end: float
    movement: str
    looped: bool


def standardize_video(cfg: Config, input_path: Path, output_path: Path) -> None:
    """ETAPA 5 (uso avulso): converte um asset para o padrão interno
    (1920x1080, H264, 30 FPS, YUV420) sem qualquer corte ou efeito.
    Útil para pré-processar a biblioteca de assets no momento da
    ingestão, deixando o pipeline principal mais rápido depois."""
    w = cfg.get("video", "standard_width", default=1920)
    h = cfg.get("video", "standard_height", default=1080)
    fps = cfg.get("video", "standard_fps", default=30)
    codec = cfg.get("video", "codec", default="libx264")
    pix_fmt = cfg.get("video", "pixel_format", default="yuv420p")

    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},fps={fps},setsar=1"
    )
    run_ffmpeg(
        [
            "-i", str(input_path),
            "-vf", vf,
            "-c:v", codec, "-pix_fmt", pix_fmt,
            "-c:a", "aac", "-b:a", "192k",
            str(output_path),
        ],
        description="standardize_video",
    )


def _plan_clip_window(video_duration: float, narration_duration: float) -> tuple[float, float, bool]:
    """ETAPA 6 - Nunca usar o vídeo desde o início. Sorteia um ponto de
    partida válido de modo que o trecho [start, start+narration] caiba
    dentro do vídeo. Se o vídeo for mais curto que a narração, sinaliza
    que será necessário fazer loop."""
    if video_duration <= narration_duration:
        return 0.0, video_duration, True

    # margem mínima para nunca começar exatamente no frame 0
    min_start = min(1.0, video_duration * 0.02)
    max_start = video_duration - narration_duration
    if max_start <= min_start:
        start = 0.0
    else:
        start = random.uniform(min_start, max_start)
    return start, start + narration_duration, False


def _build_movement_filter(movement: str, duration: float, width: int, height: int) -> str:
    """ETAPA 7 - Constrói a cadeia de filtros de crop dinâmico (com
    eval=frame) que produz um zoom ou pan lento e suave ao longo da
    duração do clipe. A imagem é previamente escalada acima do alvo
    para dar 'espaço' de manobra ao crop."""
    # fator de folga para permitir pan/zoom sem exceder as bordas
    slack = 1.18
    work_w = int(width * slack) // 2 * 2
    work_h = int(height * slack) // 2 * 2

    d = max(duration, 0.1)

    if movement == "zoom_in":
        # crop começa no tamanho total (work) e encolhe suavemente 8%
        crop_w = f"iw*(1-0.08*min(t,{d})/{d})"
        crop_h = f"ih*(1-0.08*min(t,{d})/{d})"
        x, y = "(iw-ow)/2", "(ih-oh)/2"
    elif movement == "zoom_out":
        crop_w = f"iw*(0.92+0.08*min(t,{d})/{d})"
        crop_h = f"ih*(0.92+0.08*min(t,{d})/{d})"
        x, y = "(iw-ow)/2", "(ih-oh)/2"
    elif movement == "pan_left_right":
        crop_w, crop_h = f"iw/{slack:.4f}", f"ih/{slack:.4f}"
        x = f"(iw-ow)*min(t,{d})/{d}"
        y = "(ih-oh)/2"
    elif movement == "pan_right_left":
        crop_w, crop_h = f"iw/{slack:.4f}", f"ih/{slack:.4f}"
        x = f"(iw-ow)*(1-min(t,{d})/{d})"
        y = "(ih-oh)/2"
    elif movement == "pan_bottom_top":
        crop_w, crop_h = f"iw/{slack:.4f}", f"ih/{slack:.4f}"
        x = "(iw-ow)/2"
        y = f"(ih-oh)*(1-min(t,{d})/{d})"
    elif movement == "pan_top_bottom":
        crop_w, crop_h = f"iw/{slack:.4f}", f"ih/{slack:.4f}"
        x = "(iw-ow)/2"
        y = f"(ih-oh)*min(t,{d})/{d}"
    else:
        raise ValueError(f"Movimento desconhecido: {movement}")

    return (
        f"scale={work_w}:{work_h}:force_original_aspect_ratio=increase,"
        f"crop=w='{crop_w}':h='{crop_h}':x='{x}':y='{y}'"
    )


def _build_color_filter(cfg: Config) -> str:
    """ETAPA 8 - Correção de cor para identidade visual consistente:
    leve contraste, saturação, temperatura quente e vinheta."""
    contrast = cfg.get("color_correction", "contrast", default=1.05)
    saturation = cfg.get("color_correction", "saturation", default=1.08)
    vignette = cfg.get("color_correction", "vignette", default=True)

    parts = [f"eq=contrast={contrast}:saturation={saturation}"]
    # tom levemente quente: leve aumento no canal de vermelho/amarelo,
    # leve redução no azul, via colorbalance (afeta sombras/meios-tons/luzes)
    parts.append("colorbalance=rs=0.04:gs=0.01:bs=-0.04:rm=0.03:gm=0.0:bm=-0.03")
    if vignette:
        parts.append("vignette=PI/5")
    return ",".join(parts)


def choose_movement(cfg: Config) -> str:
    options = cfg.get("camera_movement", "options", default=["zoom_in"])
    return random.choice(options)


def plan_segment_durations(cfg: Config, narration_duration: float) -> list[float]:
    """ETAPA 6 (revisada) - Em vez de usar um único vídeo (fazendo loop
    quando ele é mais curto que a narração), divide a narração em
    vários trechos de vídeos diferentes, cada um com duração dentro de
    [min_clip_seconds, max_clip_seconds]. Isso evita o efeito visual
    de "loop curto e repetitivo" quando a biblioteca de assets tem
    vídeos curtos."""
    min_clip = float(cfg.get("background_composition", "min_clip_seconds", default=5.0))
    max_clip = float(cfg.get("background_composition", "max_clip_seconds", default=10.0))
    max_clips = int(cfg.get("background_composition", "max_clips", default=8))

    min_clip = max(min_clip, 1.0)
    max_clip = max(max_clip, min_clip + 1.0)

    if narration_duration <= max_clip:
        return [narration_duration]

    avg = (min_clip + max_clip) / 2
    count = max(2, round(narration_duration / avg))
    count = min(count, max_clips)
    # nunca deixa a média cair abaixo do mínimo configurado
    while count > 1 and narration_duration / count < min_clip:
        count -= 1

    base = narration_duration / count
    durations = [base] * count
    # jitter leve entre os segmentos pra não ficar mecânico (todos com
    # exatamente a mesma duração), mantendo a soma total exata
    for i in range(count - 1):
        max_jitter = max(min(base * 0.25, max_clip - base, base - min_clip), 0.0)
        if max_jitter > 0:
            delta = random.uniform(-max_jitter, max_jitter)
            durations[i] += delta
            durations[-1] -= delta

    logger.info(
        "Planejados {} trecho(s) de vídeo de fundo para {:.1f}s de narração: {}",
        count, narration_duration, ", ".join(f"{d:.1f}s" for d in durations),
    )
    return durations


def prepare_background_sequence(
    cfg: Config,
    video_paths: list[Path],
    segment_durations: list[float],
    cache_dir: Path,
    output_path: Path,
) -> list[ClipPlan]:
    """Versão que compõe o vídeo de fundo com vários trechos (de vídeos
    iguais ou diferentes, conforme `video_paths`), processando cada um
    (ETAPAS 5-8) separadamente e concatenando tudo em `output_path`
    com a duração total da narração. Se só houver um trecho, cai no
    comportamento de `prepare_background_clip` normalmente."""
    if len(video_paths) != len(segment_durations):
        raise ValueError("video_paths e segment_durations precisam ter o mesmo tamanho")

    if len(video_paths) == 1:
        plan = prepare_background_clip(cfg, video_paths[0], segment_durations[0], output_path)
        return [plan]

    fps = cfg.get("video", "standard_fps", default=30)
    codec = cfg.get("video", "codec", default="libx264")
    pix_fmt = cfg.get("video", "pixel_format", default="yuv420p")

    cache_dir.mkdir(parents=True, exist_ok=True)
    plans: list[ClipPlan] = []
    part_paths: list[Path] = []
    for i, (video_path, duration) in enumerate(zip(video_paths, segment_durations)):
        part_path = cache_dir / f"bg_part_{i:02d}.mp4"
        plan = prepare_background_clip(cfg, video_path, duration, part_path)
        plans.append(plan)
        part_paths.append(part_path)

    concat_list_path = cache_dir / "bg_concat_list.txt"
    with open(concat_list_path, "w", encoding="utf-8") as f:
        for p in part_paths:
            escaped = str(p.resolve()).replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")

    run_ffmpeg(
        [
            "-f", "concat", "-safe", "0", "-i", str(concat_list_path),
            "-c:v", codec, "-pix_fmt", pix_fmt, "-r", str(fps),
            "-an",
            str(output_path),
        ],
        description="prepare_background_sequence (concat)",
    )

    logger.info(
        "Vídeo de fundo composto por {} trecho(s) diferentes -> {}",
        len(video_paths), output_path,
    )
    return plans


def prepare_background_clip(
    cfg: Config,
    video_path: Path,
    narration_duration: float,
    output_path: Path,
) -> ClipPlan:
    """Executa as ETAPAS 5-8 em uma única chamada de ffmpeg: seleciona o
    trecho, padroniza resolução/fps/codec, aplica o movimento
    cinematográfico e a correção de cor."""
    w = cfg.get("video", "standard_width", default=1920)
    h = cfg.get("video", "standard_height", default=1080)
    fps = cfg.get("video", "standard_fps", default=30)
    codec = cfg.get("video", "codec", default="libx264")
    pix_fmt = cfg.get("video", "pixel_format", default="yuv420p")

    video_duration = ffprobe_duration(video_path)
    start, end, looped = _plan_clip_window(video_duration, narration_duration)
    movement = choose_movement(cfg)

    logger.info(
        "Preparando clipe de fundo: {} | trecho {:.2f}s -> {:.2f}s | movimento={} | loop={}",
        video_path.name, start, end, movement, looped,
    )

    movement_filter = _build_movement_filter(movement, narration_duration, w, h)
    color_filter = _build_color_filter(cfg)
    vf = f"{movement_filter},{color_filter},scale={w}:{h},fps={fps},format={pix_fmt},setsar=1"

    args: list[str] = []
    if looped:
        # vídeo mais curto que a narração: repete em loop até cobrir a duração
        args += ["-stream_loop", "-1"]
    args += ["-i", str(video_path)]
    if not looped:
        args += ["-ss", f"{start:.3f}"]
    args += ["-t", f"{narration_duration:.3f}"]
    args += [
        "-vf", vf,
        "-an",
        "-c:v", codec, "-pix_fmt", pix_fmt,
        str(output_path),
    ]

    run_ffmpeg(args, description="prepare_background_clip")

    return ClipPlan(source=video_path, start=start, end=end, movement=movement, looped=looped)
