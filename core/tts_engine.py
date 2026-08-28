"""
core.tts_engine
===============
Text-to-Speech engine supporting OmniVoice (local GPU) and Edge TTS (cloud fallback).
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

import torch
from loguru import logger

from core.config import Config


class TTSEngineError(Exception):
    """Exceção para erros do motor TTS."""
    pass


class OmniVoiceEngine:
    """OmniVoice TTS local (GPU/CPU)."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        tts_cfg = cfg.get("tts", "omnivoice", default={})
        self.model_path = tts_cfg.get("model_path", "")
        self.voice_preset = tts_cfg.get("voice_preset", "default")
        self.speed = tts_cfg.get("speed", 1.0)
        self.temperature = tts_cfg.get("temperature", 0.7)
        self.top_p = tts_cfg.get("top_p", 0.9)
        self.device = self._resolve_device(tts_cfg.get("device", "auto"))
        self.compile = tts_cfg.get("compile", True)
        self._model = None
        self._voice_embedding = None

    def _resolve_device(self, device_str: str) -> str:
        if device_str == "auto":
            return "cuda" if torch.cuda.is_available() else "cpu"
        return device_str

    def _load_model(self):
        """Carrega modelo OmniVoice sob demanda."""
        if self._model is not None:
            return

        try:
            from omnivoice import OmniVoice
        except ImportError as e:
            raise TTSEngineError(
                "OmniVoice não instalado. Rode: pip install omnivoice"
            ) from e

        logger.info(f"Carregando OmniVoice em {self.device}...")
        self._model = OmniVoice(device=self.device)

        if self.compile and self.device == "cuda":
            try:
                self._model.model = torch.compile(self._model.model)
                logger.info("torch.compile ativado para OmniVoice")
            except Exception as e:
                logger.warning(f"torch.compile falhou, continuando sem: {e}")

        # Carrega voice preset/embedding
        if self.voice_preset and self.voice_preset != "default":
            preset_path = Path(self.voice_preset)
            if preset_path.exists():
                self._voice_embedding = self._model.load_voice(preset_path)
                logger.info(f"Voice preset carregado: {preset_path}")
            else:
                logger.warning(f"Voice preset não encontrado: {preset_path}, usando default")

    def synthesize(self, text: str, output_path: Path) -> Path:
        """Sintetiza texto para áudio WAV."""
        self._load_model()

        logger.info(f"Gerando áudio com OmniVoice ({len(text)} chars)...")

        # OmniVoice espera texto limpo
        text = text.strip()
        if not text:
            raise TTSEngineError("Texto vazio para síntese")

        # Gera áudio
        audio = self._model.generate(
            text=text,
            voice_embedding=self._voice_embedding,
            speed=self.speed,
            temperature=self.temperature,
            top_p=self.top_p,
        )

        # Salva como WAV
        import soundfile as sf
        sf.write(str(output_path), audio, samplerate=24000, subtype="PCM_16")

        logger.success(f"Áudio gerado: {output_path}")
        return output_path


class EdgeTTSEngine:
    """Edge TTS (Microsoft) - fallback cloud gratuito."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        tts_cfg = cfg.get("tts", "edge", default={})
        self.voice = tts_cfg.get("voice", "pt-BR-AntonioNeural")
        self.rate = tts_cfg.get("rate", "+0%")
        self.volume = tts_cfg.get("volume", "+0%")

    def synthesize(self, text: str, output_path: Path) -> Path:
        """Sintetiza usando edge-tts via CLI."""
        import subprocess

        text = text.strip()
        if not text:
            raise TTSEngineError("Texto vazio para síntese")

        logger.info(f"Gerando áudio com Edge TTS ({self.voice})...")

        # edge-tts precisa ser instalado: pip install edge-tts
        cmd = [
            "edge-tts",
            "--voice", self.voice,
            "--rate", self.rate,
            "--volume", self.volume,
            "--text", text,
            "--write-media", str(output_path),
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                raise TTSEngineError(f"edge-tts falhou: {result.stderr}")
        except FileNotFoundError:
            raise TTSEngineError("edge-tts não instalado. Rode: pip install edge-tts")
        except subprocess.TimeoutExpired:
            raise TTSEngineError("edge-tts timeout (>120s)")

        logger.success(f"Áudio gerado: {output_path}")
        return output_path


def create_tts_engine(cfg: Config):
    """Factory para criar engine TTS baseado na configuração."""
    provider = cfg.get("tts", "provider", default="omnivoice")

    if provider == "omnivoice":
        return OmniVoiceEngine(cfg)
    elif provider == "edge":
        return EdgeTTSEngine(cfg)
    else:
        raise TTSEngineError(f"Provedor TTS desconhecido: {provider}")


def synthesize_text_to_audio(cfg: Config, text: str, output_path: Path) -> Path:
    """
    Função principal: sintetiza texto para áudio.
    Tenta provider primário, cai para fallback se falhar.
    """
    primary_provider = cfg.get("tts", "provider", default="omnivoice")
    fallback_provider = "edge" if primary_provider == "omnivoice" else "omnivoice"

    # Tenta provider primário
    try:
        engine = create_tts_engine(cfg)
        return engine.synthesize(text, output_path)
    except Exception as e:
        logger.warning(f"TTS primário ({primary_provider}) falhou: {e}")

        # Tenta fallback
        if fallback_provider:
            logger.info(f"Tentando fallback: {fallback_provider}")
            # Temporariamente muda provider no config para tentar fallback
            original_provider = cfg._data.get("tts", {}).get("provider")
            cfg._data.setdefault("tts", {})["provider"] = fallback_provider
            try:
                engine = create_tts_engine(cfg)
                return engine.synthesize(text, output_path)
            except Exception as e2:
                logger.error(f"Fallback ({fallback_provider}) também falhou: {e2}")
            finally:
                if original_provider:
                    cfg._data["tts"]["provider"] = original_provider
                else:
                    cfg._data["tts"].pop("provider", None)

        raise TTSEngineError(f"Todos os provedores TTS falharam. Último erro: {e}")