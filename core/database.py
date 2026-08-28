"""
core.database
==============
Camada de acesso ao SQLite (videos.db). Implementa as tabelas
Videos, Musicas e Projetos descritas no planejamento, além do
scan automático dos diretórios de assets para popular o banco.
"""

from __future__ import annotations

import random
import sqlite3
import subprocess
import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from loguru import logger

from core.config import Config

SCHEMA = """
CREATE TABLE IF NOT EXISTS Videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    arquivo TEXT UNIQUE NOT NULL,
    categoria TEXT NOT NULL,
    duracao REAL,
    largura INTEGER,
    altura INTEGER,
    fps REAL,
    codec TEXT,
    usos INTEGER DEFAULT 0,
    ultima_utilizacao TEXT,
    nota REAL DEFAULT 0,
    ativo INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS Musicas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    arquivo TEXT UNIQUE NOT NULL,
    duracao REAL,
    usos INTEGER DEFAULT 0,
    ultima_utilizacao TEXT,
    ativo INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS Projetos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    titulo TEXT,
    audio TEXT,
    video TEXT,
    musica TEXT,
    thumbnail TEXT,
    duracao REAL,
    data TEXT,
    status TEXT
);
"""


@dataclass
class VideoAsset:
    id: int
    arquivo: str
    categoria: str
    duracao: float
    largura: int
    altura: int
    fps: float
    codec: str
    usos: int


@dataclass
class MusicAsset:
    id: int
    arquivo: str
    duracao: float
    usos: int


