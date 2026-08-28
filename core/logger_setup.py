"""
core.logger_setup
===================
Configura o Loguru (console + arquivo rotativo) e oferece uma função
para gerar o relatório final de cada projeto (ETAPA 16).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from loguru import logger

from core.config import Config


def setup_logging(cfg: Config) -> None:
    logger.remove()

    level = cfg.get("logging", "level", default="INFO")

    logger.add(sys.stderr, level=level, colorize=True,
                format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
                       "<level>{level: <8}</level> | "
                       "<cyan>{module}</cyan> - <level>{message}</level>")

    log_file = cfg.paths["logs"] / "pipeline_{time:YYYY-MM-DD}.log"
    logger.add(
        str(log_file),
        level=level,
        rotation=cfg.get("logging", "rotation", default="10 MB"),
        retention=cfg.get("logging", "retention", default="30 days"),
        encoding="utf-8",
    )


class ProjectReport:
    """Acumula as informações de um projeto ao longo do pipeline e grava
    um relatório final em texto e em JSON, no formato sugerido na
    ETAPA 16 do planejamento."""

    def __init__(self, cfg: Config, project_id: str):
        self.cfg = cfg
        self.project_id = project_id
        self.start_time = time.time()
        self.data: dict[str, Any] = {
            "projeto": project_id,
            "audio": None,
            "video": None,
            "musica": None,
            "legenda": "PENDENTE",
            "overlay": "PENDENTE",
            "thumbnail": "PENDENTE",
            "render": "PENDENTE",
            "tempo_segundos": None,
            "erro": None,
        }

    def set(self, **kwargs: Any) -> None:
        self.data.update(kwargs)

    def finalize(self, success: bool = True) -> Path:
        self.data["tempo_segundos"] = round(time.time() - self.start_time, 1)
        self.data["render"] = "OK" if success else "FALHOU"

        log_dir = self.cfg.paths["logs"]

        txt_path = log_dir / f"{self.project_id}.log.txt"
        json_path = log_dir / f"{self.project_id}.log.json"

        report_text = (
            f"Projeto: {self.data['projeto']}\n\n"
            f"Audio:\n{self.data['audio']}\n\n"
            f"Video:\n{self.data['video']}\n\n"
            f"Musica:\n{self.data['musica']}\n\n"
            f"Legenda:\n{self.data['legenda']}\n\n"
            f"Overlay:\n{self.data['overlay']}\n\n"
            f"Thumbnail:\n{self.data['thumbnail']}\n\n"
            f"Render:\n{self.data['render']}\n\n"
            f"Tempo:\n{self.data['tempo_segundos']} segundos\n"
        )
        if self.data.get("erro"):
            report_text += f"\nErro:\n{self.data['erro']}\n"

        txt_path.write_text(report_text, encoding="utf-8")
        json_path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2),
                              encoding="utf-8")

        logger.info("Relatório do projeto salvo em {}", txt_path)
        return txt_path
