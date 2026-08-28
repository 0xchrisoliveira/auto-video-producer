"""
core.classification
===================
ETAPA 3 - Classificação automática do tema do vídeo.

Duas estratégias, combinadas em `classify_theme_hybrid`:

1. **LLM OpenAI-compatible** — Usa endpoint compatível OpenAI (OpenRouter, Groq, Together, etc.)
   para interpretar o sentido do texto e escolher as categorias mais coerentes entre as
   disponíveis na biblioteca de assets. É a estratégia preferencial quando
   `classification.enabled` está ativo no config.yaml.

2. **Palavras-chave (regra fixa)** — `classify_theme`, usada como
   fallback quando o LLM está desligado, indisponível, ou falha por
   qualquer motivo.

Em ambos os casos, o resultado final é sempre restrito às categorias
que existem de fato na biblioteca de assets (`available_categories`),
para nunca escolher uma categoria vazia nem ficar restrito só às
`default_categories` do config quando a biblioteca tem mais opções.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from core.config import Config


@dataclass
class ClassificationResult:
    theme: Optional[str]
    categories: list[str]
    scores: dict[str, int]
    method: str = "keywords"  # "llm" ou "keywords" ou "sem_texto", útil para logs/relatórios


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c))


def _normalize(text: str) -> str:
    return _strip_accents(text.lower())


def find_text_for_audio(cfg: Config, audio_path: Path) -> Optional[str]:
    """Procura um arquivo de texto correspondente em input/textos/
    com o mesmo nome-base do áudio (ex.: video001.wav -> video001.txt)."""
    textos_dir = cfg.paths["input_textos"]
    candidate = textos_dir / f"{audio_path.stem}.txt"
    if candidate.exists():
        return candidate.read_text(encoding="utf-8")
    return None


def _filter_to_available(categories: list[str], available_categories: Optional[list[str]]) -> list[str]:
    """Restringe uma lista de categorias às que realmente existem na
    biblioteca de assets, preservando a ordem de preferência. Se nada
    sobrar (ex.: biblioteca não tem nenhuma das categorias sugeridas),
    devolve todas as categorias disponíveis em vez de uma lista vazia,
    para a seleção de vídeo nunca falhar por falta de opções."""
    if not available_categories:
        return categories
    available_set = set(available_categories)
    filtered = [c for c in categories if c in available_set]
    if filtered:
        return filtered
    logger.info(
        "Nenhuma das categorias sugeridas ({}) existe na biblioteca; usando todas as "
        "categorias disponíveis ({}).", categories, available_categories,
    )
    return list(available_categories)


def _build_classification_prompt(text: str, available_categories: list[str], themes_cfg: dict) -> str:
    """Constrói o prompt para o LLM classificar o tema."""
    themes_desc = []
    for theme, data in themes_cfg.items():
        keywords = data.get("keywords", [])
        categories = data.get("categories", [])
        themes_desc.append(f"- {theme}: keywords={keywords}, categories={categories}")

    themes_str = "\n".join(themes_desc)
    categories_str = ", ".join(available_categories)

    return f"""Você é um classificador de temas para produção de vídeos.
Analise o texto abaixo e escolha o tema mais adequado entre as opções.

TEMAS DISPONÍVEIS:
{themes_str}

CATEGORIAS DE ASSETS DISPONÍVEIS (escolha apenas destas):
{categories_str}

TEXTO PARA CLASSIFICAR:
{text}

