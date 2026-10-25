"""
core.config
============
Carrega e disponibiliza o config.yaml em toda a aplicação, resolvendo
caminhos relativos em relação à raiz do projeto.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

# ${VAR} -> valor da variável de ambiente. Só este formato é expandido
# (não expandimos $VAR solto) para não reescrever textos comuns por engano.
_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Chaves que costumam conter segredos (usadas no aviso de permissão de arquivo).
_SECRET_KEY_HINTS = ("api_key", "apikey", "secret", "token", "password", "passwd")


class Config:
    """Wrapper simples em torno do dicionário carregado do YAML."""

    def __init__(self, config_path: str | Path = "config.yaml"):
        self.config_path = Path(config_path).resolve()
        self.project_root = self.config_path.parent
        with open(self.config_path, "r", encoding="utf-8") as f:
            self._data: dict[str, Any] = yaml.safe_load(f)

        if not isinstance(self._data, dict):
            raise ValueError(f"{self.config_path} não contém um mapa YAML válido.")
        self._missing_env: set[str] = set()
        self._expand_env(self._data)
        if self._missing_env:
            logger.warning(
                "config.yaml referencia variável(s) de ambiente não definida(s): "
                "{}. Mantendo o texto literal até a variável ser exportada.",
                ", ".join(sorted(self._missing_env)),
            )
        self._warn_if_secrets_world_readable()
        self._resolve_paths()

    # ------------------------------------------------------------------
    # Segredos: ${VAR} e permissão de arquivo
    # ------------------------------------------------------------------
    def _expand_env(self, node: Any) -> Any:
        """Substitui `${VAR}` por variáveis de ambiente em todos os valores
        de texto, permitindo manter chaves de API fora do config.yaml:
        `api_key: "${OPENROUTER_API_KEY}"`."""
        if isinstance(node, dict):
            for key, value in node.items():
                node[key] = self._expand_env(value)
            return node
        if isinstance(node, list):
            for i, value in enumerate(node):
                node[i] = self._expand_env(value)
            return node
        if isinstance(node, str):
            missing = self._missing_env

            def _sub(match: re.Match) -> str:
                name = match.group(1)
                value = os.environ.get(name)
                if value is None:
                    missing.add(name)
                    return match.group(0)
                return value

            return _ENV_VAR_RE.sub(_sub, node)
        return node

    def _warn_if_secrets_world_readable(self) -> None:
        """Avisa se o config.yaml contém segredo real e está legível por
        outros usuários do sistema (POSIX)."""
        if os.name != "posix" or not self.config_path.exists():
            return
        try:
            mode = self.config_path.stat().st_mode & 0o777
        except OSError:
            return
        if mode & 0o077 == 0:
            return

        has_secret = False
        for key, value in self._iter_values(self._data):
            # chaves de arquivo/caminho (client_secrets.json, token.json...)
            # não são segredos em si
            if key.lower().endswith(("_file", "_path", "_dir", "_directory")):
                continue
            if not isinstance(value, str):
                continue
            low = value.strip()
            if not low or low in {"SUA_API_KEY_AQUI", "SUA_CHAVE"}:
                continue
            # placeholder de variável de ambiente não é segredo em si
            if "${" in low:
                continue
            # valor plausivelmente real de segredo (não é caminho nem URL)
            if "/" not in low and len(low) >= 16 and " " not in low:
                has_secret = True
                break
        if has_secret:
            logger.warning(
                "O arquivo {} tem permissão {:04o} e contém valor de segredo. "
                "Restrinja com: chmod 600 {}",
                self.config_path.name, mode, self.config_path,
            )

    def _iter_values(self, node: Any, key: str = ""):
        """Percorre o YAML e rende (chave, valor) apenas das folhas cuja
        chave parece conter segredo (api_key, token, password...)."""
        if isinstance(node, dict):
            for child_key, value in node.items():
                yield from self._iter_values(value, str(child_key))
        elif isinstance(node, list):
            for value in node:
                yield from self._iter_values(value, key)
        else:
            if any(h in key.lower() for h in _SECRET_KEY_HINTS):
                yield key, node

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

    def get_float(self, *keys: str, default: float = 0.0,
                  aliases: dict[str, float] | None = None) -> float:
        """`get()` que devolve sempre um número, aceitando aliases de texto
        (ex.: intensity: "subtle" -> 0.5) e caindo para `default` quando o
        valor não é interpretável — um YAML mal preenchido não pode derrubar
        um render no meio."""
        return coerce_float(self.get(*keys, default=default), default, aliases=aliases)


def coerce_float(value: Any, default: float = 0.0,
                 aliases: dict[str, float] | None = None) -> float:
    """Converte valor de config em float de forma defensiva."""
    if isinstance(value, bool):
        return float(default)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if aliases:
            for alias, number in aliases.items():
                if text == alias:
                    return float(number)
        try:
            return float(text)
        except ValueError:
            return float(default)
    return float(default)


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
