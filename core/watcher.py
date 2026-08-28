"""
core.watcher
=============
ETAPA 1 - Monitoramento da pasta de entrada (input/audio/).

Assim que um novo arquivo .wav aparece, o pipeline é disparado
automaticamente. Usa a biblioteca `watchdog` (recomendada) para
eventos de sistema de arquivos em tempo real, com fallback opcional
para varredura por polling caso watchdog não esteja disponível.
"""

from __future__ import annotations

import fnmatch
import threading
import time
from pathlib import Path
from queue import Queue

from loguru import logger

from core.config import Config
from core.database import Database
from core.pipeline import run_pipeline_for_audio


def _is_stable(path: Path, wait: float = 1.0) -> bool:
    """Evita processar um arquivo que ainda está sendo copiado/gravado:
    checa se o tamanho do arquivo permanece igual após um pequeno
    intervalo de espera."""
    try:
        size1 = path.stat().st_size
        time.sleep(wait)
        size2 = path.stat().st_size
        return size1 == size2 and size1 > 0
    except FileNotFoundError:
        return False


def process_file_when_ready(cfg: Config, db: Database, path: Path) -> None:
    if not _is_stable(path):
        logger.warning("Arquivo {} parece ainda estar sendo escrito; será tentado no próximo ciclo.", path)
        return
    logger.info("Novo áudio detectado: {}", path)
    run_pipeline_for_audio(cfg, db, path)


def watch_with_watchdog(cfg: Config, db: Database,
                         stop_event: threading.Event | None = None) -> None:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    pattern = cfg.get("watcher", "file_pattern", default="*.wav")
    audio_dir = cfg.paths["input_audio"]
    queue: Queue[Path] = Queue()

    class Handler(FileSystemEventHandler):
        def on_created(self, event):  # noqa: ANN001
            if event.is_directory:
                return
            p = Path(event.src_path)
            if fnmatch.fnmatch(p.name, pattern):
                queue.put(p)

        def on_moved(self, event):  # noqa: ANN001
            if event.is_directory:
                return
            p = Path(event.dest_path)
            if fnmatch.fnmatch(p.name, pattern):
                queue.put(p)

    observer = Observer()
    observer.schedule(Handler(), str(audio_dir), recursive=False)
    observer.start()
    logger.info("Monitorando {} por novos arquivos '{}' (watchdog)...", audio_dir, pattern)

    try:
        while stop_event is None or not stop_event.is_set():
            if not queue.empty():
                path = queue.get()
                process_file_when_ready(cfg, db, path)
            else:
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("Encerrando monitoramento (watchdog)...")
        observer.stop()
        observer.join()


def watch_with_polling(cfg: Config, db: Database,
                        stop_event: threading.Event | None = None) -> None:
    pattern = cfg.get("watcher", "file_pattern", default="*.wav")
    interval = cfg.get("watcher", "poll_interval_seconds", default=5)
    audio_dir = cfg.paths["input_audio"]

    logger.info("Monitorando {} por novos arquivos '{}' (polling a cada {}s)...",
                audio_dir, pattern, interval)
    seen: set[str] = set()

    try:
        while stop_event is None or not stop_event.is_set():
            for file in sorted(audio_dir.glob(pattern)):
                if stop_event is not None and stop_event.is_set():
                    break
                if file.name in seen:
                    continue
                seen.add(file.name)
                process_file_when_ready(cfg, db, file)
            # dorme em pequenos passos para reagir rápido ao stop_event
            slept = 0.0
            while slept < interval and (stop_event is None or not stop_event.is_set()):
                time.sleep(0.5)
                slept += 0.5
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("Encerrando monitoramento (polling)...")


def start_watching(cfg: Config, db: Database,
                    stop_event: threading.Event | None = None) -> None:
    try:
        import watchdog  # noqa: F401

        watch_with_watchdog(cfg, db, stop_event)
    except ImportError:
        logger.warning("Biblioteca 'watchdog' não instalada; usando polling simples.")
        watch_with_polling(cfg, db, stop_event)
