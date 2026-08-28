"""
core.config
============
Carrega e disponibiliza o config.yaml em toda a aplicação, resolvendo
caminhos relativos em relação à raiz do projeto.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


class Config:
    """Wrapper simples em torno do dicionário carregado do YAML."""

    def __init__(self, config_path: str | Path = "config.yaml"):
        self.config_path = Path(config_path).resolve()
        self.project_root = self.config_path.parent
        with open(self.config_path, "r", encoding="utf-8") as f:
            self._data: dict[str, Any] = yaml.safe_load(f)

        self._resolve_paths()

    def _resolve_paths(self) -> None:
        """Converte os caminhos declarados em `paths` para Path absolutos
        e garante que os diretórios existam."""
        paths_cfg = self._data.get("paths", {})
        resolved: dict[str, Path] = {}
        for key, rel in paths_cfg.items():
            if key == "root":
                continue
            p = (self.project_root / rel).resolve()
            resolved[key] = p

        # Cria diretórios de trabalho (não cria assets/videos/* subpastas
        # de categoria pois essas já devem existir com conteúdo do usuário)
        for key in (
            "input_audio",
            "input_textos",
            "input_thumbs",
            "processed_audio",
            "cache",
            "output_videos",
            "output_shorts",
            "logs",
        ):
            resolved[key].mkdir(parents=True, exist_ok=True)

        resolved["database"].parent.mkdir(parents=True, exist_ok=True)

        self.paths = resolved

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, *keys: str, default: Any = None) -> Any:
        """Acesso encadeado seguro: cfg.get('video', 'standard_width')"""
        node: Any = self._data
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    @property
    def raw(self) -> dict:
        return self._data


_CONFIG_SINGLETON: Config | None = None


def load_config(config_path: str | Path = "config.yaml") -> Config:
    global _CONFIG_SINGLETON
    if _CONFIG_SINGLETON is None:
        _CONFIG_SINGLETON = Config(config_path)
    return _CONFIG_SINGLETON


def get_config() -> Config:
    if _CONFIG_SINGLETON is None:
        raise RuntimeError(
            "Config ainda não foi carregada. Chame load_config() primeiro."
        )
    return _CONFIG_SINGLETON
