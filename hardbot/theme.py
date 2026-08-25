"""
Тема Tkinter в стиле «Контур»: тёплая слоновая кость + графит.
Палитра перенесена один-в-один из Theme.cs (Кодекс/реестры СНГ).
"""

import tkinter as tk

# --- Палитра ----------------------------------------------------------------
WINDOW = "#FAF9F5"      # слоновая кость
CARD = "#FFFFFF"
CARD_ALT = "#F5F4ED"    # вторичная панель
BORDER = "#E6E2D9"      # тёплая тонкая линия
TEXT = "#2B2825"        # тёплые чернила
MUTED = "#6B6660"
FAINT = "#9A938A"
ACCENT = "#36332E"      # тёплый графит
ACCENT_HOVER = "#46423B"
ACCENT_SOFT = "#ECEAE3"
SUCCESS = "#5B8A3C"
WARN = "#8A6D00"
DANGER = "#B0472C"
DANGER_HOVER = "#8E3A23"

FONT = "Segoe UI"
SERIF = "Georgia"
MONO = "Consolas"


def f(size: int, bold: bool = False) -> tuple:
    return (FONT, size, "bold") if bold else (FONT, size)


def serif(size: int, bold: bool = True) -> tuple:
    return (SERIF, size, "bold") if bold else (SERIF, size)


def mono(size: int) -> tuple:
    return (MONO, size)


# --- Виджеты ----------------------------------------------------------------
def card(parent, **kw) -> tk.Frame:
    """Белая карточка с тонкой тёплой рамкой."""
    fr = tk.Frame(parent, bg=CARD, highlightbackground=BORDER,
                  highlightthickness=1, bd=0, **kw)
    return fr


def caption(parent, text: str) -> tk.Label:
    return tk.Label(parent, text=text.upper(), bg=CARD, fg=FAINT,
                    font=(FONT, 8, "bold"))


def label(parent, text: str, *, fg=TEXT, bg=CARD, font=None, **kw) -> tk.Label:
    return tk.Label(parent, text=text, bg=bg, fg=fg, font=font or f(10), **kw)


def entry(parent, **kw) -> tk.Entry:
    e = tk.Entry(parent, bg=CARD, fg=TEXT, insertbackground=TEXT,
                 relief="flat", font=f(10),
                 highlightbackground=BORDER, highlightcolor=ACCENT,
                 highlightthickness=1, **kw)
    return e


def _hoverable(btn: tk.Button, normal: str, hover: str):
    btn.bind("<Enter>", lambda _e: btn.config(bg=hover))
    btn.bind("<Leave>", lambda _e: btn.config(bg=normal))


def button(parent, text: str, command=None, *, kind: str = "accent", **kw) -> tk.Button:
    """kind: accent (графит) | ghost (обводка) | danger."""
    if kind == "accent":
        bg, fg, hover = ACCENT, "#FFFFFF", ACCENT_HOVER
    elif kind == "danger":
        bg, fg, hover = DANGER, "#FFFFFF", DANGER_HOVER
    else:  # ghost
        bg, fg, hover = CARD, TEXT, ACCENT_SOFT
    b = tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                  activebackground=hover, activeforeground=fg, relief="flat",
                  bd=0 if kind != "ghost" else 1, font=f(10), cursor="hand2",
                  padx=14, pady=8, highlightbackground=BORDER, **kw)
    _hoverable(b, bg, hover)
    return b


def apply_titlebar(root: tk.Tk):
    """Тёплая светлая шапка окна на Windows 11 (best-effort)."""
    try:
        import ctypes
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())

        def bgr(hex_):
            r, g, b = int(hex_[1:3], 16), int(hex_[3:5], 16), int(hex_[5:7], 16)
            return r | (g << 8) | (b << 16)

        for attr, color in ((35, WINDOW), (36, TEXT), (34, BORDER)):
            val = ctypes.c_int(bgr(color))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass
