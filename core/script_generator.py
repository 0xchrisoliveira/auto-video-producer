"""
core.script_generator
=======================
Implementa o comando `python main.py generate "<tópico>"`: usa a seção
`llm.*` do config (endpoint compatível OpenAI) para escrever um roteiro
de narração e grava em `input/textos/` (ou `llm.output_dir`), pronto para
`python main.py run-text <arquivo>`.

O texto gerado nunca é executado/interpretado: é só um arquivo de
roteiro. A chave de API pode (e deve) vir de variável de ambiente via
`${MINHA_CHAVE}` no config.yaml.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from core.config import Config

DEFAULT_SYSTEM_PROMPT = (
    "Você é redator de roteiros para vídeos curtos de narração em áudio.\n"
    "Escreva em parágrafos corridos, prontos para serem lidos em voz alta:\n"
    "- sem roteiro em cena, sem markdown, sem listas, sem títulos;\n"
    "- frases claras e fluidas, com gancho nas duas primeiras linhas;\n"
    "- apenas o texto do roteiro na resposta."
)


class ScriptGenerationError(RuntimeError):
    pass


def _require_llm_config(cfg: Config) -> dict:
    llm_cfg = cfg.get("llm", default={}) or {}
    endpoint = str(llm_cfg.get("endpoint", "")).strip()
    api_key = str(llm_cfg.get("api_key", "")).strip()
    model = str(llm_cfg.get("model", "")).strip()

    if not endpoint:
        raise ScriptGenerationError("llm.endpoint não configurado no config.yaml.")
    scheme = endpoint.split(":", 1)[0].lower()
    if scheme not in {"http", "https"}:
        raise ScriptGenerationError(f"llm.endpoint inválido: {endpoint!r}")
    if not api_key or api_key == "SUA_API_KEY_AQUI" or "${" in api_key:
        raise ScriptGenerationError(
            "llm.api_key não configurada. Use api_key: \"${SUA_VARIAVEL}\" no "
            "config.yaml e exporte a variável no ambiente."
        )
    if not model:
        raise ScriptGenerationError("llm.model não configurado no config.yaml.")
    return llm_cfg


def generate_script(cfg: Config, topic: str,
                    output_path: Optional[Path] = None) -> Path:
    """Gera um roteiro de narração para `topic` e grava em .txt."""
    topic = topic.strip()
    if not topic:
        raise ScriptGenerationError("Tópico vazio: informe o assunto do roteiro.")

    llm_cfg = _require_llm_config(cfg)
    endpoint = str(llm_cfg["endpoint"])
    model = str(llm_cfg["model"])
    api_key = str(llm_cfg["api_key"])
    temperature = llm_cfg.get("temperature", 0.8)
    max_tokens = int(llm_cfg.get("max_tokens", 2000) or 2000)
    timeout = float(llm_cfg.get("timeout_seconds", 60) or 60)
    system_prompt = str(llm_cfg.get("system_prompt", "") or DEFAULT_SYSTEM_PROMPT)
    language = str(llm_cfg.get("language", "") or "").strip()
    target_seconds = llm_cfg.get("target_seconds", 0)

    user_prompt = f"Tópico do vídeo: {topic}"
    if language:
        user_prompt += f"\nIdioma do roteiro: {language}"
    if target_seconds:
        try:
            seconds = float(target_seconds)
        except (TypeError, ValueError):
            seconds = 0.0
        if seconds > 0:
            user_prompt += (
                f"\nDuração alvo da narração: ~{seconds:.0f} segundos "
                f"(~{int(seconds / 60 * 130)} palavras)"
            )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": float(temperature),
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    logger.info("Gerando roteiro para '{}' via {}...", topic, model)
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as e:
        raise ScriptGenerationError(f"Falha ao chamar {endpoint}: {e}") from e
    except json.JSONDecodeError as e:
        raise ScriptGenerationError("Resposta da API não é JSON.") from e

    try:
        content = str(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as e:
        raise ScriptGenerationError(f"Resposta inesperada da API: {e}") from e

    script = content.strip()
    if not script:
        raise ScriptGenerationError("A API devolveu um roteiro vazio.")

    if output_path is None:
        out_dir = cfg.paths["input_textos"] if not llm_cfg.get("output_dir") \
            else (cfg.project_root / str(llm_cfg["output_dir"])).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^0-9a-zA-ZÀ-ÿ_-]+", "_", topic.lower())[:48].strip("_") or "roteiro"
        output_path = out_dir / f"{slug}.txt"
        # não sobrescrever um roteiro já existente
        if output_path.exists():
            for i in range(1, 1000):
                candidate = output_path.with_name(f"{output_path.stem}{i:03d}.txt")
                if not candidate.exists():
                    output_path = candidate
                    break

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(script + "\n", encoding="utf-8")
    logger.success("Roteiro gerado em {} ({} palavras)", output_path,
                   len(script.split()))
    return output_path
