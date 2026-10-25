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

# Migrações aditivas: rodam uma única vez e são ignoradas se a coluna já
# existir (o SQLite lança OperationalError "duplicate column name").
_MIGRATIONS = [
    # usado pelo scan para detectar arquivo substituído e reexibir metadados
    "ALTER TABLE Videos ADD COLUMN mtime REAL DEFAULT 0",
    "ALTER TABLE Musicas ADD COLUMN mtime REAL DEFAULT 0",
]


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
            for statement in _MIGRATIONS:
                try:
                    conn.execute(statement)
                except sqlite3.OperationalError as e:
                    if "duplicate column name" not in str(e).lower():
                        logger.warning("Migração de schema falhou ({}): {}", statement, e)
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
        """Varre assets/videos/<categoria>/* e assets/musicas/* e:
        - insere arquivos novos;
        - reexibe metadados de arquivos que mudaram (mtime diferente);
        - marca como inativo o que sumiu do disco (para o pipeline nunca
          tentar usar um arquivo que não existe mais)."""
        videos_root = self.cfg.paths["assets_videos"]
        musicas_root = self.cfg.paths["assets_musicas"]
        video_exts = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
        audio_exts = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}

        seen_videos: set[str] = set()
        seen_musicas: set[str] = set()

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
                        seen_videos.add(rel_path)
                        try:
                            mtime = float(file.stat().st_mtime)
                        except OSError:
                            continue

                        existing = conn.execute(
                            "SELECT id, mtime, ativo FROM Videos WHERE arquivo = ?", (rel_path,)
                        ).fetchone()
                        if existing:
                            if existing["ativo"] and float(existing["mtime"] or 0.0) == mtime:
                                continue
                            meta = self._extract_video_metadata(file)
                            conn.execute(
                                """UPDATE Videos SET categoria=?, duracao=?, largura=?,
                                   altura=?, fps=?, codec=?, mtime=?, ativo=1 WHERE id=?""",
                                (categoria, meta["duracao"], meta["largura"], meta["altura"],
                                 meta["fps"], meta["codec"], mtime, existing["id"]),
                            )
                            if not existing["ativo"]:
                                logger.info("Vídeo reativado: {}", rel_path)
                            else:
                                logger.info("Metadados atualizados (arquivo alterado): {}", rel_path)
                            continue

                        meta = self._extract_video_metadata(file)
                        conn.execute(
                            """INSERT INTO Videos
                               (arquivo, categoria, duracao, largura, altura, fps, codec,
                                usos, ativo, mtime)
                               VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1, ?)""",
                            (rel_path, categoria, meta["duracao"], meta["largura"],
                             meta["altura"], meta["fps"], meta["codec"], mtime),
                        )
                        logger.info("Vídeo indexado: {} [{}]", rel_path, categoria)

            if musicas_root.exists():
                for file in sorted(musicas_root.iterdir()):
                    if file.suffix.lower() not in audio_exts:
                        continue
                    rel_path = str(file.relative_to(self.cfg.project_root))
                    seen_musicas.add(rel_path)
                    try:
                        mtime = float(file.stat().st_mtime)
                    except OSError:
                        continue

                    existing = conn.execute(
                        "SELECT id, mtime, ativo FROM Musicas WHERE arquivo = ?", (rel_path,)
                    ).fetchone()
                    if existing:
                        if existing["ativo"] and float(existing["mtime"] or 0.0) == mtime:
                            continue
                        duracao = self._extract_audio_duration(file)
                        conn.execute(
                            "UPDATE Musicas SET duracao=?, mtime=?, ativo=1 WHERE id=?",
                            (duracao, mtime, existing["id"]),
                        )
                        logger.info("Música reativada/atualizada: {}", rel_path)
                        continue

                    duracao = self._extract_audio_duration(file)
                    conn.execute(
                        """INSERT INTO Musicas (arquivo, duracao, usos, ativo, mtime)
                           VALUES (?, ?, 0, 1, ?)""",
                        (rel_path, duracao, mtime),
                    )
                    logger.info("Música indexada: {}", rel_path)

            # ----- arquivos que sumiram do disco -> inativos -----
            for row in conn.execute("SELECT arquivo FROM Videos WHERE ativo = 1").fetchall():
                if row["arquivo"] not in seen_videos:
                    conn.execute("UPDATE Videos SET ativo = 0 WHERE arquivo = ?", (row["arquivo"],))
                    logger.warning("Vídeo não está mais no disco; marcado como inativo: {}",
                                   row["arquivo"])
            for row in conn.execute("SELECT arquivo FROM Musicas WHERE ativo = 1").fetchall():
                if row["arquivo"] not in seen_musicas:
                    conn.execute("UPDATE Musicas SET ativo = 0 WHERE arquivo = ?", (row["arquivo"],))
                    logger.warning("Música não está mais no disco; marcada como inativa: {}",
                                   row["arquivo"])

    # ------------------------------------------------------------------
    # Seleção ponderada por menor uso (ETAPA 4 / ETAPA 9)
    # ------------------------------------------------------------------
    @staticmethod
    def _video_conditions(
        categories: list[str],
        exclude_ids: set[int],
        orientation: Optional[str],
        min_duration: float,
        *,
        use_categories: bool,
        use_orientation: bool,
        use_duration: bool,
    ) -> tuple[str, list]:
        """Monta WHERE + parâmetros da consulta de vídeos. Todos os valores
        entram como parâmetro placeholder (`?`) — nenhum texto vindo de
        arquivo/usuário é interpolado no SQL."""
        conditions = ["ativo = 1"]
        params: list = []

        if use_categories and categories:
            conditions.append(f"categoria IN ({','.join('?' for _ in categories)})")
            params.extend(categories)
        if exclude_ids:
            ordered = sorted(exclude_ids)
            conditions.append(f"id NOT IN ({','.join('?' for _ in ordered)})")
            params.extend(ordered)
        if use_orientation and orientation == "vertical":
            conditions.append("altura >= largura")
        elif use_orientation and orientation == "horizontal":
            conditions.append("largura >= altura")
        if use_duration and min_duration > 0:
            conditions.append("duracao >= ?")
            params.append(float(min_duration))

        return " AND ".join(conditions), params

    # Cascata de relaxamento: cada passo abre mão de um critério em vez de
    # jogar tudo para "qualquer vídeo" de uma vez só.
    _VIDEO_CASCADE = (
        ("categoria + orientação + duração", True, True, True),
        ("categoria + orientação", True, True, False),
        ("categoria + duração", True, False, True),
        ("categoria", True, False, False),
        ("orientação + duração", False, True, True),
        ("orientação", False, True, False),
        ("duração", False, False, True),
        ("qualquer vídeo", False, False, False),
    )

    def pick_least_used_video(self, categories: list[str],
                              exclude_ids: Optional[set[int]] = None,
                              orientation: Optional[str] = None,
                              min_duration: float = 0.0) -> Optional[VideoAsset]:
        """Escolhe o vídeo menos usado dentro do pool, relaxando os
        critérios aos poucos (categoria, orientação e duração).

        `min_duration` é a duração necessária do trecho: prioriza vídeos
        longos o bastante para cobri-lo sem entrar em loop (loop curto
        repetitivo piora o resultado). Só cai para vídeos mais curtos
        quando não há outra opção na biblioteca."""
        pool_size = self.cfg.get("selection", "pool_size", default=20)
        exclude_ids = exclude_ids or set()
        last_index = len(self._VIDEO_CASCADE) - 1

        for index, (label, use_cat, use_orient, use_dur) in enumerate(self._VIDEO_CASCADE):
            where, params = self._video_conditions(
                categories, exclude_ids, orientation, min_duration,
                use_categories=use_cat, use_orientation=use_orient, use_duration=use_dur,
            )
            with self._connect() as conn:
                rows = conn.execute(
                    f"""SELECT * FROM Videos WHERE {where}
                        ORDER BY usos ASC, ultima_utilizacao ASC NULLS FIRST
                        LIMIT ?""",
                    (*params, pool_size),
                ).fetchall()

            if not rows:
                continue

            if index > 0:
                if index == last_index:
                    logger.warning(
                        "Selecionando sem nenhum critério; revise a biblioteca de assets "
                        "(categoria/orientação/duração).",
                    )
                else:
                    logger.info("Sem opções no critério anterior; usando '{}'.", label)

            chosen = random.choice(rows)
            return VideoAsset(
                id=chosen["id"], arquivo=chosen["arquivo"], categoria=chosen["categoria"],
                duracao=chosen["duracao"], largura=chosen["largura"], altura=chosen["altura"],
                fps=chosen["fps"], codec=chosen["codec"], usos=chosen["usos"],
            )
        return None

    def count_available_videos(self, categories: Optional[list[str]] = None,
                               orientation: Optional[str] = None,
                               min_duration: float = 0.0) -> int:
        """Quantos vídeos ativos atendem aos critérios. O pipeline usa isso
        para limitar o número de trechos ao que a biblioteca suporta,
        evitando repetir o mesmo clipe várias vezes no mesmo vídeo."""
        where, params = self._video_conditions(
            list(categories or []), set(), orientation, min_duration,
            use_categories=bool(categories), use_orientation=orientation is not None,
            use_duration=min_duration > 0,
        )
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS total FROM Videos WHERE {where}", params
            ).fetchone()
        return int(row["total"])

    def pick_least_used_music(self, min_duration: float = 0.0) -> Optional[MusicAsset]:
        """Escolhe a música menos usada, preferindo uma com duração >=
        `min_duration` (evita loop audível da trilha). Se não houver
        nenhuma longa o bastante, cai para qualquer uma — a mixagem
        faz loop automático até cobrir a narração."""
        pool_size = self.cfg.get("selection", "pool_size", default=20)

        for use_duration in (True, False):
            conditions = ["ativo = 1"]
            params: list = []
            if use_duration and min_duration > 0:
                conditions.append("duracao >= ?")
                params.append(float(min_duration))

            with self._connect() as conn:
                rows = conn.execute(
                    f"""SELECT * FROM Musicas WHERE {' AND '.join(conditions)}
                        ORDER BY usos ASC, ultima_utilizacao ASC NULLS FIRST
                        LIMIT ?""",
                    (*params, pool_size),
                ).fetchall()

            if not rows:
                continue

            if not use_duration and min_duration > 0:
                logger.warning(
                    "Nenhuma música cobre {:.0f}s; usando uma mais curta (a mixagem faz "
                    "loop). Acrescente trilhas mais longas em assets/musicas/ para evitar "
                    "repetição.", min_duration,
                )
            chosen = random.choice(rows)
            return MusicAsset(
                id=chosen["id"], arquivo=chosen["arquivo"],
                duracao=chosen["duracao"], usos=chosen["usos"],
            )
        return None

    def increment_video_usage(self, video_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE Videos SET usos = usos + 1, ultima_utilizacao = ? WHERE id = ?",
                (datetime.now().isoformat(), video_id),
            )

    def mark_video_inactive(self, video_id: int) -> None:
        """Marca um asset cujo arquivo não existe mais no disco."""
        with self._connect() as conn:
            conn.execute("UPDATE Videos SET ativo = 0 WHERE id = ?", (video_id,))

    def mark_music_inactive(self, music_id: int) -> None:
        """Marca uma trilha cujo arquivo não existe mais no disco."""
        with self._connect() as conn:
            conn.execute("UPDATE Musicas SET ativo = 0 WHERE id = ?", (music_id,))

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
