#!/usr/bin/env python3
"""
gui.py
=======
Interface gráfica (whitelabel) do pipeline Auto Video Producer.
Envolve as mesmas funções usadas pela CLI (main.py) em uma janela
Tkinter, para quem prefere não usar o terminal.

A marca exibida na janela (nome, slogan e ícone) vem da seção `brand`
do config.yaml, então o mesmo painel serve para qualquer canal/tema.

Visual inspirado em Material Design: barra de navegação lateral,
cartões com cantos arredondados e elevação, paleta de cores e
tipografia consistentes.

Uso:
    python gui.py
    python gui.py --config caminho/outro_config.yaml
"""

from __future__ import annotations

import argparse
import platform
import queue
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from loguru import logger

from core.config import Config
from core.config_writer import update_yaml_values
from core.database import Database
from core.logger_setup import setup_logging
from core.pipeline import run_pipeline_for_audio
from core.watcher import start_watching

# Marca padrão; sobrescrita pela seção `brand` do config.yaml quando existir.
DEFAULT_BRAND_NAME = "Auto Video Producer"
DEFAULT_BRAND_TAGLINE = "Painel de controle do pipeline"
DEFAULT_BRAND_ICON = "🎬"

RESOLUTION_PRESETS = {
    "vertical": ("Vertical — YouTube Shorts / Reels / TikTok (1080x1920)", 1080, 1920),
    "horizontal": ("Horizontal — YouTube tradicional (1920x1080)", 1920, 1080),
    "quadrado": ("Quadrado — Instagram feed (1080x1080)", 1080, 1080),
}


# ======================================================================
# Paleta de cores (Material Design 3 — variação lilás/violeta)
# ======================================================================
class Colors:
    BG = "#F4F2FB"              # fundo geral da janela
    SURFACE = "#FFFFFF"         # fundo dos cartões
    SURFACE_ALT = "#F7F6FC"     # fundo de áreas internas suaves

    PRIMARY = "#4F46E5"         # indigo — ação principal
    PRIMARY_DARK = "#352F8C"    # indigo escuro — app bar / nav rail
    PRIMARY_DARKER = "#2A2570"  # indigo mais escuro — hover na nav rail
    PRIMARY_LIGHT = "#ECEAFB"   # indigo bem claro — hovers/realces em cards

    ACCENT = "#14B8A6"          # teal — ações de "executar"
    ACCENT_DARK = "#0D9488"

    TEXT_PRIMARY = "#211D3B"
    TEXT_SECONDARY = "#6C6890"
    TEXT_ON_PRIMARY = "#FFFFFF"
    TEXT_ON_PRIMARY_MUTED = "#C9C5F2"

    BORDER = "#E4E1F3"
    ERROR = "#DC2626"
    WARNING = "#D97706"
    SUCCESS = "#16A34A"

    CONSOLE_BG = "#1E1B33"
    CONSOLE_TEXT = "#D9D6EF"
    CONSOLE_MUTED = "#8B85B8"


class Fonts:
    """Fontes criadas após a existência de uma janela raiz."""

    def __init__(self) -> None:
        family = self._pick_family()
        mono = self._pick_mono_family()
        self.app_title = tkfont.Font(family=family, size=14, weight="bold")
        self.page_title = tkfont.Font(family=family, size=16, weight="bold")
        self.section_title = tkfont.Font(family=family, size=11, weight="bold")
        self.body = tkfont.Font(family=family, size=10)
        self.body_bold = tkfont.Font(family=family, size=10, weight="bold")
        self.small = tkfont.Font(family=family, size=9)
        self.nav_item = tkfont.Font(family=family, size=10, weight="bold")
        self.chip = tkfont.Font(family=family, size=9, weight="bold")
        self.mono = tkfont.Font(family=mono, size=9)

    @staticmethod
    def _pick_family() -> str:
        preferred = ["Segoe UI", "Helvetica Neue", "Ubuntu", "Arial"]
        available = set(tkfont.families())
        for name in preferred:
            if name in available:
                return name
        return "TkDefaultFont"

    @staticmethod
    def _pick_mono_family() -> str:
        preferred = ["Cascadia Mono", "Consolas", "Ubuntu Mono", "Menlo", "Courier New"]
        available = set(tkfont.families())
        for name in preferred:
            if name in available:
                return name
        return "TkFixedFont"


def configure_style(style: ttk.Style, fonts: Fonts) -> None:
    """Configura o tema ttk 'clam' com a paleta Material Design."""
    style.theme_use("clam")

    style.configure(".", background=Colors.BG, foreground=Colors.TEXT_PRIMARY,
                     font=fonts.body, borderwidth=0)

    # Labels ------------------------------------------------------------
    style.configure("TLabel", background=Colors.BG, foreground=Colors.TEXT_PRIMARY)
    style.configure("Card.TLabel", background=Colors.SURFACE, foreground=Colors.TEXT_PRIMARY)
    style.configure("CardMuted.TLabel", background=Colors.SURFACE, foreground=Colors.TEXT_SECONDARY)
    style.configure("CardTitle.TLabel", background=Colors.SURFACE, foreground=Colors.TEXT_PRIMARY,
                     font=fonts.section_title)
    style.configure("PageTitle.TLabel", background=Colors.BG, foreground=Colors.TEXT_PRIMARY,
                     font=fonts.page_title)
    style.configure("PageSubtitle.TLabel", background=Colors.BG, foreground=Colors.TEXT_SECONDARY)
    style.configure("StatusChip.TLabel", background=Colors.PRIMARY_DARKER,
                     foreground=Colors.TEXT_ON_PRIMARY, font=fonts.chip, padding=(10, 5))

    # Botões --------------------------------------------------------------
    style.configure("TButton", background=Colors.SURFACE_ALT, foreground=Colors.TEXT_PRIMARY,
                     padding=(14, 8), relief="flat", font=fonts.body_bold, borderwidth=0)
    style.map("TButton",
              background=[("active", Colors.BORDER), ("disabled", Colors.SURFACE_ALT)],
              foreground=[("disabled", Colors.TEXT_SECONDARY)])

    style.configure("Primary.TButton", background=Colors.PRIMARY, foreground=Colors.TEXT_ON_PRIMARY,
                     padding=(18, 10), font=fonts.body_bold)
    style.map("Primary.TButton",
              background=[("active", Colors.PRIMARY_DARK), ("disabled", "#B7B3E8")],
              foreground=[("disabled", "#F1F0FB")])

    style.configure("Accent.TButton", background=Colors.ACCENT, foreground=Colors.TEXT_ON_PRIMARY,
                     padding=(18, 10), font=fonts.body_bold)
    style.map("Accent.TButton",
              background=[("active", Colors.ACCENT_DARK), ("disabled", "#A7E3DA")])

    style.configure("Outline.TButton", background=Colors.SURFACE, foreground=Colors.PRIMARY,
                     padding=(12, 7), font=fonts.body_bold)
    style.map("Outline.TButton", background=[("active", Colors.PRIMARY_LIGHT)])

    style.configure("Ghost.TButton", background=Colors.PRIMARY_DARK, foreground=Colors.TEXT_ON_PRIMARY,
                     padding=(12, 7), font=fonts.body)
    style.map("Ghost.TButton", background=[("active", Colors.PRIMARY_DARKER)])

    # Entradas ------------------------------------------------------------
    style.configure("TEntry", fieldbackground=Colors.SURFACE_ALT, foreground=Colors.TEXT_PRIMARY,
                     bordercolor=Colors.BORDER, lightcolor=Colors.BORDER, darkcolor=Colors.BORDER,
                     borderwidth=1, relief="solid", padding=8)
    style.map("TEntry", bordercolor=[("focus", Colors.PRIMARY)])

    style.configure("TCombobox", fieldbackground=Colors.SURFACE_ALT, background=Colors.SURFACE_ALT,
                     foreground=Colors.TEXT_PRIMARY, bordercolor=Colors.BORDER, arrowcolor=Colors.PRIMARY,
                     padding=6)
    style.map("TCombobox", fieldbackground=[("readonly", Colors.SURFACE_ALT)])

    style.configure("TRadiobutton", background=Colors.SURFACE, foreground=Colors.TEXT_PRIMARY,
                     font=fonts.body)
    style.map("TRadiobutton", background=[("active", Colors.SURFACE)])

    style.configure("TCheckbutton", background=Colors.SURFACE, foreground=Colors.TEXT_PRIMARY)

    style.configure("TSeparator", background=Colors.BORDER)

    # Progressbar -----------------------------------------------------------
    style.configure("TProgressbar", background=Colors.ACCENT, troughcolor=Colors.SURFACE_ALT,
                     bordercolor=Colors.SURFACE_ALT, lightcolor=Colors.ACCENT, darkcolor=Colors.ACCENT,
                     thickness=6)

    # Treeview ----------------------------------------------------------------
    style.configure("Treeview", background=Colors.SURFACE, fieldbackground=Colors.SURFACE,
                     foreground=Colors.TEXT_PRIMARY, rowheight=28, borderwidth=0, font=fonts.body)
    style.configure("Treeview.Heading", background=Colors.PRIMARY_LIGHT, foreground=Colors.PRIMARY_DARK,
                     font=fonts.body_bold, padding=(8, 8), relief="flat")
    style.map("Treeview.Heading", background=[("active", Colors.PRIMARY_LIGHT)])
    style.map("Treeview",
              background=[("selected", Colors.PRIMARY)],
              foreground=[("selected", Colors.TEXT_ON_PRIMARY)])

    # Notebook (não usado como navegação principal, mas deixado consistente)
    style.configure("TNotebook", background=Colors.BG, borderwidth=0)
    style.configure("TNotebook.Tab", background=Colors.SURFACE_ALT, foreground=Colors.TEXT_SECONDARY,
                     padding=(14, 8), font=fonts.body_bold)
    style.map("TNotebook.Tab",
              background=[("selected", Colors.SURFACE)],
              foreground=[("selected", Colors.PRIMARY)])


