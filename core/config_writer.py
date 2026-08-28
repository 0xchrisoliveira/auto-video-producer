"""
core.config_writer
====================
Edição pontual do config.yaml preservando comentários e formatação
original. Usado pela interface gráfica para salvar preferências do
usuário (resolução, fps, bitrate etc.) sem reescrever o arquivo
inteiro com um dump genérico de YAML (o que perderia comentários).

Só é seguro para editar chaves escalares simples de primeiro nível
dentro de uma seção (ex.: video.standard_width). Não deve ser usado
para estruturas aninhadas como `classification.themes`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if re.match(r"^[A-Za-z0-9_./\-]+$", text):
        return text
    # precisa de aspas (contém espaços, acentos, símbolos etc.)
    escaped = text.replace('"', '\\"')
    return f'"{escaped}"'


def update_yaml_value(yaml_path: Path, section: str, key: str, value: Any) -> None:
    """Substitui o valor de `section.key` no arquivo YAML em disco,
    mantendo o resto do arquivo (incluindo comentários) intacto.
    Lança KeyError se a seção/chave não for encontrada.

    Preserva o estilo de terminador de linha original de cada linha
    (CRLF ou LF) para não deixar o arquivo com uma mistura dos dois,
    o que aconteceria se sempre escrevêssemos "\\n" nas linhas editadas
    de um arquivo que usa "\\r\\n" (como é o caso do config.yaml padrão
    do projeto, editado no Windows)."""
    with open(yaml_path, "r", encoding="utf-8", newline="") as f:
        original = f.read()
    lines = original.splitlines(keepends=True)

    section_header_re = re.compile(rf"^{re.escape(section)}:\s*(#.*)?$")
    top_level_re = re.compile(r"^\S")
    key_re = re.compile(rf"^(\s+){re.escape(key)}:(\s*)(.*)$")

    out: list[str] = []
    in_section = False
    replaced = False

    for line in lines:
        # separa o conteúdo da linha do seu terminador original (\r\n, \n ou nenhum)
        content = line.rstrip("\r\n")
        newline = line[len(content):]

        if not in_section:
            out.append(line)
            if section_header_re.match(content):
                in_section = True
            continue

        if top_level_re.match(content) and content.strip() != "":
            in_section = False
            out.append(line)
            continue

        match = key_re.match(content)
        if match and not replaced:
            indent, _, rest = match.groups()
            comment = ""
            if "#" in rest:
                idx = rest.index("#")
                comment = "  " + rest[idx:]
            out.append(f"{indent}{key}: {_format_value(value)}{comment}{newline}")
            replaced = True
            continue

        out.append(line)

    if not replaced:
        raise KeyError(f"Chave '{section}.{key}' não encontrada em {yaml_path}")

    yaml_path.write_text("".join(out), encoding="utf-8", newline="")


def update_yaml_values(yaml_path: Path, updates: list[tuple[str, str, Any]]) -> None:
    """Aplica várias atualizações de uma vez (section, key, value)."""
    for section, key, value in updates:
        update_yaml_value(yaml_path, section, key, value)