class Database:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.db_path = cfg.paths["database"]
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
        logger.debug("Schema do banco verificado/criado em {}", self.db_path)

    # ------------------------------------------------------------------
    # Probing de mídia (usado no scan de assets)
    # ------------------------------------------------------------------
    @staticmethod
    def _ffprobe_video(path: Path) -> Optional[dict]:
        try:
            cmd = [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", str(path),
            ]
            out = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return json.loads(out.stdout)
        except Exception as e:  # noqa: BLE001
            logger.warning("Falha ao rodar ffprobe em {}: {}", path, e)
            return None

    def _extract_video_metadata(self, path: Path) -> dict:
        info = self._ffprobe_video(path)
        meta = {"duracao": 0.0, "largura": 0, "altura": 0, "fps": 0.0, "codec": ""}
        if not info:
            return meta
        fmt = info.get("format", {})
        meta["duracao"] = float(fmt.get("duration", 0.0) or 0.0)
        for stream in info.get("streams", []):
            if stream.get("codec_type") == "video":
                meta["largura"] = int(stream.get("width", 0) or 0)
                meta["altura"] = int(stream.get("height", 0) or 0)
                meta["codec"] = stream.get("codec_name", "")
                fps_str = stream.get("r_frame_rate", "0/1")
                try:
                    num, den = fps_str.split("/")
                    meta["fps"] = float(num) / float(den) if float(den) else 0.0
                except Exception:  # noqa: BLE001
                    meta["fps"] = 0.0
                break
        return meta

    def _extract_audio_duration(self, path: Path) -> float:
        info = self._ffprobe_video(path)
        if not info:
            return 0.0
        return float(info.get("format", {}).get("duration", 0.0) or 0.0)

    # ------------------------------------------------------------------
    # Scan de assets -> popula Videos e Musicas
    # ------------------------------------------------------------------
    def scan_assets(self) -> None:
        """Varre assets/videos/<categoria>/*.mp4 e assets/musicas/*.mp3
        (ou outros formatos de mídia) e insere/atualiza no banco."""
        videos_root = self.cfg.paths["assets_videos"]
        musicas_root = self.cfg.paths["assets_musicas"]
        video_exts = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
        audio_exts = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}

        with self._connect() as conn:
            if videos_root.exists():
                for category_dir in sorted(videos_root.iterdir()):
                    if not category_dir.is_dir():
                        continue
                    categoria = category_dir.name
                    for file in sorted(category_dir.iterdir()):
                        if file.suffix.lower() not in video_exts:
                            continue
                        rel_path = str(file.relative_to(self.cfg.project_root))
                        existing = conn.execute(
                            "SELECT id FROM Videos WHERE arquivo = ?", (rel_path,)
                        ).fetchone()
                        if existing:
                            continue
                        meta = self._extract_video_metadata(file)
                        conn.execute(
                            """INSERT INTO Videos
                               (arquivo, categoria, duracao, largura, altura, fps, codec, usos, ativo)
                               VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1)""",
                            (rel_path, categoria, meta["duracao"], meta["largura"],
                             meta["altura"], meta["fps"], meta["codec"]),
                        )
                        logger.info("Vídeo indexado: {} [{}]", rel_path, categoria)

            if musicas_root.exists():
                for file in sorted(musicas_root.iterdir()):
                    if file.suffix.lower() not in audio_exts:
                        continue
                    rel_path = str(file.relative_to(self.cfg.project_root))
                    existing = conn.execute(
                        "SELECT id FROM Musicas WHERE arquivo = ?", (rel_path,)
                    ).fetchone()
                    if existing:
                        continue
                    duracao = self._extract_audio_duration(file)
                    conn.execute(
                        """INSERT INTO Musicas (arquivo, duracao, usos, ativo)
                           VALUES (?, ?, 0, 1)""",
                        (rel_path, duracao),
                    )
                    logger.info("Música indexada: {}", rel_path)

    # ------------------------------------------------------------------
    # Seleção ponderada por menor uso (ETAPA 4 / ETAPA 9)
    # ------------------------------------------------------------------
    def pick_least_used_video(self, categories: list[str],
                               exclude_ids: Optional[set[int]] = None,
                               orientation: Optional[str] = None) -> Optional[VideoAsset]:
        """Escolhe o vídeo menos usado dentro do pool, com fallback em
        cascata:
        1) categoria certa + orientação certa
        2) qualquer categoria + orientação certa
        3) qualquer categoria + qualquer orientação (último recurso,
           loga aviso, evita travar o pipeline por falta de asset).

        `orientation` é "vertical", "horizontal" ou None (ignora o
        filtro). Vídeos quadrados (largura == altura) entram no pool
        de ambas as orientações."""
        pool_size = self.cfg.get("selection", "pool_size", default=20)
        exclude_ids = exclude_ids or set()
        exclude_clause = ""
        exclude_params: tuple = ()
        if exclude_ids:
            exclude_clause = f" AND id NOT IN ({','.join('?' for _ in exclude_ids)})"
            exclude_params = tuple(exclude_ids)

        orientation_clause = ""
        if orientation == "vertical":
            orientation_clause = " AND altura >= largura"
        elif orientation == "horizontal":
            orientation_clause = " AND largura >= altura"

        placeholders = ",".join("?" for _ in categories)
        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT * FROM Videos
                    WHERE ativo = 1 AND categoria IN ({placeholders}){exclude_clause}{orientation_clause}
                    ORDER BY usos ASC, ultima_utilizacao ASC NULLS FIRST
                    LIMIT ?""",
                (*categories, *exclude_params, pool_size),
            ).fetchall()

            if not rows and orientation_clause:
                # sem vídeo na orientação certa dentro da categoria certa;
                # tenta a orientação certa em qualquer categoria
                rows = conn.execute(
                    f"""SELECT * FROM Videos WHERE ativo = 1{exclude_clause}{orientation_clause}
                       ORDER BY usos ASC LIMIT ?""",
                    (*exclude_params, pool_size),
                ).fetchall()

            if not rows:
                # último recurso: qualquer vídeo ativo, independente de
                # categoria e orientação (evita travar o pipeline; se
                # isso acontecer, provavelmente falta asset na
                # orientação desejada na biblioteca)
                if orientation_clause:
                    logger.warning(
                        "Nenhum vídeo na orientação '{}' disponível (mesmo fora da "
                        "categoria); caindo para qualquer orientação como último recurso.",
                        orientation,
                    )
                rows = conn.execute(
                    f"""SELECT * FROM Videos WHERE ativo = 1{exclude_clause}
                       ORDER BY usos ASC LIMIT ?""",
                    (*exclude_params, pool_size),
                ).fetchall()

            if not rows:
                return None
            chosen = random.choice(rows)
            return VideoAsset(
                id=chosen["id"], arquivo=chosen["arquivo"], categoria=chosen["categoria"],
                duracao=chosen["duracao"], largura=chosen["largura"], altura=chosen["altura"],
                fps=chosen["fps"], codec=chosen["codec"], usos=chosen["usos"],
            )

    def pick_least_used_music(self, min_duration: float = 0.0) -> Optional[MusicAsset]:
        pool_size = self.cfg.get("selection", "pool_size", default=20)
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM Musicas WHERE ativo = 1
                   ORDER BY usos ASC, ultima_utilizacao ASC NULLS FIRST
                   LIMIT ?""",
                (pool_size,),
            ).fetchall()
            if not rows:
                return None
            chosen = random.choice(rows)
            return MusicAsset(
                id=chosen["id"], arquivo=chosen["arquivo"],
                duracao=chosen["duracao"], usos=chosen["usos"],
            )

    def increment_video_usage(self, video_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE Videos SET usos = usos + 1, ultima_utilizacao = ? WHERE id = ?",
                (datetime.now().isoformat(), video_id),
            )

    def increment_music_usage(self, music_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE Musicas SET usos = usos + 1, ultima_utilizacao = ? WHERE id = ?",
                (datetime.now().isoformat(), music_id),
            )

    # ------------------------------------------------------------------
    # Leituras usadas pela classificação (ETAPA 3) e pela interface gráfica
    # ------------------------------------------------------------------
    def get_active_categories(self) -> list[str]:
        """Retorna todas as categorias de vídeo que existem de fato na
        biblioteca (ao menos um vídeo ativo cadastrado). Usada pela
        classificação (LLM e por palavras-chave) para nunca ficar
        restrita apenas às poucas categorias listadas em
        `classification.default_categories` do config.yaml."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT categoria FROM Videos WHERE ativo = 1 ORDER BY categoria"
            ).fetchall()
        return [row["categoria"] for row in rows]

    def asset_summary(self) -> dict:
        """Resumo da biblioteca de assets (para exibição na GUI)."""
        with self._connect() as conn:
            video_rows = conn.execute(
                """SELECT categoria, COUNT(*) as total FROM Videos
                   WHERE ativo = 1 GROUP BY categoria ORDER BY categoria"""
            ).fetchall()
            total_videos = conn.execute(
                "SELECT COUNT(*) FROM Videos WHERE ativo = 1"
            ).fetchone()[0]
            total_musicas = conn.execute(
                "SELECT COUNT(*) FROM Musicas WHERE ativo = 1"
            ).fetchone()[0]
        return {
            "por_categoria": {row["categoria"]: row["total"] for row in video_rows},
            "total_videos": total_videos,
            "total_musicas": total_musicas,
        }

    def list_projects(self, limit: int = 200) -> list[sqlite3.Row]:
        """Retorna os projetos mais recentes (para o histórico na GUI)."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM Projetos ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()

    def register_project(self, titulo: str, audio: str, video: str, musica: str,
                          thumbnail: str, duracao: float, status: str) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO Projetos
                   (titulo, audio, video, musica, thumbnail, duracao, data, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (titulo, audio, video, musica, thumbnail, duracao,
                 datetime.now().isoformat(), status),
            )
            return cur.lastrowid
