from __future__ import annotations

import json
import math
import re
import threading
import traceback
import webbrowser
from datetime import date, datetime
from io import StringIO
from pathlib import Path

import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import mplfinance as mpf
import numpy as np
import pandas as pd
import requests
import yfinance as yf
from scipy.signal import find_peaks

APP_TITLE = "Aktien Screener"
APP_DIR = Path(__file__).resolve().parent
CACHE_DIR = APP_DIR / ".cache"

INDEX_SOURCES = {
    "S&P 500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "DAX": "https://en.wikipedia.org/wiki/DAX",
}
STANDARD_FIELDS = {"Open", "High", "Low", "Close", "Adj Close", "Volume", "Dividends", "Stock Splits", "Capital Gains"}

# ---------------------------------------------------------------------------
# Apple-inspiriertes Design: helle Oberfläche, ein Akzentton, viel Weißraum.
# ---------------------------------------------------------------------------
COLOR_BG = "#f5f6f8"
COLOR_CARD = "#ffffff"
COLOR_TEXT = "#101828"
COLOR_TEXT_SECONDARY = "#667085"
COLOR_BORDER = "#e4e7ec"
COLOR_ACCENT = "#0957a2"
COLOR_ACCENT_HOVER = "#074a8a"
COLOR_DISABLED = "#c7c7cc"
COLOR_GREEN = "#16a34a"
COLOR_GREEN_BG = "#e9f9ef"
COLOR_RED = "#e0333f"
COLOR_BLUE_BG = "#e7eff7"
COLOR_TAB_INACTIVE = "#eceef1"


def pick_font_family() -> str:
    try:
        available = set(tkfont.families())
    except Exception:
        return "Segoe UI"
    for candidate in ("SF Pro Display", "SF Pro Text", "Segoe UI Variable", "Segoe UI", "Helvetica Neue", "Helvetica"):
        if candidate in available:
            return candidate
    return "TkDefaultFont"


def round_rect_points(x1, y1, x2, y2, r):
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


class RoundedButton(tk.Canvas):
    """Pillenförmiger, flat gestalteter Button im Apple-Stil (ttk kennt keine runden Ecken).

    outline=True zeichnet die sekundäre Variante: weißer Grund, blaue Kontur/Schrift.
    """

    def __init__(self, parent, text, command, width=200, height=46, radius=23,
                 bg=COLOR_BG, fg="white", accent=COLOR_ACCENT, accent_hover=COLOR_ACCENT_HOVER,
                 disabled_bg=COLOR_DISABLED, font=None, outline=False):
        super().__init__(parent, width=width, height=height, bg=bg, highlightthickness=0)
        self.command = command
        self.accent = accent
        self.accent_hover = accent_hover
        self.disabled_bg = disabled_bg
        self.outline = outline
        self.base_bg = bg
        self.width, self.height, self.radius = width, height, radius
        self._enabled = True
        points = round_rect_points(1.5, 1.5, width - 1.5, height - 1.5, radius)
        if outline:
            self._shape = self.create_polygon(points, smooth=True, fill=bg, outline=accent, width=1.5)
            label_fg = accent
        else:
            self._shape = self.create_polygon(points, smooth=True, fill=accent, outline="")
            label_fg = fg
        self._label = self.create_text(width / 2, height / 2, text=text, fill=label_fg, font=font)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self.configure(cursor="hand2")

    def set_text(self, text: str) -> None:
        self.itemconfig(self._label, text=text)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if not self.outline:
            self.itemconfig(self._shape, fill=self.accent if enabled else self.disabled_bg)
        self.configure(cursor="hand2" if enabled else "arrow")

    def _on_enter(self, _event=None) -> None:
        if not self._enabled:
            return
        self.itemconfig(self._shape, fill=COLOR_BLUE_BG if self.outline else self.accent_hover)

    def _on_leave(self, _event=None) -> None:
        if not self._enabled:
            return
        self.itemconfig(self._shape, fill=self.base_bg if self.outline else self.accent)

    def _on_click(self, _event=None) -> None:
        if self._enabled and self.command:
            self.command()


class TabBar(tk.Canvas):
    """Reiterleiste im App-Stil: jeder Reiter ist eine eigene abgerundete Fläche mit
    sichtbarem Abstand zum nächsten – der aktive Reiter ist weiß mit dünnem Rand und
    blauer Schrift, inaktive Reiter sind dezent grau.
    """

    def __init__(self, parent, items: list[tuple[str, str]], command, bg=COLOR_BG, height=44,
                 inactive_bg=COLOR_TAB_INACTIVE, gap=8, font=None):
        super().__init__(parent, bg=bg, highlightthickness=0, height=height)
        self.items = items  # Liste aus (key, Anzeigetext), key = Ticker
        self.command = command
        self.inactive_bg = inactive_bg
        self.gap = gap
        self.font = tkfont.Font(font=font) if font else tkfont.nametofont("TkDefaultFont")
        self.selected = items[0][0] if items else None
        self._segments: dict[str, tuple[float, float]] = {}
        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<Button-1>", self._on_click)

    def select(self, key: str, fire: bool = True) -> None:
        if key not in dict(self.items):
            return
        self.selected = key
        self._redraw()
        if fire and self.command:
            self.command(key)

    def _fit_text(self, text: str, max_width: float) -> str:
        if self.font.measure(text) <= max_width:
            return text
        truncated = text
        while truncated and self.font.measure(truncated + "…") > max_width:
            truncated = truncated[:-1]
        return (truncated + "…") if truncated else text

    def _redraw(self) -> None:
        self.delete("all")
        width = max(self.winfo_width(), 1)
        height = int(self["height"])
        n = len(self.items)
        if n == 0:
            return
        seg_w = width / n
        self._segments = {}
        for i, (key, label) in enumerate(self.items):
            x1, x2 = i * seg_w, (i + 1) * seg_w
            self._segments[key] = (x1, x2)
            selected = key == self.selected
            bx1, bx2 = x1 + self.gap / 2, x2 - self.gap / 2
            fill = COLOR_CARD if selected else self.inactive_bg
            outline = COLOR_BORDER if selected else ""
            self.create_polygon(round_rect_points(bx1, 3, bx2, height - 3, 12), smooth=True, fill=fill, outline=outline, width=1)
            color = COLOR_ACCENT if selected else COLOR_TEXT_SECONDARY
            text = self._fit_text(label, (bx2 - bx1) - 16)
            self.create_text((x1 + x2) / 2, height / 2, text=text, fill=color, font=self.font)

    def _on_click(self, event) -> None:
        for key, (x1, x2) in self._segments.items():
            if x1 <= event.x <= x2 and key != self.selected:
                self.select(key)
                return


class ScrollableArea(tk.Frame):
    """Vertikal scrollbarer Container – nötig, da der mehrteilige Chart (Kurs + MACD + RSI)
    plus Info-Panel öfter höher ist als das Fenster."""

    def __init__(self, parent, bg=COLOR_BG):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.inner.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(self._window, width=e.width))
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.canvas.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))


# =============================================================================
# Watchlist: Index-Mitglieder laden (nur S&P 500 und DAX)
# =============================================================================

def tradingview_url(ticker: str) -> str:
    symbol = ticker[:-3] if ticker.endswith(".DE") else ticker
    return f"https://www.tradingview.com/symbols/{symbol}/"


def normalize_ticker(raw: str) -> str:
    value = str(raw).strip().upper().strip('"\'')
    if not value:
        return ""
    value = re.split(r"[;,#\t ]", value, maxsplit=1)[0].strip()
    if ":" in value:
        value = value.split(":", 1)[1]
    return value


def _download_html_tables(url: str) -> list[pd.DataFrame]:
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0 Aktien-Screener"}, timeout=30)
    response.raise_for_status()
    return pd.read_html(StringIO(response.text))


def _column_name(column: object) -> str:
    if isinstance(column, tuple):
        return " ".join(str(x) for x in column if str(x) != "nan").strip().lower()
    return str(column).strip().lower()


def _members_frame(tickers: list[str], names: list[str], index_name: str) -> pd.DataFrame:
    frame = pd.DataFrame({"Ticker": tickers, "Name": names})
    frame["Ticker"] = frame["Ticker"].astype(str).map(normalize_ticker)
    frame["Name"] = frame["Name"].fillna("").astype(str).str.strip()
    frame["Index"] = index_name
    return frame[frame["Ticker"] != ""].drop_duplicates("Ticker").reset_index(drop=True)


