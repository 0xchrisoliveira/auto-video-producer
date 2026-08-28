#!/usr/bin/env python3
"""
main.py
========
Ponto de entrada do pipeline "Auto Video Producer".

Uso:
    python main.py watch                 # ETAPA 1: monitora input/audio/ continuamente
    python main.py run <arquivo.wav>      # processa um único áudio manualmente
    python main.py run-text <arquivo.txt> # processa um texto (TTS -> vídeo)
    python main.py scan                   # (re)popula o banco a partir dos assets em disco
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from loguru import logger

from core.config import load_config
from core.database import Database
from core.logger_setup import setup_logging
from core.pipeline import run_pipeline_for_audio, run_pipeline_for_text
from core.watcher import start_watching


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pipeline automatizado 'Auto Video Producer'")
    parser.add_argument("--config", default="config.yaml", help="Caminho do config.yaml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("watch", help="Monitora input/audio/ e processa novos áudios automaticamente")
    subparsers.add_parser("scan", help="Varre assets/ e popula/atualiza o banco de dados")

    run_parser = subparsers.add_parser("run", help="Processa um único arquivo de áudio (.wav)")
    run_parser.add_argument("audio", help="Caminho para o arquivo .wav da narração")

    run_text_parser = subparsers.add_parser("run-text", help="Processa um arquivo de texto (.txt) -> TTS -> vídeo")
    run_text_parser.add_argument("text", help="Caminho para o arquivo .txt com o texto")

    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    cfg = load_config(args.config)
    setup_logging(cfg)

    db = Database(cfg)

    if args.command == "scan":
        logger.info("Escaneando assets para popular o banco de dados...")
        db.scan_assets()
        logger.success("Scan concluído.")
        return 0

    if args.command == "run":
        audio_path = Path(args.audio).resolve()
        if not audio_path.exists():
            logger.error("Arquivo de áudio não encontrado: {}", audio_path)
            return 1
        db.scan_assets()  # garante que o banco reflita os assets atuais
        result = run_pipeline_for_audio(cfg, db, audio_path)
        return 0 if result is not None else 1

    if args.command == "run-text":
        text_path = Path(args.text).resolve()
        if not text_path.exists():
            logger.error("Arquivo de texto não encontrado: {}", text_path)
            return 1
        if not text_path.suffix.lower() == ".txt":
            logger.error("Arquivo deve ser .txt: {}", text_path)
            return 1
        db.scan_assets()
        result = run_pipeline_for_text(cfg, db, text_path)
        return 0 if result is not None else 1

    if args.command == "watch":
        db.scan_assets()
        start_watching(cfg, db)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())