# ======================================================================
# Cartão com cantos arredondados (emula elevação do Material Design)
# ======================================================================
class RoundedCard(tk.Frame):
    """Cartão com fundo arredondado desenhado em Canvas. Os widgets de
    conteúdo devem ser filhos de `self.body` (um tk.Frame comum)."""

    def __init__(self, parent, page_bg=Colors.BG, card_bg=Colors.SURFACE,
                 radius=16, pad=18, min_height=0):
        super().__init__(parent, bg=page_bg)
        self._page_bg = page_bg
        self._card_bg = card_bg
        self._radius = radius
        self._pad = pad
        self._min_height = min_height

        self.canvas = tk.Canvas(self, bg=page_bg, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self.body = tk.Frame(self.canvas, bg=card_bg)
        self._window = self.canvas.create_window(pad, pad, anchor="nw", window=self.body)

        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.body.bind("<Configure>", self._on_body_configure)

    def _on_canvas_configure(self, event) -> None:
        inner_w = max(event.width - 2 * self._pad, 10)
        self.canvas.itemconfig(self._window, width=inner_w)
        self._redraw(event.width, self.canvas.winfo_height())

    def _on_body_configure(self, _event=None) -> None:
        h = max(self.body.winfo_reqheight() + 2 * self._pad, self._min_height)
        w = self.canvas.winfo_width()
        self.canvas.configure(height=h)
        self._redraw(w, h)

    def _redraw(self, w: int, h: int) -> None:
        if w < 4 or h < 4:
            return
        self.canvas.delete("card_bg")
        self._round_rect(1, 1, w - 1, h - 1, self._radius, fill=self._card_bg,
                          outline=self._card_bg, width=1, tags="card_bg")
        self.canvas.tag_lower("card_bg")

    def _round_rect(self, x1, y1, x2, y2, r, **kwargs):
        r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.canvas.create_polygon(points, smooth=True, **kwargs)


def section_header(parent, fonts: Fonts, icon: str, title: str,
                    grid_row: int | None = None, columnspan: int = 1) -> None:
    """Pequeno cabeçalho de seção dentro de um card (ícone + título).

    Se `grid_row` for informado, o cabeçalho é posicionado com `grid`
    (para conviver com o restante do formulário, que também usa grid).
    Caso contrário, usa `pack` (para corpos de card mais simples)."""
    row = tk.Frame(parent, bg=Colors.SURFACE)
    if grid_row is not None:
        row.grid(row=grid_row, column=0, columnspan=columnspan, sticky="w", pady=(0, 10))
    else:
        row.pack(fill="x", pady=(0, 10))
    tk.Label(row, text=icon, bg=Colors.SURFACE, fg=Colors.PRIMARY,
              font=fonts.section_title).pack(side="left", padx=(0, 8))
    tk.Label(row, text=title, bg=Colors.SURFACE, fg=Colors.TEXT_PRIMARY,
              font=fonts.section_title).pack(side="left")


def page_header(parent, fonts: Fonts, title: str, subtitle: str) -> None:
    """Cabeçalho de página, fora dos cartões (título grande + subtítulo)."""
    ttk.Label(parent, text=title, style="PageTitle.TLabel").pack(anchor="w", pady=(0, 4))
    ttk.Label(parent, text=subtitle, style="PageSubtitle.TLabel",
              wraplength=760, justify="left").pack(anchor="w", pady=(0, 16))


def _open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    system = platform.system()
    try:
        if system == "Windows":
            subprocess.Popen(["explorer", str(path)])
        elif system == "Darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:  # noqa: BLE001
        messagebox.showwarning(
            "Não foi possível abrir a pasta",
            f"Abra manualmente:\n{path}\n\nDetalhes: {e}",
        )


NAV_ITEMS = [
    ("process", "▶", "Processar áudio"),
    ("watch", "🛰", "Monitoramento automático"),
    ("library", "🎬", "Biblioteca de assets"),
    ("config", "⚙", "Configurações"),
    ("history", "🕓", "Histórico"),
]


class App:
    def __init__(self, root: tk.Tk, config_path: Path):
        self.root = root
        self.config_path = config_path
        self.cfg = Config(str(config_path))
        setup_logging(self.cfg)
        self.db = Database(self.cfg)

        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.ui_queue: "queue.Queue[tuple[str, object]]" = queue.Queue()

        self.watch_thread: threading.Thread | None = None
        self.watch_stop_event: threading.Event | None = None
        self.busy = False

        logger.add(self._log_sink, level="INFO", format="{time:HH:mm:ss} | {level: <8} | {message}")

        self.fonts = Fonts()
        self.style = ttk.Style(self.root)
        configure_style(self.style, self.fonts)

        # Marca whitelabel: definida em config.yaml (seção `brand`).
        self.brand_name = str(self.cfg.get("brand", "name", default=DEFAULT_BRAND_NAME))
        self.brand_tagline = str(self.cfg.get("brand", "tagline", default=DEFAULT_BRAND_TAGLINE))
        self.brand_icon = str(self.cfg.get("brand", "icon", default=DEFAULT_BRAND_ICON))

        self.root.title(f"{self.brand_name} — {self.brand_tagline}")
        self.root.configure(bg=Colors.BG)
        self.root.geometry("1080x760")
        self.root.minsize(920, 640)

        self.nav_buttons: dict[str, tk.Button] = {}
        self.tab_frames: dict[str, tk.Frame] = {}
        self.active_tab = "process"

        self._build_layout()
        self._refresh_status_bar()
        self._refresh_library_tab()
        self._refresh_history_tab()
        self._select_tab("process")

        self.root.after(150, self._poll_queues)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Log (thread-safe: o sink do loguru roda em qualquer thread, então
    # só empilha texto numa fila; quem escreve no widget é o loop
    # principal do Tkinter via _poll_queues)
    # ------------------------------------------------------------------
    def _log_sink(self, message) -> None:  # noqa: ANN001
        self.log_queue.put(str(message))

    def _poll_queues(self) -> None:
        try:
            while True:
                text = self.log_queue.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", text)
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass

        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                self._handle_ui_event(kind, payload)
        except queue.Empty:
            pass

        self.root.after(150, self._poll_queues)

    def _handle_ui_event(self, kind: str, payload: object) -> None:
        if kind == "pipeline_done":
            self._set_busy(False)
            messagebox.showinfo("Concluído", f"Vídeo gerado com sucesso:\n{payload}")
            self._refresh_history_tab()
        elif kind == "pipeline_failed":
            self._set_busy(False)
            messagebox.showerror(
                "Falha no processamento",
                "O pipeline não conseguiu gerar o vídeo. Veja o log abaixo para detalhes.",
            )
        elif kind == "pipeline_error":
            self._set_busy(False)
            messagebox.showerror("Erro inesperado", str(payload))
        elif kind == "scan_done":
            self._set_busy(False)
            self._refresh_library_tab()
        elif kind == "watch_stopped":
            self.watch_status_var.set("Monitoramento parado.")
            self.watch_toggle_btn.configure(text="Iniciar monitoramento", state="normal")

    # ------------------------------------------------------------------
    # Layout geral: barra superior + navegação lateral + conteúdo + log
    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        self._build_app_bar()

        middle = tk.Frame(self.root, bg=Colors.BG)
        middle.pack(fill="both", expand=True)

        self._build_nav_rail(middle)

        content_outer = tk.Frame(middle, bg=Colors.BG)
        content_outer.pack(side="left", fill="both", expand=True)

        self.content_container = tk.Frame(content_outer, bg=Colors.BG)
        self.content_container.pack(fill="both", expand=True, padx=24, pady=(20, 10))
        self.content_container.grid_rowconfigure(0, weight=1)
        self.content_container.grid_columnconfigure(0, weight=1)

        self.tab_process = tk.Frame(self.content_container, bg=Colors.BG)
        self.tab_watch = tk.Frame(self.content_container, bg=Colors.BG)
        self.tab_library = tk.Frame(self.content_container, bg=Colors.BG)
        self.tab_config = tk.Frame(self.content_container, bg=Colors.BG)
        self.tab_history = tk.Frame(self.content_container, bg=Colors.BG)

        self.tab_frames = {
            "process": self.tab_process,
            "watch": self.tab_watch,
            "library": self.tab_library,
            "config": self.tab_config,
            "history": self.tab_history,
        }
        for frame in self.tab_frames.values():
            frame.grid(row=0, column=0, sticky="nsew")

        self._build_process_tab()
        self._build_watch_tab()
        self._build_library_tab()
        self._build_config_tab()
        self._build_history_tab()

        self._build_log_panel(content_outer)

    def _build_app_bar(self) -> None:
        bar = tk.Frame(self.root, bg=Colors.PRIMARY_DARK, height=64)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)

        left = tk.Frame(bar, bg=Colors.PRIMARY_DARK)
        left.pack(side="left", padx=22)
        tk.Label(left, text=self.brand_icon, bg=Colors.PRIMARY_DARK, fg=Colors.TEXT_ON_PRIMARY,
                  font=(self.fonts.app_title.actual("family"), 18)).pack(side="left", padx=(0, 10))
        title_col = tk.Frame(left, bg=Colors.PRIMARY_DARK)
        title_col.pack(side="left")
        tk.Label(title_col, text=self.brand_name, bg=Colors.PRIMARY_DARK, fg=Colors.TEXT_ON_PRIMARY,
                  font=self.fonts.app_title).pack(anchor="w")
        tk.Label(title_col, text=self.brand_tagline, bg=Colors.PRIMARY_DARK,
                  fg=Colors.TEXT_ON_PRIMARY_MUTED, font=self.fonts.small).pack(anchor="w")

        right = tk.Frame(bar, bg=Colors.PRIMARY_DARK)
        right.pack(side="right", padx=22)
        ttk.Button(right, text="📂  Abrir pasta de vídeos gerados", style="Ghost.TButton",
                   command=lambda: _open_folder(self.cfg.paths["output_videos"])).pack(side="right")

        self.status_var = tk.StringVar()
        self.status_chip = tk.Label(bar, textvariable=self.status_var, bg=Colors.PRIMARY_DARKER,
                                     fg=Colors.TEXT_ON_PRIMARY, font=self.fonts.chip, padx=12, pady=6)
        self.status_chip.pack(side="right", padx=(0, 16))

        # linha sutil de "elevação" abaixo da app bar
        tk.Frame(self.root, bg=Colors.BORDER, height=1).pack(fill="x")

    def _build_nav_rail(self, parent: tk.Frame) -> None:
        rail = tk.Frame(parent, bg=Colors.PRIMARY_DARK, width=232)
        rail.pack(side="left", fill="y")
        rail.pack_propagate(False)

        tk.Frame(rail, bg=Colors.PRIMARY_DARK, height=18).pack(fill="x")

        for key, icon, label in NAV_ITEMS:
            btn = tk.Button(
                rail, text=f"  {icon}   {label}", anchor="w", bd=0, relief="flat",
                bg=Colors.PRIMARY_DARK, fg=Colors.TEXT_ON_PRIMARY_MUTED,
                activebackground=Colors.PRIMARY_DARKER, activeforeground=Colors.TEXT_ON_PRIMARY,
                font=self.fonts.nav_item, cursor="hand2", padx=14, pady=12,
                highlightthickness=0, justify="left",
                command=lambda k=key: self._select_tab(k),
            )
            btn.pack(fill="x", padx=10, pady=3)
            self.nav_buttons[key] = btn

        spacer = tk.Frame(rail, bg=Colors.PRIMARY_DARK)
        spacer.pack(fill="both", expand=True)

        footer = tk.Label(rail, text=f"{self.brand_name} • Pipeline local", bg=Colors.PRIMARY_DARK,
                           fg=Colors.TEXT_ON_PRIMARY_MUTED, font=self.fonts.small)
        footer.pack(pady=14)

    def _select_tab(self, key: str) -> None:
        self.active_tab = key
        for k, btn in self.nav_buttons.items():
            if k == key:
                btn.configure(bg=Colors.PRIMARY, fg=Colors.TEXT_ON_PRIMARY,
                              activebackground=Colors.PRIMARY)
            else:
                btn.configure(bg=Colors.PRIMARY_DARK, fg=Colors.TEXT_ON_PRIMARY_MUTED,
                              activebackground=Colors.PRIMARY_DARKER)
        self.tab_frames[key].tkraise()

    def _build_log_panel(self, parent: tk.Frame) -> None:
        wrap = tk.Frame(parent, bg=Colors.BG)
        wrap.pack(fill="x", padx=24, pady=(6, 18))

        card = RoundedCard(wrap, page_bg=Colors.BG, card_bg=Colors.CONSOLE_BG, radius=16, pad=14)
        card.pack(fill="x")

        header = tk.Frame(card.body, bg=Colors.CONSOLE_BG)
        header.pack(fill="x", pady=(0, 8))
        tk.Label(header, text="🖥  Log de execução", bg=Colors.CONSOLE_BG, fg=Colors.CONSOLE_TEXT,
                  font=self.fonts.section_title).pack(side="left")
        ttk.Button(header, text="Limpar log", style="Outline.TButton",
                   command=self._clear_log).pack(side="right")

        self.log_text = ScrolledText(
            card.body, height=9, state="disabled", wrap="word", bd=0, relief="flat",
            bg=Colors.CONSOLE_BG, fg=Colors.CONSOLE_TEXT, insertbackground=Colors.CONSOLE_TEXT,
            font=self.fonts.mono, padx=10, pady=8, highlightthickness=0,
        )
        self.log_text.pack(fill="both", expand=True)

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _refresh_status_bar(self) -> None:
        w = self.cfg.get("video", "standard_width", default=1080)
        h = self.cfg.get("video", "standard_height", default=1920)
        orientation = "Vertical (Shorts)" if h > w else ("Quadrado" if h == w else "Horizontal")
        self.status_var.set(f"⚙ {w}×{h} · {orientation}")

    # ------------------------------------------------------------------
    # Aba: Processar áudio
    # ------------------------------------------------------------------
    def _build_process_tab(self) -> None:
        frame = self.tab_process
        page_header(
            frame, self.fonts, "Processar áudio",
            "Selecione uma narração (.wav) e gere o vídeo completo (seleção de fundo, "
            "legendas, overlay, tela final, thumbnail e exportação).",
        )

        card = RoundedCard(frame)
        card.pack(fill="x")
        body = card.body
        body.columnconfigure(1, weight=1)

        section_header(body, self.fonts, "🎙", "Arquivos de entrada", grid_row=0, columnspan=3)

        ttk.Label(body, text="Arquivo de áudio (.wav)", style="Card.TLabel").grid(
            row=1, column=0, sticky="w", pady=(0, 4)
        )
        self.audio_path_var = tk.StringVar()
        ttk.Entry(body, textvariable=self.audio_path_var).grid(
            row=2, column=0, columnspan=2, sticky="we", pady=(0, 4)
        )
        ttk.Button(body, text="Procurar…", style="Outline.TButton",
                   command=self._browse_audio).grid(row=2, column=2, sticky="w", padx=(8, 0))

        ttk.Label(body, text="Texto do tema (opcional, .txt)", style="Card.TLabel").grid(
            row=3, column=0, sticky="w", pady=(16, 4)
        )
        self.texto_path_var = tk.StringVar()
        ttk.Entry(body, textvariable=self.texto_path_var).grid(
            row=4, column=0, columnspan=2, sticky="we"
        )
        ttk.Button(body, text="Procurar…", style="Outline.TButton",
                   command=self._browse_texto).grid(row=4, column=2, sticky="w", padx=(8, 0))
        ttk.Label(
            body,
            text="Se preenchido, é copiado para input/textos/ com o mesmo nome do áudio, "
                 "usado para escolher a categoria de vídeo pelo tema do texto.",
            style="CardMuted.TLabel", wraplength=760, justify="left",
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))

        ttk.Separator(body).grid(row=6, column=0, columnspan=3, sticky="we", pady=18)

        self.process_btn = ttk.Button(body, text="▶  Processar agora", style="Accent.TButton",
                                       command=self._start_pipeline)
        self.process_btn.grid(row=7, column=0, columnspan=3, sticky="w")

        self.process_progress = ttk.Progressbar(body, mode="indeterminate")
        self.process_progress.grid(row=8, column=0, columnspan=3, sticky="we", pady=(14, 0))

    def _browse_audio(self) -> None:
        initial = self.cfg.paths["input_audio"]
        path = filedialog.askopenfilename(
            title="Selecione a narração",
            initialdir=str(initial) if initial.exists() else str(Path.home()),
            filetypes=[("Áudio WAV", "*.wav"), ("Todos os arquivos", "*.*")],
        )
        if path:
            self.audio_path_var.set(path)

    def _browse_texto(self) -> None:
        initial = self.cfg.paths["input_textos"]
        path = filedialog.askopenfilename(
            title="Selecione o texto do roteiro (opcional)",
            initialdir=str(initial) if initial.exists() else str(Path.home()),
            filetypes=[("Texto", "*.txt"), ("Todos os arquivos", "*.*")],
        )
        if path:
            self.texto_path_var.set(path)

    def _start_pipeline(self) -> None:
        if self.busy:
            return
        audio_str = self.audio_path_var.get().strip()
        if not audio_str:
            messagebox.showwarning("Selecione um áudio", "Escolha um arquivo .wav antes de processar.")
            return
        audio_path = Path(audio_str)
        if not audio_path.exists():
            messagebox.showerror("Arquivo não encontrado", f"Não encontrei:\n{audio_path}")
            return

        texto_str = self.texto_path_var.get().strip()
        if texto_str:
            texto_path = Path(texto_str)
            if texto_path.exists():
                destino = self.cfg.paths["input_textos"] / f"{audio_path.stem}.txt"
                try:
                    destino.write_text(texto_path.read_text(encoding="utf-8"), encoding="utf-8")
                except Exception as e:  # noqa: BLE001
                    logger.warning("Não foi possível copiar o texto do tema: {}", e)

        self._set_busy(True, "Processando... isso pode levar alguns minutos.")
        threading.Thread(target=self._run_pipeline_worker, args=(audio_path,), daemon=True).start()

    def _run_pipeline_worker(self, audio_path: Path) -> None:
        try:
            self.db.scan_assets()
            result = run_pipeline_for_audio(self.cfg, self.db, audio_path)
            if result is not None:
                self.ui_queue.put(("pipeline_done", str(result)))
            else:
                self.ui_queue.put(("pipeline_failed", None))
        except Exception as e:  # noqa: BLE001
            logger.exception("Erro inesperado ao processar pelo painel: {}", e)
            self.ui_queue.put(("pipeline_error", str(e)))

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.process_btn.configure(state=state)
        self.scan_btn.configure(state=state)
        if busy:
            self.process_progress.start(12)
        else:
            self.process_progress.stop()

    # ------------------------------------------------------------------
    # Aba: Monitoramento automático
    # ------------------------------------------------------------------
    def _build_watch_tab(self) -> None:
        frame = self.tab_watch
        page_header(
            frame, self.fonts, "Monitoramento automático",
            "Monitora a pasta input/audio/ continuamente: assim que um novo .wav aparece "
            "por lá, o pipeline roda automaticamente, sem precisar clicar em nada.",
        )

        card = RoundedCard(frame)
        card.pack(fill="x")
        body = card.body

        section_header(body, self.fonts, "🛰", "Status do monitoramento")

        ttk.Label(body, text=f"Pasta monitorada: {self.cfg.paths['input_audio']}",
                  style="CardMuted.TLabel", wraplength=760, justify="left").pack(anchor="w")

        ttk.Separator(body).pack(fill="x", pady=16)

        self.watch_toggle_btn = ttk.Button(body, text="▶  Iniciar monitoramento",
                                            style="Primary.TButton", command=self._toggle_watch)
        self.watch_toggle_btn.pack(anchor="w")

        self.watch_status_var = tk.StringVar(value="Monitoramento parado.")
        ttk.Label(body, textvariable=self.watch_status_var, style="CardMuted.TLabel").pack(
            anchor="w", pady=(14, 0)
        )

    def _toggle_watch(self) -> None:
        if self.watch_thread is not None and self.watch_thread.is_alive():
            self.watch_status_var.set("Parando monitoramento (pode levar alguns segundos)...")
            self.watch_toggle_btn.configure(state="disabled")
            self.watch_stop_event.set()
            threading.Thread(target=self._wait_watch_stop, daemon=True).start()
        else:
            self.watch_stop_event = threading.Event()
            self.watch_thread = threading.Thread(
                target=start_watching,
                args=(self.cfg, self.db, self.watch_stop_event),
                daemon=True,
            )
            self.watch_thread.start()
            self.watch_status_var.set("Monitorando... deixe a janela aberta em segundo plano.")
            self.watch_toggle_btn.configure(text="⏸  Parar monitoramento")

    def _wait_watch_stop(self) -> None:
        if self.watch_thread is not None:
            self.watch_thread.join(timeout=30)
        self.ui_queue.put(("watch_stopped", None))

    # ------------------------------------------------------------------
    # Aba: Biblioteca de assets
    # ------------------------------------------------------------------
    def _build_library_tab(self) -> None:
        frame = self.tab_library
        page_header(
            frame, self.fonts, "Biblioteca de assets",
            "Vídeos de fundo (assets/videos/<categoria>/) e músicas ambiente "
            "(assets/musicas/) cadastrados no banco de dados.",
        )

        card = RoundedCard(frame)
        card.pack(fill="x")
        body = card.body

        top = tk.Frame(body, bg=Colors.SURFACE)
        top.pack(fill="x", pady=(0, 4))
        self.scan_btn = ttk.Button(top, text="🔄  Escanear assets agora", style="Primary.TButton",
                                    command=self._start_scan)
        self.scan_btn.pack(side="left")
        ttk.Button(top, text="📂  Abrir pasta de assets", style="Outline.TButton",
                   command=lambda: _open_folder(self.cfg.paths["assets_videos"])).pack(
            side="left", padx=(10, 0)
        )

        self.library_summary_var = tk.StringVar(value="Carregando...")
        ttk.Label(body, textvariable=self.library_summary_var, style="CardMuted.TLabel",
                  justify="left").pack(anchor="w", pady=(14, 10))

        columns = ("categoria", "quantidade")
        self.library_tree = ttk.Treeview(body, columns=columns, show="headings", height=10)
        self.library_tree.heading("categoria", text="Categoria")
        self.library_tree.heading("quantidade", text="Qtd. de vídeos")
        self.library_tree.column("categoria", width=340)
        self.library_tree.column("quantidade", width=160, anchor="center")
        self.library_tree.pack(fill="both", expand=True)

    def _start_scan(self) -> None:
        if self.busy:
            return
        self._set_busy(True)
        threading.Thread(target=self._run_scan_worker, daemon=True).start()

    def _run_scan_worker(self) -> None:
        try:
            self.db.scan_assets()
        except Exception as e:  # noqa: BLE001
            logger.exception("Erro ao escanear assets: {}", e)
        self.ui_queue.put(("scan_done", None))

    def _refresh_library_tab(self) -> None:
        summary = self.db.asset_summary()
        self.library_summary_var.set(
            f"Total de vídeos ativos: {summary['total_videos']}    |    "
            f"Total de músicas ativas: {summary['total_musicas']}"
        )
        for row in self.library_tree.get_children():
            self.library_tree.delete(row)
        for categoria, total in sorted(summary["por_categoria"].items()):
            self.library_tree.insert("", "end", values=(categoria, total))

    # ------------------------------------------------------------------
    # Aba: Configurações
    # ------------------------------------------------------------------
    def _build_config_tab(self) -> None:
        frame = self.tab_config
        page_header(
            frame, self.fonts, "Configurações",
            "Ajuste o formato de exportação, taxa de quadros, bitrate, overlay do canal "
            "e volume da música ambiente.",
        )

        w = self.cfg.get("video", "standard_width", default=1080)
        h = self.cfg.get("video", "standard_height", default=1920)

        current_preset = "custom"
        for key, (_, pw, ph) in RESOLUTION_PRESETS.items():
            if (pw, ph) == (w, h):
                current_preset = key
                break

        card = RoundedCard(frame)
        card.pack(fill="x")
        body = card.body
        body.columnconfigure(1, weight=1)

        section_header(body, self.fonts, "📐", "Formato de exportação do vídeo", grid_row=0, columnspan=3)

        self.resolution_var = tk.StringVar(value=current_preset)
        r = 1
        for key, (label, pw, ph) in RESOLUTION_PRESETS.items():
            ttk.Radiobutton(body, text=label, value=key, variable=self.resolution_var).grid(
                row=r, column=0, columnspan=3, sticky="w", pady=3
            )
            r += 1
        ttk.Radiobutton(body, text="Personalizado (definir largura/altura abaixo)",
                         value="custom", variable=self.resolution_var).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(3, 4)
        )
        r += 1

        dims = tk.Frame(body, bg=Colors.SURFACE)
        dims.grid(row=r, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(dims, text="Largura (px)", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self.width_var = tk.StringVar(value=str(w))
        ttk.Entry(dims, textvariable=self.width_var, width=10).grid(row=1, column=0, sticky="w", pady=(2, 0))
        ttk.Label(dims, text="Altura (px)", style="Card.TLabel").grid(row=0, column=1, sticky="w", padx=(24, 0))
        self.height_var = tk.StringVar(value=str(h))
        ttk.Entry(dims, textvariable=self.height_var, width=10).grid(
            row=1, column=1, sticky="w", padx=(24, 0), pady=(2, 0)
        )
        r += 1

        ttk.Separator(body).grid(row=r, column=0, columnspan=3, sticky="we", pady=20)
        r += 1

        section_header(body, self.fonts, "🎚", "Exportação e áudio", grid_row=r, columnspan=3)
        r += 1

        ttk.Label(body, text="Quadros por segundo (FPS)", style="Card.TLabel").grid(
            row=r, column=0, sticky="w", pady=(0, 2)
        )
        self.fps_var = tk.StringVar(value=str(self.cfg.get("video", "standard_fps", default=30)))
        ttk.Combobox(body, textvariable=self.fps_var, values=["24", "30", "60"], width=8,
                     state="readonly").grid(row=r, column=1, sticky="w")
        r += 1

        ttk.Label(body, text="Bitrate de exportação (ex.: 18M)", style="Card.TLabel").grid(
            row=r, column=0, sticky="w", pady=(14, 2)
        )
        self.bitrate_var = tk.StringVar(value=str(self.cfg.get("video", "export_bitrate", default="18M")))
        ttk.Entry(body, textvariable=self.bitrate_var, width=12).grid(row=r, column=1, sticky="w", pady=(14, 0))
        r += 1

        ttk.Label(body, text="Texto do overlay do canal", style="Card.TLabel").grid(
            row=r, column=0, sticky="w", pady=(14, 2)
        )
        self.overlay_text_var = tk.StringVar(value=str(self.cfg.get("overlay", "text", default=self.brand_name)))
        ttk.Entry(body, textvariable=self.overlay_text_var, width=32).grid(
            row=r, column=1, columnspan=2, sticky="w", pady=(14, 0)
        )
        r += 1

        ttk.Label(body, text="Volume da música ambiente (dB, negativo = mais baixo)",
                  style="Card.TLabel").grid(row=r, column=0, columnspan=2, sticky="w", pady=(14, 2))
        r += 1
        self.music_db_var = tk.StringVar(value=str(self.cfg.get("audio_mix", "music_db", default=-28)))
        ttk.Entry(body, textvariable=self.music_db_var, width=10).grid(row=r, column=0, sticky="w")
        r += 1

        ttk.Separator(body).grid(row=r, column=0, columnspan=3, sticky="we", pady=20)
        r += 1

        ttk.Button(body, text="💾  Salvar configurações", style="Primary.TButton",
                   command=self._save_config).grid(row=r, column=0, columnspan=3, sticky="w")

    def _save_config(self) -> None:
        preset = self.resolution_var.get()
        if preset in RESOLUTION_PRESETS:
            _, w, h = RESOLUTION_PRESETS[preset]
        else:
            try:
                w = int(self.width_var.get())
                h = int(self.height_var.get())
            except ValueError:
                messagebox.showerror("Valor inválido", "Largura e altura devem ser números inteiros.")
                return

        try:
            fps = int(self.fps_var.get())
        except ValueError:
            messagebox.showerror("Valor inválido", "FPS deve ser um número inteiro.")
            return

        try:
            music_db = float(self.music_db_var.get())
        except ValueError:
            messagebox.showerror("Valor inválido", "O volume da música deve ser um número (ex.: -28).")
            return

        bitrate = self.bitrate_var.get().strip() or "18M"
        overlay_text = self.overlay_text_var.get().strip() or self.brand_name

        try:
            update_yaml_values(self.config_path, [
                ("video", "standard_width", w),
                ("video", "standard_height", h),
                ("video", "standard_fps", fps),
                ("video", "export_bitrate", bitrate),
                ("overlay", "text", overlay_text),
                ("audio_mix", "music_db", music_db),
            ])
        except KeyError as e:
            messagebox.showerror("Erro ao salvar", str(e))
            return

        self.cfg = Config(str(self.config_path))
        self.db = Database(self.cfg)
        self._refresh_status_bar()
        self.width_var.set(str(w))
        self.height_var.set(str(h))
        messagebox.showinfo("Salvo", "Configurações salvas com sucesso.")

    # ------------------------------------------------------------------
    # Aba: Histórico
    # ------------------------------------------------------------------
    def _build_history_tab(self) -> None:
        frame = self.tab_history
        page_header(
            frame, self.fonts, "Histórico",
            "Projetos já processados pelo pipeline, com informações de vídeo, música, "
            "duração e status de exportação.",
        )

        card = RoundedCard(frame)
        card.pack(fill="x")
        body = card.body

        top = tk.Frame(body, bg=Colors.SURFACE)
        top.pack(fill="x", pady=(0, 10))
        ttk.Button(top, text="🔄  Atualizar", style="Outline.TButton",
                   command=self._refresh_history_tab).pack(side="left")

        columns = ("id", "titulo", "video", "musica", "duracao", "status", "data")
        self.history_tree = ttk.Treeview(body, columns=columns, show="headings", height=16)
        headings = {
            "id": "ID", "titulo": "Projeto", "video": "Vídeo de fundo", "musica": "Música",
            "duracao": "Duração (s)", "status": "Status", "data": "Data",
        }
        widths = {"id": 50, "titulo": 140, "video": 220, "musica": 160, "duracao": 90, "status": 90, "data": 150}
        for col in columns:
            self.history_tree.heading(col, text=headings[col])
            self.history_tree.column(col, width=widths[col], anchor="w")
        self.history_tree.pack(fill="both", expand=True)

    def _refresh_history_tab(self) -> None:
        for row in self.history_tree.get_children():
            self.history_tree.delete(row)
        for proj in self.db.list_projects():
            duracao = f"{proj['duracao']:.1f}" if proj["duracao"] else "-"
            self.history_tree.insert(
                "", "end",
                values=(proj["id"], proj["titulo"], proj["video"], proj["musica"],
                        duracao, proj["status"], proj["data"]),
            )

    # ------------------------------------------------------------------
    def _on_close(self) -> None:
        if self.watch_stop_event is not None:
            self.watch_stop_event.set()
        self.root.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(description="Interface gráfica do pipeline Auto Video Producer")
    parser.add_argument("--config", default="config.yaml", help="Caminho do config.yaml")
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    if not config_path.exists():
        print(f"config.yaml não encontrado em: {config_path}", file=sys.stderr)
        return 1

    root = tk.Tk()
    App(root, config_path)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