def fetch_index_members(index_name: str) -> pd.DataFrame:
    tables = _download_html_tables(INDEX_SOURCES[index_name])
    if index_name == "S&P 500":
        for table in tables:
            columns = {_column_name(c): c for c in table.columns}
            symbol_col = next((c for n, c in columns.items() if n == "symbol"), None)
            name_col = next((c for n, c in columns.items() if "security" in n or "company" in n), None)
            if symbol_col is not None and len(table) >= 450:
                tickers = [str(x).strip().upper().replace(".", "-") for x in table[symbol_col].dropna()]
                names = table.loc[table[symbol_col].notna(), name_col].astype(str).tolist() if name_col is not None else tickers
                return _members_frame(tickers, names, index_name)
    if index_name == "DAX":
        for table in tables:
            columns = {_column_name(c): c for c in table.columns}
            symbol_col = next((c for n, c in columns.items() if "ticker" in n or "symbol" in n), None)
            name_col = next((c for n, c in columns.items() if "company" in n or "constituent" in n or n == "name"), None)
            if symbol_col is not None and 35 <= len(table) <= 50:
                tickers, names = [], []
                for idx, raw in table[symbol_col].dropna().items():
                    ticker = re.sub(r"[^A-Z0-9.-]", "", str(raw).strip().upper().split()[0])
                    if ticker:
                        tickers.append(ticker if ticker.endswith(".DE") else ticker + ".DE")
                        names.append(str(table.loc[idx, name_col]).strip() if name_col is not None else ticker)
                if len(tickers) >= 35:
                    return _members_frame(tickers, names, index_name)
    raise ValueError(f"Mitgliederliste für {index_name} konnte nicht erkannt werden.")


def merge_member_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame(columns=["Ticker", "Name", "Index"])
    combined = pd.concat(frames, ignore_index=True)
    rows = []
    for ticker, group in combined.groupby("Ticker", sort=False):
        names = [x for x in group["Name"].astype(str) if x and x.lower() != "nan"]
        indices = list(dict.fromkeys(group["Index"].astype(str)))
        rows.append({"Ticker": ticker, "Name": names[0] if names else ticker, "Index": ", ".join(indices)})
    return pd.DataFrame(rows)