RESPONDA APENAS EM JSON VÁLIDO:
{{
  "theme": "nome_do_tema",
  "categories": ["categoria1", "categoria2", "categoria3"],
  "confidence": 0.95
}}"""


async def _classify_with_llm_async(cfg: Config, text: str, available_categories: list[str]) -> ClassificationResult:
    """Classificação assíncrona via OpenAI-compatible API."""
    class_cfg = cfg.get("classification", default={})
    endpoint = class_cfg.get("endpoint", "https://openrouter.ai/api/v1/chat/completions")
    api_key = class_cfg.get("api_key", "")
    model = class_cfg.get("model", "anthropic/claude-3.5-sonnet")
    timeout = class_cfg.get("timeout_seconds", 30)
    themes_cfg = class_cfg.get("themes", {})

    if not api_key or api_key == "SUA_API_KEY_AQUI":
        raise TTSEngineError("API key não configurada em classification.api_key")

    prompt = _build_classification_prompt(text, available_categories, themes_cfg)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Você é um classificador preciso. Responda apenas JSON válido."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 500,
        "response_format": {"type": "json_object"},
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(endpoint, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

    # Extrai resposta do formato OpenAI
    content = data["choices"][0]["message"]["content"]
    result = json.loads(content)

    theme = result.get("theme")
    categories = result.get("categories", [])
    confidence = result.get("confidence", 0.0)

    logger.info(f"Classificação LLM: theme={theme}, categories={categories}, confidence={confidence}")

    return ClassificationResult(
        theme=theme,
        categories=categories,
        scores={theme: int(confidence * 100)} if theme else {},
        method="llm"
    )


def classify_with_llm(cfg: Config, text: str, available_categories: list[str]) -> ClassificationResult:
    """Wrapper síncrono para classificação via LLM."""
    try:
        return asyncio.run(_classify_with_llm_async(cfg, text, available_categories))
    except Exception as e:
        logger.warning(f"Classificação via LLM falhou: {e}")
        raise


def classify_theme(cfg: Config, text: Optional[str]) -> ClassificationResult:
    """Classificação por correspondência de palavras-chave (regra fixa).
    Usada como fallback de `classify_theme_hybrid`."""
    themes_cfg: dict = cfg.get("classification", "themes", default={})
    default_categories: list[str] = cfg.get(
        "classification", "default_categories", default=["natureza", "cidade", "pessoas"]
    )

    if not text:
        logger.info("Nenhum texto disponível para classificação; usando categorias padrão.")
        return ClassificationResult(theme=None, categories=default_categories, scores={}, method="keywords")

    normalized_text = _normalize(text)
    scores: dict[str, int] = {}

    for theme, theme_data in themes_cfg.items():
        keywords = theme_data.get("keywords", [])
        count = 0
        for kw in keywords:
            kw_norm = _normalize(kw)
            count += len(re.findall(re.escape(kw_norm), normalized_text))
        if count:
            scores[theme] = count

    if not scores:
        logger.info("Nenhum tema identificado no texto; usando categorias padrão.")
        return ClassificationResult(theme=None, categories=default_categories, scores={}, method="keywords")

    best_theme = max(scores, key=scores.get)
    categories = themes_cfg[best_theme].get("categories", default_categories)
    logger.info("Tema identificado (palavras-chave): '{}' (scores={}) -> categorias {}",
                best_theme, scores, categories)
    return ClassificationResult(theme=best_theme, categories=categories, scores=scores, method="keywords")


def classify_theme_hybrid(
    cfg: Config,
    text: Optional[str],
    available_categories: Optional[list[str]] = None,
) -> ClassificationResult:
    """Ponto de entrada usado pelo pipeline (ETAPA 3). Tenta a
    classificação via LLM OpenAI-compatible quando habilitada; se falhar
    ou estiver desabilitada, cai para a classificação por
    palavras-chave. Em ambos os casos, o resultado é restrito às
    categorias que realmente existem na biblioteca de assets."""
    enabled = cfg.get("classification", "enabled", default=True)

    if not text or not text.strip():
        # sem nenhum texto para analisar: usa toda a biblioteca disponível
        logger.info("Nenhum texto disponível para classificar; sorteando entre todas as categorias da biblioteca.")
        fallback_categories = available_categories or cfg.get(
            "classification", "default_categories", default=["natureza", "cidade", "pessoas"]
        )
        return ClassificationResult(theme=None, categories=fallback_categories, scores={}, method="sem_texto")

    if enabled:
        try:
            llm_result = classify_with_llm(cfg, text, available_categories or [])
            categories = _filter_to_available(llm_result.categories, available_categories)
            return ClassificationResult(
                theme=llm_result.theme, categories=categories, scores=llm_result.scores, method="llm"
            )
        except Exception as e:  # noqa: BLE001 - qualquer falha do LLM cai para o fallback
            logger.warning("Classificação via LLM falhou, usando fallback por palavras-chave: {}", e)

    result = classify_theme(cfg, text)
    result.categories = _filter_to_available(result.categories, available_categories)
    return result


# Exceção local para não depender de tts_engine
class TTSEngineError(Exception):
    pass


import asyncio