def normalize_download_frame(df: pd.DataFrame, ticker: str, name: str = "", index_name: str = "") -> pd.DataFrame:
    result = df.copy()
    if isinstance(result.columns, pd.MultiIndex):
        columns = []
        for column in result.columns:
            parts = [str(x).strip() for x in column if str(x).strip() not in {"", "None", "nan"}]
            field = next((x for x in parts if x in STANDARD_FIELDS), None)
            columns.append(field or next((x for x in parts if x.upper() != ticker.upper()), "Value"))
        result.columns = columns
    result = result.loc[:, ~result.columns.duplicated()].reset_index()
    if "Datetime" in result.columns and "Date" not in result.columns:
        result = result.rename(columns={"Datetime": "Date"})
    if "Date" not in result.columns:
        raise ValueError("Keine Datumsspalte gefunden")
    result["Date"] = pd.to_datetime(result["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    result.insert(0, "Ticker", ticker)
    result.insert(1, "Name", name or ticker)
    result.insert(2, "Index", index_name)
    preferred = ["Ticker", "Name", "Index", "Date", "Open", "High", "Low", "Close", "Adj Close", "Volume", "Dividends", "Stock Splits", "Capital Gains"]
    existing = [x for x in preferred if x in result.columns]
    return result[existing + [x for x in result.columns if x not in existing]]


# =============================================================================
# Kursmuster-Erkennung: Support/Widerstand, Kanal, Kopf-Schulter, Elliott, Ausbruch
# =============================================================================

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up, down = delta.clip(lower=0), -delta.clip(upper=0)
    avg_up = up.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_down = down.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_up / avg_down.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100)


def cluster_points(points: list[tuple[int, float]], tolerance: float) -> list[list[tuple[int, float]]]:
    clusters: list[list[tuple[int, float]]] = []
    for point in sorted(points, key=lambda x: x[1]):
        best, best_diff = None, float("inf")
        for i, cluster in enumerate(clusters):
            center = float(np.median([x[1] for x in cluster]))
            diff = abs(point[1] - center) / center
            if diff <= tolerance and diff < best_diff:
                best, best_diff = i, diff
        if best is None:
            clusters.append([point])
        else:
            clusters[best].append(point)
    return clusters


def collapse_nearby(points: list[tuple[int, float]], gap: int = 7) -> list[tuple[int, float]]:
    if not points:
        return []
    groups = [[sorted(points)[0]]]
    for point in sorted(points)[1:]:
        if point[0] - groups[-1][-1][0] <= gap:
            groups[-1].append(point)
        else:
            groups.append([point])
    return [min(group, key=lambda x: x[1]) for group in groups]


def find_level(series: pd.Series, atr: pd.Series, current: float, direction: str, lookback: int = 180) -> tuple[float, int]:
    """Sucht gehäuft getestete horizontale Kurslevel.

    direction='above': Widerstand oberhalb des aktuellen Kurses (auf Basis der Hochs).
    direction='below': Support unterhalb des aktuellen Kurses (auf Basis der Tiefs).
    """
    # Der aktuelle (letzte) Balken wird bewusst ausgeschlossen: Widerstand/Support sollen
    # aus der Historie VOR heute stammen, sonst kann der heutige Kurs bei einem neuen
    # Extremwert versehentlich sein eigenes Widerstands-/Supportlevel werden.
    history = series.iloc[:-1] if len(series) > 1 else series
    values = history.iloc[-lookback:].reset_index(drop=True)
    if values.empty:
        return (np.nan, 0)
    med_atr = float(np.nanmedian(atr.iloc[-lookback:]))
    prominence = max(med_atr * 0.55, float(np.nanmedian(values)) * 0.005)
    if direction == "above":
        peaks, _ = find_peaks(values.values, distance=6, prominence=prominence)
    else:
        peaks, _ = find_peaks(-values.values, distance=6, prominence=prominence)
    tolerance = max(0.008, min(0.018, (float(atr.iloc[-1]) / current) * 0.55))
    levels = []
    for cluster in cluster_points([(int(i), float(values.iloc[i])) for i in peaks], tolerance):
        points = collapse_nearby(cluster)
        center = float(np.median([x[1] for x in points]))
        if direction == "above" and center > current * 1.02:
            levels.append((center, len(points)))
        elif direction == "below" and center < current * 0.98:
            levels.append((center, len(points)))
    reverse = direction == "below"
    multi = sorted([x for x in levels if x[1] >= 2], key=lambda x: x[0], reverse=reverse)
    if multi:
        return multi[0]
    if levels:
        return sorted(levels, key=lambda x: x[0], reverse=reverse)[0]
    if direction == "above":
        target = float(values.quantile(0.9))
        if target <= current * 1.02:
            target = float(values.max())
        return (target, 1) if target > current else (np.nan, 0)
    target = float(values.quantile(0.1))
    if target >= current * 0.98:
        target = float(values.min())
    return (target, 1) if target < current else (np.nan, 0)


def zigzag_pivots(close: pd.Series, pct_threshold: float = 0.045) -> list[tuple[int, float, str]]:
    """Vereinfachter Zickzack-Indikator: liefert abwechselnde Hoch-/Tiefpunkte.

    Grundlage für die heuristische Kopf-Schulter- und Elliott-ABC-Erkennung.
    """
    values = close.reset_index(drop=True)
    if len(values) < 3:
        return []
    pivots: list[tuple[int, float, str]] = []
    direction = None
    ext_idx, ext_price = 0, float(values.iloc[0])
    for i in range(1, len(values)):
        price = float(values.iloc[i])
        if direction is None:
            if price >= ext_price * (1 + pct_threshold):
                direction, ext_idx, ext_price = "up", i, price
            elif price <= ext_price * (1 - pct_threshold):
                direction, ext_idx, ext_price = "down", i, price
            continue
        if direction == "up":
            if price >= ext_price:
                ext_idx, ext_price = i, price
            elif price <= ext_price * (1 - pct_threshold):
                pivots.append((ext_idx, ext_price, "H"))
                direction, ext_idx, ext_price = "down", i, price
        else:
            if price <= ext_price:
                ext_idx, ext_price = i, price
            elif price >= ext_price * (1 + pct_threshold):
                pivots.append((ext_idx, ext_price, "L"))
                direction, ext_idx, ext_price = "up", i, price
    pivots.append((ext_idx, ext_price, "H" if direction == "up" else "L"))
    return pivots


def detect_head_shoulders(pivots: list[tuple[int, float, str]]) -> dict | None:
    """Klassisches (bärisches) Kopf-Schulter-Top: drei Hochs, mittleres am höchsten."""
    highs = [p for p in pivots if p[2] == "H"]
    if len(highs) < 3:
        return None
    left, head, right = highs[-3], highs[-2], highs[-1]
    troughs = [p for p in pivots if p[2] == "L" and left[0] < p[0] < right[0]]
    if len(troughs) < 2:
        return None
    neckline = float(np.mean([troughs[0][1], troughs[1][1]]))
    head_higher = head[1] > left[1] * 1.02 and head[1] > right[1] * 1.02
    shoulders_similar = abs(left[1] - right[1]) / max(left[1], right[1]) < 0.07
    if head_higher and shoulders_similar:
        return {
            "typ": "Kopf-Schulter-Top (bärisches Umkehrmuster)", "neckline": neckline, "kopf": head[1], "index": right[0],
            "punkte": [(left[0], left[1], "linke Schulter"), (head[0], head[1], "Kopf"), (right[0], right[1], "rechte Schulter")],
            "neckline_punkte": [(troughs[0][0], troughs[0][1]), (troughs[1][0], troughs[1][1])],
        }
    return None


def detect_inverse_head_shoulders(pivots: list[tuple[int, float, str]]) -> dict | None:
    """Inverses (bullisches) Kopf-Schulter-Boden-Muster."""
    lows = [p for p in pivots if p[2] == "L"]
    if len(lows) < 3:
        return None
    left, head, right = lows[-3], lows[-2], lows[-1]
    peaks = [p for p in pivots if p[2] == "H" and left[0] < p[0] < right[0]]
    if len(peaks) < 2:
        return None
    neckline = float(np.mean([peaks[0][1], peaks[1][1]]))
    head_lower = head[1] < left[1] * 0.98 and head[1] < right[1] * 0.98
    shoulders_similar = abs(left[1] - right[1]) / max(left[1], right[1]) < 0.07
    if head_lower and shoulders_similar:
        return {
            "typ": "Inverse Kopf-Schulter (bullisches Umkehrmuster)", "neckline": neckline, "kopf": head[1], "index": right[0],
            "punkte": [(left[0], left[1], "linke Schulter"), (head[0], head[1], "Kopf"), (right[0], right[1], "rechte Schulter")],
            "neckline_punkte": [(peaks[0][0], peaks[0][1]), (peaks[1][0], peaks[1][1])],
        }
    return None


def fit_trend_channel(pivots: list[tuple[int, float, str]]) -> dict:
    """Trendkanal für die Chart-Anzeige: verbindet die letzten zwei Swing-Hochs zu einer
    oberen Trendlinie und die letzten zwei Swing-Tiefs zu einer unteren Trendlinie – so, wie
    man einen Kanal auch manuell einzeichnen würde.
    """
    highs = [p for p in pivots if p[2] == "H"]
    lows = [p for p in pivots if p[2] == "L"]
    result: dict = {"oben": None, "unten": None}
    if len(highs) >= 2:
        (i1, p1), (i2, p2) = highs[-2][:2], highs[-1][:2]
        if i2 != i1:
            result["oben"] = {"punkte": [(i1, p1), (i2, p2)]}
    if len(lows) >= 2:
        (i1, p1), (i2, p2) = lows[-2][:2], lows[-1][:2]
        if i2 != i1:
            result["unten"] = {"punkte": [(i1, p1), (i2, p2)]}
    return result


def detect_channel(support_touches: int, support_center: float, resistance: float, resistance_tests: int) -> dict | None:
    """Kanal = Support UND Widerstand jeweils mehrfach getestet."""
    if resistance_tests >= 2 and support_touches >= 2 and np.isfinite(resistance) and resistance > support_center:
        return {"typ": "Handelskanal (Support & Widerstand jeweils mehrfach bestätigt)"}
    return None


ELLIOTT_LABELS_BY_LENGTH = {
    2: ["0", "1"], 3: ["0", "1", "2"], 4: ["0", "1", "2", "3"], 5: ["0", "1", "2", "3", "4"],
    6: ["0", "1", "2", "3", "4", "5"], 7: ["0", "1", "2", "3", "4", "5", "A"],
    8: ["0", "1", "2", "3", "4", "5", "A", "B"], 9: ["0", "1", "2", "3", "4", "5", "A", "B", "C"],
}
ELLIOTT_POSITION_BY_LENGTH = {
    2: "Welle 1 (Impuls) läuft", 3: "Welle 2 (Korrektur von Welle 1) läuft",
    4: "Welle 3 (Impuls) läuft", 5: "Welle 4 (Korrektur von Welle 3) läuft",
    6: "Impuls 1–5 abgeschlossen – Korrektur A-B-C steht bevor", 7: "Welle B (Erholung nach Welle A) läuft",
    8: "Welle C (letzte Korrekturwelle) läuft", 9: "Korrektur A-B-C abgeschlossen – neuer Impuls könnte beginnen",
}


def _validate_elliott_window(pts: list[tuple[int, float, str]]) -> dict | None:
    """Prüft ein Fenster aufeinanderfolgender Zickzack-Punkte gegen die drei harten
    Elliott-Regeln (Welle 2 nicht über 100 % von Welle 1 hinaus, Welle 3 nicht die
    kürzeste, Welle 4 überschneidet nicht das Kursgebiet von Welle 1). Verletzt das
    Fenster eine harte Regel, ist die Zählung ungültig (None).
    """
    n = len(pts)
    if n < 2 or n not in ELLIOTT_LABELS_BY_LENGTH:
        return None
    for i in range(1, n):
        if pts[i][2] == pts[i - 1][2]:
            return None
    up = pts[0][2] == "L"
    price = [p[1] for p in pts]
    idx = [p[0] for p in pts]

    def leg(a: int, b: int) -> float:
        return abs(price[b] - price[a])

    hints: list[str] = []
    if n >= 3:
        wave2_ok = (price[2] > price[0]) if up else (price[2] < price[0])
        if not wave2_ok:
            return None
    if n >= 6:
        w1, w3, w5 = leg(0, 1), leg(2, 3), leg(4, 5)
        if w3 < w1 and w3 < w5:
            return None
    elif n >= 4:
        w1, w3 = leg(0, 1), leg(2, 3)
        if w3 < w1 * 0.4:
            hints.append("Welle 3 wirkt im Verhältnis zu Welle 1 ungewöhnlich kurz")
    if n >= 5:
        wave4_ok = (price[4] > price[1]) if up else (price[4] < price[1])
        if not wave4_ok:
            return None
    if n >= 8:
        b_exceeds = (price[7] > price[5]) if up else (price[7] < price[5])
        if b_exceeds:
            hints.append("Welle B läuft über das Ende von Welle 5 hinaus (untypisch)")

    labels = ELLIOTT_LABELS_BY_LENGTH[n]
    return {
        "richtung": "aufwärts" if up else "abwärts",
        "punkte": list(zip(idx, price, labels)),
        "anzahl_wellen": n,
        "aktuelle_position": ELLIOTT_POSITION_BY_LENGTH[n],
        "hinweise": hints,
    }


def label_elliott_wave(pivots: list[tuple[int, float, str]]) -> dict | None:
    """Vollständige Elliott-Wellen-Zählung: sucht – ausgehend vom aktuellsten Punkt
    rückwärts – das längste noch gültige Fenster und liefert die aktuelle Wellenposition.
    """
    for window in (9, 8, 7, 6, 5, 4, 3, 2):
        if len(pivots) < window:
            continue
        result = _validate_elliott_window(pivots[-window:])
        if result:
            return result
    return None


def detect_breakout(close: pd.Series, high: pd.Series, low: pd.Series, atr: pd.Series, volume: pd.Series, lookback: int) -> dict | None:
    """Ausbruch über einen Widerstand bzw. unter einen Support, mit Volumen-Check."""
    if len(close) < 2:
        return None
    current, prev = float(close.iloc[-1]), float(close.iloc[-2])
    breakout_resistance, _ = find_level(high, atr, prev, "above", lookback)
    breakout_support, _ = find_level(low, atr, prev, "below", lookback)
    vol_now = float(volume.iloc[-1])
    history = volume.iloc[-21:-1]
    vol_avg20 = float(history.mean()) if len(history) else float(volume.mean() or 0)
    vol_confirmed = vol_avg20 > 0 and vol_now > vol_avg20 * 1.3
    if np.isfinite(breakout_resistance) and prev <= breakout_resistance < current:
        return {"typ": "Ausbruch über Widerstand (bullisch)", "level": breakout_resistance, "volumen_bestaetigt": vol_confirmed}
    if np.isfinite(breakout_support) and prev >= breakout_support > current:
        return {"typ": "Ausbruch unter Support (bärisch)", "level": breakout_support, "volumen_bestaetigt": vol_confirmed}
    return None


def analyze_ticker(group: pd.DataFrame) -> dict | None:
    g = group.sort_values("Date").drop_duplicates("Date").dropna(subset=["Close", "High", "Low"]).copy()
    if len(g) < 180:
        return None
    raw = g["Close"].astype(float)
    adj = g["Adj Close"].astype(float) if "Adj Close" in g else raw
    factor = (adj / raw.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).fillna(1)
    close, high, low = adj, g["High"].astype(float) * factor, g["Low"].astype(float) * factor
    volume = g.get("Volume", pd.Series(0, index=g.index)).astype(float).fillna(0)
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()
    rs = rsi(close)
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    histogram = macd - macd.ewm(span=9, adjust=False).mean()
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14, min_periods=5).mean()
    lb = min(230, len(g))
    lo, cl, hi, at, vol = [s.iloc[-lb:].reset_index(drop=True) for s in (low, close, high, atr, volume)]
    dates = pd.to_datetime(g["Date"].iloc[-lb:]).reset_index(drop=True)
    current, current_atr = float(cl.iloc[-1]), float(at.iloc[-1])
    tolerance = max(0.008, min(0.018, (current_atr / current) * 0.55))
    prominence = max(float(np.nanmedian(at)) * 0.5, float(np.nanmedian(lo)) * 0.0045)
    minima, _ = find_peaks(-lo.values, distance=6, prominence=prominence)
    candidates = []
    for cluster in cluster_points([(int(i), float(lo.iloc[i])) for i in minima], tolerance):
        points = collapse_nearby(cluster)
        if len(points) < 2:
            continue
        center = float(np.median([x[1] for x in points]))
        if center > current * (1 + tolerance * 1.3):
            continue
        recent = np.where((lo.iloc[-5:] <= center * (1 + tolerance)) & (lo.iloc[-5:] >= center * (1 - 2 * tolerance)) & (cl.iloc[-5:] >= center * (1 - 1.1 * tolerance)))[0]
        if len(recent):
            idx = lb - 5 + int(recent[-1])
            if idx - points[-1][0] > 7:
                points.append((idx, float(lo.iloc[idx])))
        if len(points) < 2 or points[-1][0] - points[0][0] < 25:
            continue
        hard = center * (1 - max(0.02, tolerance * 1.8))
        below = (cl.iloc[points[1][0]:] < hard).values
        max_run = run = 0
        for flag in below:
            run = run + 1 if flag else 0
            max_run = max(max_run, run)
        bounces = [float(hi.iloc[i + 1:min(len(hi), i + 11)].max() / center - 1) for i, _ in points if i + 3 < len(hi)]
        candidates.append({
            "center": center, "points": points, "touches": len(points), "days": lb - 1 - points[-1][0],
            "distance": (current - center) / center, "hard": hard, "max_run": max_run,
            "broken": current < hard, "bounce": float(np.median(bounces)) if bounces else 0,
            "dispersion": float(np.std([x[1] for x in points]) / center), "tolerance": tolerance,
        })

    def candidate_score(x: dict) -> float:
        score = min(x["touches"], 5) * 10
        d = x["distance"]
        score += 30 if -0.005 <= d <= 0.02 else 23 if d <= 0.04 else 14 if d <= 0.06 else 7 if d <= 0.09 else -10
        score += 16 if x["days"] <= 7 else 10 if x["days"] <= 20 else 5 if x["days"] <= 45 else 0
        score += min(10, x["bounce"] * 80) - x["dispersion"] * 500 - x["max_run"] * 12
        return score - (50 if x["broken"] else 0)

    # Die Analyse bricht NICHT ab, nur weil aktuell kein mehrfach getesteter Support in
    # Kursnähe existiert – ohne Long-Support-Kandidat bleiben die Long-spezifischen Felder
    # (Support, Stop-Idee, Exit-Ziel, Score) neutral/NaN, wodurch der Ticker automatisch aus
    # der A-Kandidaten-Auswahl herausfällt, statt die komplette Analyse zu verwerfen.
    long_candidate_found = bool(candidates)
    if long_candidate_found:
        best = max(candidates, key=candidate_score)
    else:
        best = {
            "center": np.nan, "points": [], "touches": 0, "days": np.nan,
            "distance": np.nan, "hard": np.nan, "max_run": 0,
            "broken": False, "bounce": 0.0, "dispersion": 0.0, "tolerance": tolerance,
        }
    resistance, resistance_tests = find_level(hi, at, current, "above", lb)
    stop = (best["center"] - max(current_atr * 0.75, best["center"] * 0.012)) if long_candidate_found else np.nan
    e20, e50, e200 = float(ema20.iloc[-1]), float(ema50.iloc[-1]), float(ema200.iloc[-1])
    slope = float(ema50.iloc[-1] / ema50.iloc[-21] - 1)
    rv, hv, hp = float(rs.iloc[-1]), float(histogram.iloc[-1]), float(histogram.iloc[-4])
    prev_rsi = float(rs.iloc[-2]) if len(rs) >= 2 else rv
    open_series = g["Open"].astype(float) * factor if "Open" in g else close
    latest_open = float(open_series.iloc[-1])
    prev_close = float(close.iloc[-2])
    latest_low = float(low.iloc[-1])
    latest_high = float(high.iloc[-1])
    candle_range = max(latest_high - latest_low, current * 0.001)
    lower_wick = min(latest_open, current) - latest_low
    upper_wick = latest_high - max(latest_open, current)
    bullish_reversal_candle = bool(
        current > latest_open and current > prev_close
        and lower_wick / candle_range >= 0.30
        and long_candidate_found and latest_low <= best["center"] * (1 + tolerance * 1.3)
    )
    bearish_reversal_candle = bool(
        current < latest_open and current < prev_close
        and upper_wick / candle_range >= 0.30
        and np.isfinite(resistance) and latest_high >= resistance * (1 - tolerance * 1.3)
    )
    rsi_turns_up = bool(rv > prev_rsi and (prev_rsi < 45 or rv < 50))
    rsi_turns_down = bool(rv < prev_rsi and (prev_rsi > 55 or rv > 50))
    macd_improves = bool(hv > hp)
    above_ema20 = bool(current > e20)
    reversal_signals = []
    if above_ema20: reversal_signals.append("Schlusskurs über EMA20")
    if macd_improves: reversal_signals.append("MACD verbessert")
    if bullish_reversal_candle: reversal_signals.append("bullische Umkehrkerze")
    if rsi_turns_up: reversal_signals.append("RSI dreht nach oben")
    reversal_confirmed = bool(reversal_signals)

    pivots = zigzag_pivots(cl)
    head_shoulders = detect_head_shoulders(pivots)
    inverse_head_shoulders = detect_inverse_head_shoulders(pivots)
    channel = detect_channel(best["touches"], best["center"], resistance, resistance_tests)
    elliott = label_elliott_wave(pivots)
    breakout = detect_breakout(cl, hi, lo, at, vol, lb)
    trend_channel_raw = fit_trend_channel(pivots)

    # Kursziel-Fallback ("Measured Move"): existiert nach einem echten Ausbruch kein
    # historisches Gegenlevel mehr, wird die Distanz vom Ausgangslevel zum aktuellen Kurs
    # einfach in dieselbe Richtung weiter projiziert.
    exit_target = resistance
    if not np.isfinite(exit_target):
        exit_target = current + abs(current - best["center"]) if long_candidate_found else current + current_atr * 3
    risk, reward = current - stop, exit_target - current
    crv = reward / risk if risk > 0 and np.isfinite(reward) and reward > 0 else np.nan

    patterns: list[str] = []
    bullish_votes = bearish_votes = 0
    if inverse_head_shoulders:
        patterns.append(inverse_head_shoulders["typ"]); bullish_votes += 1
    if head_shoulders:
        patterns.append(head_shoulders["typ"]); bearish_votes += 1
    if channel:
        patterns.append(channel["typ"])
    elliott_decisive = None
    if elliott:
        elliott_up = elliott["richtung"] == "aufwärts"
        n_waves = elliott["anzahl_wellen"]
        if n_waves in (5, 9):
            elliott_decisive = elliott_up
        elif n_waves == 6:
            elliott_decisive = not elliott_up
        patterns.append(f"Elliott-Wellen: {elliott['aktuelle_position']} ({elliott['richtung']})")
        if elliott_decisive is True:
            bullish_votes += 1
        elif elliott_decisive is False:
            bearish_votes += 1
    if breakout:
        tag = " [Volumen bestätigt]" if breakout["volumen_bestaetigt"] else " [ohne Volumenbestätigung]"
        patterns.append(breakout["typ"] + tag)
        if "bullisch" in breakout["typ"]:
            bullish_votes += 1
        else:
            bearish_votes += 1
    if reversal_confirmed:
        bullish_votes += 1
    if bearish_reversal_candle:
        bearish_votes += 1

    if bullish_votes > bearish_votes and bullish_votes > 0:
        signal_type = "Long-Setup"
    elif bearish_votes > bullish_votes and bearish_votes > 0:
        signal_type = "Short-Setup"
    else:
        signal_type = "Kein klares Setup"

    if long_candidate_found:
        score = 10 if best["touches"] == 2 else 18 if best["touches"] == 3 else 24 if best["touches"] == 4 else 28
        d = best["distance"]
        score += 24 if -0.005 <= d <= 0.02 else 19 if d <= 0.04 else 12 if d <= 0.06 else 6 if d <= 0.09 else 0
        score += 15 if best["days"] <= 7 else 11 if best["days"] <= 15 else 7 if best["days"] <= 30 else 3 if best["days"] <= 60 else 0
        score += min(10, max(0, best["bounce"] * 80))
        score += 5 if current > e200 else 0
        score += 4 if e50 > e200 else 0
        score += 4 if slope > 0.01 else 2 if slope > 0 else 0
        score += 4 if current > e20 else -5
        score += 3 if current > e50 else -5
        score += 4 if 42 <= rv <= 62 else 1 if 35 <= rv < 42 or 62 < rv <= 68 else -3
        score += 3 if hv > 0 else -3
        score += 4 if macd_improves else -4
        score += 4 if bullish_reversal_candle else 0
        score += 3 if rsi_turns_up else 0
        score += 8 if np.isfinite(crv) and crv >= 3 else 6 if np.isfinite(crv) and crv >= 2 else 3 if np.isfinite(crv) and crv >= 1.5 else -5 if np.isfinite(crv) and crv < 1.5 else 0
        score += 6 if inverse_head_shoulders else 0
        score += 4 if channel else 0
        score += 5 if (breakout and "bullisch" in breakout["typ"] and breakout["volumen_bestaetigt"]) else 2 if (breakout and "bullisch" in breakout["typ"]) else 0
        score -= 10 if head_shoulders else 0
        score -= min(25, best["max_run"] * 9) + min(10, best["dispersion"] * 500)
        if best["broken"]:
            score -= 35
        score = round(max(0, min(100, score)), 1)
    else:
        score = 0.0
    d = best["distance"]
    support_gebrochen = bool(best["broken"] or d < -0.025)

    recent_volume = volume.iloc[-20:]
    vol20 = float(recent_volume.mean())
    traded_value = (close * volume).replace([np.inf, -np.inf], np.nan)
    avg_traded_value20 = float(traded_value.iloc[-20:].mean())

    # Chart-Overlay: exakte Datumsangaben für Trendkanal, SKS-/Elliott-Punkte und
    # Ausbruchslevel, damit die grafische Auswertung sie direkt einzeichnen kann.
    def _pt(idx: int, price: float) -> tuple[str | None, float]:
        ii = int(idx)
        if 0 <= ii < len(dates):
            return dates.iloc[ii].date().isoformat(), float(price)
        return None, float(price)

    overlay: dict = {}
    overlay_pivots = []
    for i, p, t in pivots:
        dt, price = _pt(i, p)
        if dt is not None:
            overlay_pivots.append([dt, price, t])
    overlay["pivots"] = overlay_pivots
    if trend_channel_raw.get("oben"):
        overlay["kanal_oben"] = [list(_pt(i, p)) for i, p in trend_channel_raw["oben"]["punkte"]]
    if trend_channel_raw.get("unten"):
        overlay["kanal_unten"] = [list(_pt(i, p)) for i, p in trend_channel_raw["unten"]["punkte"]]
    sks_pattern = head_shoulders or inverse_head_shoulders
    if sks_pattern:
        sks_points = []
        for i, p, label in sks_pattern["punkte"]:
            dt, price = _pt(i, p)
            sks_points.append([dt, price, label])
        overlay["sks"] = {
            "typ": sks_pattern["typ"], "punkte": sks_points,
            "neckline_punkte": [list(_pt(i, p)) for i, p in sks_pattern["neckline_punkte"]],
        }
    if elliott:
        elliott_points = []
        for i, p, label in elliott["punkte"]:
            dt, price = _pt(i, p)
            elliott_points.append([dt, price, label])
        overlay["elliott"] = {"richtung": elliott["richtung"], "punkte": elliott_points}
    if breakout:
        overlay["breakout"] = {
            "typ": breakout["typ"], "level": float(breakout["level"]),
            "datum": dates.iloc[-1].date().isoformat(),
        }
    chart_overlay_json = json.dumps(overlay, ensure_ascii=False)

    return {
        "Ticker": str(g["Ticker"].iloc[-1]), "Name": str(g["Name"].iloc[-1]) if "Name" in g else str(g["Ticker"].iloc[-1]),
        "Index": str(g["Index"].iloc[-1]) if "Index" in g else "",
        "Kurs": current, "Support": best["center"],
        "Supportzone unten": best["center"] * (1 - tolerance) if long_candidate_found else np.nan,
        "Supportzone oben": best["center"] * (1 + tolerance) if long_candidate_found else np.nan,
        "Abstand Support %": d * 100, "Support-Tests": best["touches"], "Letzter Test vor Tagen": best["days"],
        "Nächster Widerstand": resistance,
        "Stop-Idee": stop, "Entry-Idee": current, "Exit-Ziel": exit_target, "CRV bis Widerstand": crv,
        "EMA50 Trend 1M %": slope * 100, "RSI14": rv,
        "Umkehrsignale": ", ".join(reversal_signals) if reversal_signals else "Keine",
        "Umkehrsignal-Anzahl": len(reversal_signals),
        "Über EMA50": bool(current > e50), "Über EMA200": bool(current > e200),
        "Ø Aktienvolumen 20T": vol20, "Ø Handelswert 20T": avg_traded_value20,
        "Technischer Score": score, "Support gebrochen": support_gebrochen,
        "Erkannte Muster": ", ".join(patterns) if patterns else "Keine",
        "Signaltyp": signal_type,
        "Elliott-Position": elliott["aktuelle_position"] if elliott else "Keine gültige Zählung gefunden",
        "Elliott-Richtung": elliott["richtung"] if elliott else "",
        "Ausbruch erkannt": breakout["typ"] if breakout else "Nein",
        "Chart-Overlay": chart_overlay_json,
    }


# =============================================================================
# GUI
# =============================================================================

class AktienScreenerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.font_family = pick_font_family()
        self.title(APP_TITLE)
        self.configure(bg=COLOR_BG)
        self.geometry("1200x900")
        self.minsize(980, 680)

        self.data: pd.DataFrame | None = None
        self.candidates: pd.DataFrame | None = None
        self.status_var = tk.StringVar(value="Bereit")
        self.progress_var = tk.DoubleVar(value=0)
        self.running = False

        self._setup_style()
        self._build_layout()

    # ------------------------------------------------------------- Styling
    def _setup_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        base_font = (self.font_family, 11)
        style.configure(".", background=COLOR_BG, foreground=COLOR_TEXT, font=base_font)
        style.configure("TFrame", background=COLOR_BG)
        style.configure("TLabel", background=COLOR_BG, foreground=COLOR_TEXT, font=base_font)
        style.configure("Title.TLabel", background=COLOR_BG, foreground=COLOR_TEXT, font=(self.font_family, 27, "bold"))
        style.configure("Subtitle.TLabel", background=COLOR_BG, foreground=COLOR_TEXT_SECONDARY, font=(self.font_family, 13))
        style.configure("Status.TLabel", background=COLOR_BG, foreground=COLOR_TEXT_SECONDARY, font=(self.font_family, 10))
        style.configure("Horizontal.TProgressbar", troughcolor=COLOR_BORDER, background=COLOR_ACCENT,
                         bordercolor=COLOR_BG, lightcolor=COLOR_ACCENT, darkcolor=COLOR_ACCENT, thickness=6)

    # ------------------------------------------------------------- Layout
    def _build_layout(self) -> None:
        header = ttk.Frame(self, padding=(32, 28, 32, 16))
        header.pack(fill="x")
        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(header, text="Long-Kandidaten A · S&P 500 & DAX", style="Subtitle.TLabel").pack(anchor="w", pady=(2, 0))

        action = ttk.Frame(self, padding=(32, 0, 32, 20))
        action.pack(fill="x")
        self.start_button = RoundedButton(
            action, text="Scan starten", command=self.start_scan, width=200, height=46, radius=23,
            font=(self.font_family, 13, "bold"),
        )
        self.start_button.pack(side="left")
        progress_wrap = ttk.Frame(action)
        progress_wrap.pack(side="left", fill="x", expand=True, padx=(24, 0))
        self.progress_bar = ttk.Progressbar(progress_wrap, variable=self.progress_var, maximum=100)
        self.status_label = ttk.Label(progress_wrap, textvariable=self.status_var, style="Status.TLabel")

        self.body = ttk.Frame(self, padding=(32, 0, 32, 28))
        self.body.pack(fill="both", expand=True)
        self._show_placeholder("Klicke „Scan starten“, um den S&P 500 und den DAX nach Long-Kandidaten A zu durchsuchen.")

    def _show_placeholder(self, text: str) -> None:
        for widget in self.body.winfo_children():
            widget.destroy()
        wrap = ttk.Frame(self.body)
        wrap.pack(expand=True)
        ttk.Label(wrap, text=text, style="Subtitle.TLabel", wraplength=560, justify="center").pack(pady=120)

    # ------------------------------------------------------------- Ablauf
    def start_scan(self) -> None:
        if self.running:
            return
        self.running = True
        self.start_button.set_enabled(False)
        self.start_button.set_text("Wird analysiert …")
        self.progress_bar.pack(fill="x")
        self.status_label.pack(anchor="w", pady=(4, 0))
        self.progress_var.set(0)
        self.status_var.set("Watchlist wird geladen …")
        self._show_placeholder("Der Scan läuft – das dauert je nach Marktlage einige Minuten.")
        threading.Thread(target=self._run_scan, daemon=True).start()

    def _run_scan(self) -> None:
        try:
            data = self._load_ohlcv()
            self.data = data
            results, errors = [], []
            groups = list(data.groupby("Ticker", sort=False))
            for i, (ticker, group) in enumerate(groups, start=1):
                self.status_var.set(f"Muster erkennen {i}/{len(groups)}: {ticker}")
                self.progress_var.set(45 + i / len(groups) * 55)
                try:
                    result = analyze_ticker(group)
                    if result:
                        results.append(result)
                except Exception as exc:
                    errors.append((ticker, str(exc)))
            if not results:
                raise RuntimeError("Keine auswertbaren Kursdaten gefunden.")
            candidates = self._rank_candidates(pd.DataFrame(results))
            self.candidates = candidates
            self.after(0, lambda: self._on_scan_done(candidates))
        except Exception as exc:
            self.after(0, lambda: self._on_scan_failed(exc))

    def _load_ohlcv(self) -> pd.DataFrame:
        CACHE_DIR.mkdir(exist_ok=True)
        cache_file = CACHE_DIR / "ohlcv.csv"
        if cache_file.exists():
            modified = datetime.fromtimestamp(cache_file.stat().st_mtime).date()
            if modified == date.today():
                self.status_var.set("Heutige Kursdaten aus dem Cache werden verwendet …")
                self.progress_var.set(45)
                return pd.read_csv(cache_file, parse_dates=["Date"])
        self.status_var.set("Aktuelle Indexmitglieder werden geladen …")
        members = merge_member_frames([fetch_index_members("S&P 500"), fetch_index_members("DAX")])
        if members.empty:
            raise ValueError("Keine Indexmitglieder gefunden.")
        tickers = members["Ticker"].tolist()
        metadata = members.set_index("Ticker")[["Name", "Index"]].to_dict("index")
        frames = []
        for i, ticker in enumerate(tickers, start=1):
            self.status_var.set(f"Kursdaten laden {i}/{len(tickers)}: {ticker}")
            self.progress_var.set(i / len(tickers) * 45)
            try:
                df = yf.download(ticker, period="1y", interval="1d", auto_adjust=False, actions=True, progress=False, threads=False)
                if df.empty:
                    continue
                info = metadata.get(ticker, {})
                frames.append(normalize_download_frame(df, ticker, info.get("Name", ticker), info.get("Index", "")))
            except Exception:
                continue
        if not frames:
            raise RuntimeError("Es konnten keine Kursdaten geladen werden.")
        combined = pd.concat(frames, ignore_index=True, sort=False)
        combined.to_csv(cache_file, index=False)
        return combined

    def _rank_candidates(self, results: pd.DataFrame) -> pd.DataFrame:
        volume_pct = results["Ø Aktienvolumen 20T"].rank(pct=True, method="average").fillna(0)
        value_pct = results["Ø Handelswert 20T"].rank(pct=True, method="average").fillna(0)
        results["Liquiditäts-Score"] = ((0.35 * volume_pct + 0.65 * value_pct) * 10).round(1)
        results["Liquidität"] = pd.cut(
            results["Liquiditäts-Score"], bins=[-0.01, 2, 4, 6, 8, 10.01],
            labels=["Sehr niedrig", "Niedrig", "Mittel", "Hoch", "Sehr hoch"],
        ).astype(str)
        crv = pd.to_numeric(results["CRV bis Widerstand"], errors="coerce")
        signal_count = pd.to_numeric(results["Umkehrsignal-Anzahl"], errors="coerce").fillna(0)
        trend_ok = results["Über EMA200"].astype(bool)
        midtrend_ok = results["Über EMA50"].astype(bool) | (results["EMA50 Trend 1M %"] > 0)
        reversal_ok = signal_count >= 1
        not_broken = (~results["Support gebrochen"]) & (results["Abstand Support %"] >= -2.5)
        tradable = results["Liquiditäts-Score"] >= 2.0
        long_signal_ok = results["Signaltyp"] != "Short-Setup"
        eligible_mask = (
            not_broken
            & (results["Support-Tests"] >= 3)
            & results["Abstand Support %"].between(-1.5, 7.0)
            & trend_ok & tradable & long_signal_ok & crv.ge(1.25)
            & (reversal_ok | midtrend_ok)
            & (results["Technischer Score"] >= 58)
        )
        results["Ranking-Score"] = (
            results["Technischer Score"] * 0.82
            + results["Liquiditäts-Score"] * 1.2
            + np.minimum(signal_count, 3) * 2.0
            + np.minimum(results["Support-Tests"], 5) * 0.8
        ).round(1)
        eligible = results[eligible_mask].sort_values("Ranking-Score", ascending=False).reset_index(drop=True)
        a_count = min(12, max(5, math.ceil(len(eligible) * 0.15))) if len(eligible) else 0
        top = eligible.head(a_count).copy()
        if not top.empty:
            top["Nächste Berichtszahlen"] = [self._fetch_next_earnings(t) for t in top["Ticker"]]
        return top

    def _fetch_next_earnings(self, ticker: str) -> date | None:
        self.status_var.set(f"Nächste Berichtszahlen prüfen: {ticker}")
        try:
            calendar = yf.Ticker(ticker).calendar
            raw = calendar.get("Earnings Date") if isinstance(calendar, dict) else None
            if raw is None and hasattr(calendar, "loc"):
                try:
                    raw = calendar.loc["Earnings Date"]
                except Exception:
                    raw = None
            if raw is None:
                return None
            candidates = raw if isinstance(raw, (list, tuple)) else [raw]
            parsed = []
            for value in candidates:
                if value is None:
                    continue
                try:
                    parsed.append(pd.Timestamp(value).date())
                except Exception:
                    continue
            if not parsed:
                return None
            future = sorted(d for d in parsed if d >= date.today())
            return future[0] if future else sorted(parsed)[0]
        except Exception:
            return None

    def _on_scan_failed(self, exc: Exception) -> None:
        self.running = False
        self.start_button.set_enabled(True)
        self.start_button.set_text("Erneut versuchen")
        self.progress_bar.pack_forget()
        self.status_label.pack_forget()
        self._show_placeholder("Beim Scan ist ein Fehler aufgetreten.")
        messagebox.showerror("Fehler", f"{exc}\n\n{traceback.format_exc(limit=2)}")

    def _on_scan_done(self, candidates: pd.DataFrame) -> None:
        self.running = False
        self.start_button.set_enabled(True)
        self.start_button.set_text("Erneut scannen")
        self.progress_bar.pack_forget()
        self.status_label.pack_forget()
        if candidates.empty:
            self._show_placeholder("Aktuell keine Long-Kandidaten A im S&P 500 oder DAX gefunden. Später erneut versuchen.")
            return
        self._render_candidates(candidates)

    # ------------------------------------------------------------- Ergebnis-Tabs
    def _render_candidates(self, candidates: pd.DataFrame) -> None:
        for widget in self.body.winfo_children():
            widget.destroy()
        tab_items = [(row["Ticker"], f"{row['Ticker']} · {row.get('Name', '')}" if row.get("Name") else row["Ticker"])
                     for _, row in candidates.iterrows()]
        tabbar = TabBar(self.body, tab_items, command=self._show_candidate, font=(self.font_family, 12, "bold"))
        tabbar.pack(fill="x", pady=(0, 16))
        scroll_area = ScrollableArea(self.body)
        scroll_area.pack(fill="both", expand=True)
        content = scroll_area.inner
        self._candidate_frames: dict[str, ttk.Frame] = {}
        total = len(candidates)
        for rank, (_, row) in enumerate(candidates.iterrows(), start=1):
            tab = ttk.Frame(content, padding=(0, 0, 0, 0))
            self._build_candidate_tab(tab, row, rank, total)
            self._candidate_frames[row["Ticker"]] = tab
        if tab_items:
            self._show_candidate(tab_items[0][0])

    def _show_candidate(self, ticker: str) -> None:
        for frame in self._candidate_frames.values():
            frame.pack_forget()
        self._candidate_frames[ticker].pack(fill="both", expand=True)

    def _build_candidate_tab(self, parent: ttk.Frame, row: pd.Series, rank: int, total: int) -> None:
        chart_card = tk.Frame(parent, bg=COLOR_CARD, highlightbackground=COLOR_BORDER, highlightthickness=1)
        chart_card.pack(fill="both", expand=True)
        ticker = row["Ticker"]
        group = self.data[self.data["Ticker"] == ticker].sort_values("Date").copy()
        self._draw_candlestick(chart_card, group, row)
        self._build_info_panel(parent, row, rank, total)

    def _draw_candlestick(self, parent: tk.Frame, group: pd.DataFrame, row: pd.Series) -> None:
        g = group.dropna(subset=["Close", "High", "Low"])
        dates = pd.DatetimeIndex(pd.to_datetime(g["Date"]).dt.normalize(), name="Date")
        raw_close = g["Close"].astype(float)
        adj_close = g["Adj Close"].astype(float) if "Adj Close" in g else raw_close
        factor = (adj_close / raw_close.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).fillna(1)
        full = pd.DataFrame({
            "Open": g["Open"].astype(float).values * factor.values,
            "High": g["High"].astype(float).values * factor.values,
            "Low": g["Low"].astype(float).values * factor.values,
            "Close": adj_close.values,
        }, index=dates)
        full = full[~full.index.duplicated(keep="last")].sort_index()
        # EMA über die komplette Historie berechnen (wie in analyze_ticker), nicht nur über
        # das Anzeigefenster – sonst weichen die im Chart gezeichneten EMA-Linien von den
        # EMA-Werten ab, die für "Über EMA50/200" im Kriterientext verwendet werden.
        # EMA50/EMA200 auf Basis des Schlusskurses, wie bei TradingView.
        ema50_full = full["Close"].ewm(span=50, adjust=False).mean()
        ema200_full = full["Close"].ewm(span=200, adjust=False).mean()
        chart_df = full.tail(180)
        ema50 = ema50_full.reindex(chart_df.index)
        ema200 = ema200_full.reindex(chart_df.index)

        marketcolors = mpf.make_marketcolors(
            up=COLOR_GREEN, down=COLOR_RED, edge={"up": COLOR_GREEN, "down": COLOR_RED},
            wick={"up": COLOR_GREEN, "down": COLOR_RED},
        )
        style = mpf.make_mpf_style(
            marketcolors=marketcolors, facecolor=COLOR_CARD, figcolor=COLOR_CARD, edgecolor=COLOR_CARD,
            gridcolor=COLOR_BORDER, gridstyle=":",
            rc={
                "font.family": self.font_family, "axes.edgecolor": COLOR_BORDER,
                "axes.labelcolor": COLOR_TEXT_SECONDARY, "xtick.color": COLOR_TEXT_SECONDARY,
                "ytick.color": COLOR_TEXT_SECONDARY, "axes.titlesize": 13, "axes.titleweight": "bold",
                "axes.titlecolor": COLOR_TEXT, "axes.titlelocation": "left", "axes.titlepad": 12,
            },
        )
        addplots = [
            mpf.make_addplot(ema50, color=COLOR_ACCENT, width=1.4),
            mpf.make_addplot(ema200, color="#8fb4d9", width=1.4),
        ]
        name = row.get("Name", "")
        chart_title = f"{row['Ticker']} · {name}" if name else str(row["Ticker"])
        fig, axlist = mpf.plot(
            chart_df, type="candle", style=style, addplot=addplots, returnfig=True,
            volume=False, figsize=(7.4, 4.9), tight_layout=True, datetime_format="%d.%m.", xrotation=0,
            title=chart_title,
        )
        axis = axlist[0]

        pos_of = {d.strftime("%Y-%m-%d"): i for i, d in enumerate(chart_df.index)}
        last_pos = len(chart_df) - 1

        def pos(date_str):
            return pos_of.get(date_str)

        if pd.notna(row.get("Supportzone unten")) and pd.notna(row.get("Supportzone oben")):
            axis.axhspan(float(row["Supportzone unten"]), float(row["Supportzone oben"]), color=COLOR_GREEN, alpha=0.12, zorder=0)
        if pd.notna(row.get("Stop-Idee")):
            axis.axhline(float(row["Stop-Idee"]), linestyle=(0, (2, 2)), linewidth=1.2, color=COLOR_RED, alpha=0.85)
        if pd.notna(row.get("Exit-Ziel")):
            axis.axhline(float(row["Exit-Ziel"]), linestyle=(0, (4, 2)), linewidth=1.2, color=COLOR_GREEN, alpha=0.85)
        elif pd.notna(row.get("Nächster Widerstand")):
            axis.axhline(float(row["Nächster Widerstand"]), linestyle="--", linewidth=1, color=COLOR_TEXT_SECONDARY, alpha=0.8)

        try:
            overlay = json.loads(row.get("Chart-Overlay") or "{}")
        except (TypeError, ValueError):
            overlay = {}

        def extend(p1, p2):
            x1, y1 = p1
            x2, y2 = p2
            if x2 == x1:
                return [x1, x2], [y1, y2]
            slope = (y2 - y1) / (x2 - x1)
            if last_pos > x2:
                return [x1, last_pos], [y1, y2 + slope * (last_pos - x2)]
            return [x1, x2], [y1, y2]

        kanal_oben = overlay.get("kanal_oben")
        if kanal_oben and all(d for d, _ in kanal_oben):
            p = [(pos(d), v) for d, v in kanal_oben]
            if all(x is not None for x, _ in p):
                lx, ly = extend(p[0], p[1])
                axis.plot(lx, ly, linestyle="--", linewidth=1.2, color="#ff9f0a", alpha=0.9, zorder=2)
        kanal_unten = overlay.get("kanal_unten")
        if kanal_unten and all(d for d, _ in kanal_unten):
            p = [(pos(d), v) for d, v in kanal_unten]
            if all(x is not None for x, _ in p):
                lx, ly = extend(p[0], p[1])
                axis.plot(lx, ly, linestyle="--", linewidth=1.2, color="#ffb340", alpha=0.9, zorder=2)

        sks = overlay.get("sks")
        if sks:
            pts = [(pos(d), v, lbl) for d, v, lbl in sks.get("punkte", []) if pos(d) is not None]
            if pts:
                sx, sy, _ = zip(*pts)
                axis.plot(sx, sy, linewidth=1, color="#af52de", marker="^", markersize=6, zorder=4)
                for x, y, lbl in pts:
                    axis.annotate(lbl, (x, y), textcoords="offset points", xytext=(0, 8), fontsize=7.5, color="#af52de", ha="center")
            neck = [(pos(d), v) for d, v in sks.get("neckline_punkte", []) if pos(d) is not None]
            if len(neck) == 2:
                lx, ly = extend(neck[0], neck[1])
                axis.plot(lx, ly, linestyle="-.", linewidth=1.2, color="#af52de", zorder=2)

        elliott = overlay.get("elliott")
        if elliott:
            pts = [(pos(d), v, lbl) for d, v, lbl in elliott.get("punkte", []) if pos(d) is not None]
            if pts:
                ex_, ey_, _ = zip(*pts)
                axis.plot(ex_, ey_, linestyle=":", linewidth=1.1, color=COLOR_ACCENT, marker="s", markersize=5, zorder=4)
                for x, y, lbl in pts:
                    axis.annotate(lbl, (x, y), textcoords="offset points", xytext=(0, -13), fontsize=7.5, color=COLOR_ACCENT, ha="center", fontweight="bold")

        breakout_info = overlay.get("breakout")
        if breakout_info and breakout_info.get("datum") and pos(breakout_info["datum"]) is not None:
            axis.plot([pos(breakout_info["datum"])], [breakout_info["level"]], marker="*", markersize=14, color=COLOR_TEXT, zorder=5)

        canvas = FigureCanvasTkAgg(fig, master=parent)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True, padx=1, pady=1)
        plt.close(fig)

    def _build_info_panel(self, parent: ttk.Frame, row: pd.Series, rank: int, total: int) -> None:
        panel = tk.Frame(parent, bg=COLOR_CARD, highlightbackground=COLOR_BORDER, highlightthickness=1)
        panel.pack(fill="x", pady=(14, 0))
        inner = tk.Frame(panel, bg=COLOR_CARD)
        inner.pack(fill="x", padx=20, pady=16)

        header = tk.Frame(inner, bg=COLOR_CARD)
        header.pack(fill="x")
        left = tk.Frame(header, bg=COLOR_CARD)
        left.pack(side="left")
        tk.Label(left, text=row["Ticker"], bg=COLOR_CARD, fg=COLOR_TEXT, font=(self.font_family, 19, "bold")).pack(side="left")
        tk.Label(left, text=f"  {row.get('Name', '')}", bg=COLOR_CARD, fg=COLOR_TEXT_SECONDARY, font=(self.font_family, 13)).pack(side="left")
        ticker = row["Ticker"]
        link = tk.Label(left, text="  ↗ TradingView", bg=COLOR_CARD, fg=COLOR_ACCENT, font=(self.font_family, 11, "underline"), cursor="hand2")
        link.pack(side="left", padx=(6, 0))
        link.bind("<Button-1>", lambda _e, t=ticker: webbrowser.open(tradingview_url(t)))

        self._chip(header, f"Kandidat · Rang {rank}/{total}", COLOR_BLUE_BG, COLOR_ACCENT, side="right")

        stats = tk.Frame(inner, bg=COLOR_CARD)
        stats.pack(fill="x", pady=(14, 0))
        self._stat_tile(stats, "Score", f"{row['Technischer Score']:.0f}")
        crv = row.get("CRV bis Widerstand")
        self._stat_tile(stats, "CRV", f"{float(crv):.1f}" if pd.notna(crv) else "–", COLOR_GREEN)
        if pd.notna(row.get("RSI14")):
            self._stat_tile(stats, "RSI", f"{float(row['RSI14']):.0f}")
        self._stat_tile(stats, "Liquidität", str(row.get("Liquidität", "")))

        bullets = tk.Frame(inner, bg=COLOR_CARD)
        bullets.pack(fill="x", pady=(16, 0), anchor="w")
        for line in self._criteria_lines(row):
            bullet_row = tk.Frame(bullets, bg=COLOR_CARD)
            bullet_row.pack(fill="x", pady=2, anchor="w")
            tk.Label(bullet_row, text="●", bg=COLOR_CARD, fg=COLOR_GREEN, font=(self.font_family, 7)).pack(side="left", padx=(0, 8), anchor="n", pady=(4, 0))
            tk.Label(bullet_row, text=line, bg=COLOR_CARD, fg=COLOR_TEXT, font=(self.font_family, 11),
                     anchor="w", justify="left", wraplength=940).pack(side="left", fill="x")

        trade = tk.Frame(inner, bg=COLOR_CARD)
        trade.pack(fill="x", pady=(16, 0))
        entry = float(row.get("Entry-Idee", row.get("Kurs", np.nan)))
        stop, ziel = row.get("Stop-Idee"), row.get("Exit-Ziel")
        self._stat_tile(trade, "Entry", f"{entry:.2f}")
        if pd.notna(stop):
            self._stat_tile(trade, "Stop", f"{float(stop):.2f}", COLOR_RED)
        if pd.notna(ziel):
            self._stat_tile(trade, "Ziel", f"{float(ziel):.2f}", COLOR_GREEN)

        tk.Label(
            inner, text="Vorauswahl, keine Kauf-/Verkaufsempfehlung. Stop-Loss und Exit-Order manuell beim Broker setzen.",
            bg=COLOR_CARD, fg=COLOR_TEXT_SECONDARY, font=(self.font_family, 9, "italic"),
        ).pack(anchor="w", pady=(14, 0))

    def _chip(self, parent: tk.Frame, text: str, bg: str, fg: str, side: str = "left") -> None:
        tk.Label(parent, text=text, bg=bg, fg=fg, font=(self.font_family, 10, "bold"), padx=10, pady=4).pack(side=side, padx=(0, 8))

    def _stat_tile(self, parent: tk.Frame, label: str, value: str, value_color: str | None = None) -> None:
        tile = tk.Frame(parent, bg=COLOR_CARD, highlightbackground=COLOR_BORDER, highlightthickness=1)
        tile.pack(side="left", padx=(0, 10))
        inner = tk.Frame(tile, bg=COLOR_CARD)
        inner.pack(padx=14, pady=8)
        tk.Label(inner, text=label, bg=COLOR_CARD, fg=COLOR_TEXT_SECONDARY, font=(self.font_family, 9)).pack(anchor="w")
        tk.Label(inner, text=value, bg=COLOR_CARD, fg=value_color or COLOR_TEXT, font=(self.font_family, 15, "bold")).pack(anchor="w")

    @staticmethod
    def _criteria_lines(row: pd.Series) -> list[str]:
        lines = []
        if pd.notna(row.get("Support")):
            lines.append(
                f"Support {float(row['Support']):.2f} bereits {int(row['Support-Tests'])}× getestet, "
                f"zuletzt vor {int(row['Letzter Test vor Tagen'])} Tagen ({float(row['Abstand Support %']):+.1f}% vom aktuellen Kurs)"
            )
        trend_bits = [name for name, ok in (("EMA50", row.get("Über EMA50")), ("EMA200", row.get("Über EMA200"))) if ok]
        if trend_bits:
            lines.append("Aufwärtstrend bestätigt: Kurs über " + " & ".join(trend_bits))
        if row.get("Umkehrsignale") and row["Umkehrsignale"] != "Keine":
            lines.append(f"Umkehrsignale: {row['Umkehrsignale']}")
        if row.get("Erkannte Muster") and row["Erkannte Muster"] != "Keine":
            lines.append(f"Chartmuster: {row['Erkannte Muster']}")
        if row.get("Ausbruch erkannt") and row["Ausbruch erkannt"] != "Nein":
            lines.append(f"Ausbruch: {row['Ausbruch erkannt']}")
        earnings = row.get("Nächste Berichtszahlen")
        if pd.notna(earnings):
            tage = (earnings - date.today()).days
            lines.append(f"Nächste Quartals-/Jahreszahlen: {earnings.strftime('%d.%m.%Y')} (in {tage} Tagen)")
        if not lines:
            lines.append("Technische Kennzahlen erfüllen die A-Kandidat-Kriterien.")
        return lines


if __name__ == "__main__":
    AktienScreenerApp().mainloop()
