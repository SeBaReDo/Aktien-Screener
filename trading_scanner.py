from __future__ import annotations

import csv
import json
import math
import re
import threading
import traceback
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from io import BytesIO, StringIO
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import numpy as np
import pandas as pd
import requests
import yfinance as yf
from scipy.signal import find_peaks

APP_TITLE = "Aktien App v8 – Marktsituation, Asset-Screener, Entry & Risiko"
APP_DIR = Path(__file__).resolve().parent
CONFIG_DIR = APP_DIR / "marktdaten"

INDEX_SOURCES = {
    "S&P 500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "NASDAQ-100": "https://en.wikipedia.org/wiki/Nasdaq-100",
    "DAX": "https://en.wikipedia.org/wiki/DAX",
}
STANDARD_FIELDS = {"Open", "High", "Low", "Close", "Adj Close", "Volume", "Dividends", "Stock Splits", "Capital Gains"}

AMPEL_FARBEN = {
    "rot": "#f8d7da",
    "gelb": "#fff3cd",
    "grün": "#d4edda",
    "neutral": "#e2e3e5",
}
AMPEL_TEXTFARBEN = {
    "rot": "#842029",
    "gelb": "#664d03",
    "grün": "#0f5132",
    "neutral": "#41464b",
}

# ---------------------------------------------------------------------------
# Konzept-Grundlage: Projekte/Finanzen/Aktien/Aktien App.md (Vault, Sebastian)
#   Schritt 1: Marktsituation analysieren (Zinsen, Kriege, Wahlen, Berichtssaison,
#              Splits/Dividenden, Jahreszyklus, Inflation, Dollar)
#   Schritt 2: Asset-Screener (Kopf-Schulter, Kanal/Widerstand mehrfach getestet,
#              Elliott-Wellen ABC, Ausbruch, Long/Short-Einstufung)
#   Schritt 3: Entry / Chancen-Risiken (Stop-Loss, Exit, Verkaufsorder-Erinnerung)
# Diese drei Schritte bilden die drei Haupt-Tabs der App.
# ---------------------------------------------------------------------------


# =============================================================================
# Allgemeine Hilfsfunktionen (Ticker/Watchlist/Downloads)
# =============================================================================

def normalize_ticker(raw: str) -> str:
    value = str(raw).strip().upper().strip('"\'')
    if not value:
        return ""
    value = re.split(r"[;,#\t ]", value, maxsplit=1)[0].strip()
    if ":" in value:
        value = value.split(":", 1)[1]
    return value


def read_watchlist(path: Path) -> list[str]:
    if not path.exists():
        return []
    candidates: list[str] = []
    if path.suffix.lower() == ".txt":
        candidates = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    else:
        try:
            df = pd.read_csv(path, sep=None, engine="python", dtype=str)
            preferred = ["yahooticker", "yahoo ticker", "ticker", "symbol", "symbols", "instrument", "code"]
            columns = {str(c).strip().lower(): c for c in df.columns}
            chosen = next((columns[n] for n in preferred if n in columns), df.columns[0])
            candidates = df[chosen].dropna().astype(str).tolist()
        except Exception:
            with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
                candidates = [row[0] for row in csv.reader(handle) if row]
    result, seen = [], set()
    for item in candidates:
        ticker = normalize_ticker(item)
        if ticker and ticker not in {"TICKER", "SYMBOL", "INSTRUMENT", "CODE"} and ticker not in seen:
            result.append(ticker)
            seen.add(ticker)
    return result


def merge_unique(*groups: list[str]) -> list[str]:
    result, seen = [], set()
    for group in groups:
        for raw in group:
            ticker = normalize_ticker(raw)
            if ticker and ticker not in seen:
                result.append(ticker)
                seen.add(ticker)
    return result


def _download_html_tables(url: str) -> list[pd.DataFrame]:
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0 Trading-Support-Scanner"}, timeout=30)
    response.raise_for_status()
    return pd.read_html(StringIO(response.text))


def _column_name(column: object) -> str:
    if isinstance(column, tuple):
        return " ".join(str(x) for x in column if str(x) != "nan").strip().lower()
    return str(column).strip().lower()


def _china_yahoo_ticker(code: str) -> str:
    """Convert a mainland China A-share code to Yahoo Finance format."""
    clean = re.sub(r"\D", "", str(code)).zfill(6)
    if clean.startswith(("5", "6", "9")):
        return clean + ".SS"
    if clean.startswith(("0", "1", "2", "3")):
        return clean + ".SZ"
    if clean.startswith(("4", "8")):
        return clean + ".BJ"
    return ""


def _members_frame(tickers: list[str], names: list[str], index_name: str) -> pd.DataFrame:
    frame = pd.DataFrame({"Ticker": tickers, "Name": names})
    frame["Ticker"] = frame["Ticker"].astype(str).map(normalize_ticker)
    frame["Name"] = frame["Name"].fillna("").astype(str).str.strip()
    frame["Index"] = index_name
    return frame[frame["Ticker"] != ""].drop_duplicates("Ticker").reset_index(drop=True)


def fetch_csi300_members() -> pd.DataFrame:
    """Load CSI 300 members with names. Online source first, bundled fallback second."""
    errors = []
    urls = [
        "https://chinaamc.com.hk/wp-content/uploads/chinaamc/holdings/CSI300_EN.xlsx",
        "https://www.csindex.com.cn/uploads/file/autofile/cons/000300cons.xls",
    ]
    for url in urls:
        try:
            response = requests.get(url, headers={"User-Agent": "Mozilla/5.0 Trading-Support-Scanner"}, timeout=40)
            response.raise_for_status()
            content = response.content
            sheets = pd.read_excel(BytesIO(content), sheet_name=None, header=None, dtype=str)
            for raw in sheets.values():
                header_row = None
                for i in range(min(20, len(raw))):
                    vals = [str(x).strip().lower() for x in raw.iloc[i].tolist()]
                    if "name" in vals and ("ticker" in vals or "code" in vals):
                        header_row = i
                        break
                if header_row is not None:
                    table = pd.read_excel(BytesIO(content), header=header_row, dtype=str)
                    cols = {_column_name(c): c for c in table.columns}
                    name_col = next((c for n, c in cols.items() if n == "name" or "security name" in n), None)
                    code_col = next((c for n, c in cols.items() if n in {"ticker", "code"} or "security code" in n), None)
                    if name_col is not None and code_col is not None:
                        codes = table[code_col].astype(str).str.extract(r"(\d{6})")[0]
                        names = table[name_col].astype(str)
                        pairs = [(c, n) for c, n in zip(codes, names) if isinstance(c, str) and len(c) == 6]
                        if len(pairs) >= 250:
                            tickers = [_china_yahoo_ticker(c) for c, _ in pairs]
                            return _members_frame(tickers, [n for _, n in pairs], "CSI 300")
                for code_idx in range(raw.shape[1]):
                    codes = raw.iloc[:, code_idx].astype(str).str.extract(r"^(\d{6})$")[0]
                    if codes.notna().sum() >= 250:
                        name_idx = code_idx + 1 if code_idx + 1 < raw.shape[1] else max(0, code_idx - 1)
                        valid = codes.notna()
                        pairs = list(zip(codes[valid], raw.loc[valid, name_idx].astype(str)))
                        tickers = [_china_yahoo_ticker(c) for c, _ in pairs]
                        return _members_frame(tickers, [n for _, n in pairs], "CSI 300")
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    fallback = APP_DIR / "csi300_fallback.csv"
    if fallback.exists():
        table = pd.read_csv(fallback, dtype=str)
        tickers = [_china_yahoo_ticker(c) for c in table["Code"]]
        return _members_frame(tickers, table["Name"].tolist(), "CSI 300")
    raise ValueError("CSI-300-Liste konnte nicht geladen werden. " + " | ".join(errors))


def fetch_index_members(index_name: str) -> pd.DataFrame:
    if index_name == "CSI 300":
        return fetch_csi300_members()
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
    if index_name == "NASDAQ-100":
        for table in tables:
            columns = {_column_name(c): c for c in table.columns}
            symbol_col = next((c for n, c in columns.items() if "ticker" in n or n == "symbol"), None)
            name_col = next((c for n, c in columns.items() if "company" in n or n == "name"), None)
            if symbol_col is not None and 90 <= len(table) <= 120:
                mask = table[symbol_col].astype(str).str.fullmatch(r"[A-Za-z0-9.\-]+", na=False)
                tickers = [str(x).strip().upper().replace(".", "-") for x in table.loc[mask, symbol_col]]
                names = table.loc[mask, name_col].astype(str).tolist() if name_col is not None else tickers
                if len(tickers) >= 90:
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
# SCHRITT 1 – Marktsituation analysieren
# (Zinsentscheidungen, Kriege, Wahlen, Berichtssaison, Splits/Dividenden,
#  Jahreszyklus, Inflation, Dollar-Stand)
# =============================================================================

def _load_json(name: str, default: dict) -> dict:
    path = CONFIG_DIR / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def fetch_fred_series(series_id: str, lookback_days: int = 420) -> pd.Series:
    """Lädt eine FRED-Zeitreihe über den öffentlichen fredgraph.csv-Export (kein API-Key nötig)."""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    response = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 Aktien-App"})
    response.raise_for_status()
    frame = pd.read_csv(StringIO(response.text))
    frame.columns = ["Date", series_id]
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    frame[series_id] = pd.to_numeric(frame[series_id], errors="coerce")
    frame = frame.dropna().set_index("Date")[series_id].sort_index()
    if frame.empty:
        raise ValueError(f"FRED-Serie {series_id} lieferte keine Werte.")
    cutoff = frame.index.max() - pd.Timedelta(days=lookback_days)
    return frame[frame.index >= cutoff]


def analyze_fed_rate() -> dict:
    try:
        series = fetch_fred_series("DFF", lookback_days=200)
        current = float(series.iloc[-1])
        older = series[series.index <= series.index.max() - pd.Timedelta(days=90)]
        change = current - float(older.iloc[-1]) if len(older) else 0.0
        trend = "steigend" if change > 0.1 else "fallend" if change < -0.1 else "stabil"
        status = "grün" if trend != "steigend" else ("gelb" if change <= 0.5 else "rot")
        return {
            "label": "US-Leitzins (Fed Funds Rate)",
            "value": f"{current:.2f} % – {trend} ({change:+.2f} Pp / 3 Monate)",
            "status": status,
            "source": "FRED, Serie DFF (fred.stlouisfed.org)",
        }
    except Exception as exc:
        return {
            "label": "US-Leitzins (Fed Funds Rate)",
            "value": "nicht abrufbar – manuell prüfen",
            "status": "gelb",
            "source": "https://fred.stlouisfed.org/series/DFF",
            "error": str(exc),
        }


def analyze_ecb_rate() -> dict:
    try:
        url = "https://sdw-wsrest.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.DFR.LEV?format=csvdata&lastNObservations=120"
        response = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 Aktien-App"})
        response.raise_for_status()
        frame = pd.read_csv(StringIO(response.text))
        frame["TIME_PERIOD"] = pd.to_datetime(frame["TIME_PERIOD"], errors="coerce")
        frame = frame.dropna(subset=["TIME_PERIOD", "OBS_VALUE"]).sort_values("TIME_PERIOD")
        current = float(frame["OBS_VALUE"].iloc[-1])
        older = frame[frame["TIME_PERIOD"] <= frame["TIME_PERIOD"].max() - pd.Timedelta(days=90)]
        change = current - float(older["OBS_VALUE"].iloc[-1]) if len(older) else 0.0
        trend = "steigend" if change > 0.05 else "fallend" if change < -0.05 else "stabil"
        status = "grün" if trend != "steigend" else "gelb"
        return {
            "label": "EZB-Einlagensatz",
            "value": f"{current:.2f} % – {trend} ({change:+.2f} Pp / 3 Monate)",
            "status": status,
            "source": "ECB Statistical Data Warehouse (FM.D.U2.EUR.4F.KR.DFR.LEV)",
        }
    except Exception as exc:
        return {
            "label": "EZB-Einlagensatz",
            "value": "nicht abrufbar – manuell prüfen",
            "status": "gelb",
            "source": "https://www.ecb.europa.eu/stats/",
            "error": str(exc),
        }


def analyze_boj_rate(cb_config: dict) -> dict:
    info = cb_config.get("boj_rate", {})
    wert = info.get("wert")
    if wert is None:
        return {"label": "BoJ-Leitzins", "value": "nicht gepflegt", "status": "neutral", "source": info.get("quelle", "")}
    return {
        "label": "BoJ-Leitzins",
        "value": f"{wert:.2f} % (Stand {info.get('stand', '?')}, manuell gepflegt – keine stabile freie Live-API)",
        "status": "neutral",
        "source": info.get("quelle", "https://www.boj.or.jp/en/mopo/"),
    }


def analyze_us_inflation() -> dict:
    try:
        series = fetch_fred_series("CPIAUCSL", lookback_days=800)
        monthly = series.resample("MS").last().dropna()
        yoy = (monthly / monthly.shift(12) - 1) * 100
        yoy = yoy.dropna()
        current = float(yoy.iloc[-1])
        status = "grün" if 1.5 <= current <= 3.0 else ("rot" if current > 4.0 or current < 0 else "gelb")
        return {
            "label": "US-Inflation (CPI, Jahresrate)",
            "value": f"{current:.1f} % ggü. Vorjahr",
            "status": status,
            "source": "FRED, Serie CPIAUCSL",
        }
    except Exception as exc:
        return {
            "label": "US-Inflation (CPI, Jahresrate)",
            "value": "nicht abrufbar – manuell prüfen",
            "status": "gelb",
            "source": "https://fred.stlouisfed.org/series/CPIAUCSL",
            "error": str(exc),
        }


def analyze_yield_curve() -> dict:
    try:
        s10 = fetch_fred_series("DGS10", lookback_days=30)
        s2 = fetch_fred_series("DGS2", lookback_days=30)
        spread = float(s10.iloc[-1]) - float(s2.iloc[-1])
        status = "grün" if spread > 0.25 else ("rot" if spread < -0.10 else "gelb")
        note = "invertiert – klassisches Rezessionswarnsignal" if spread < 0 else "normal (positiv)"
        return {
            "label": "US-Zinskurve 10J–2J",
            "value": f"{spread:+.2f} Pp – {note}",
            "status": status,
            "source": "FRED, Serien DGS10 / DGS2",
        }
    except Exception as exc:
        return {
            "label": "US-Zinskurve 10J–2J",
            "value": "nicht abrufbar – manuell prüfen",
            "status": "gelb",
            "source": "https://fred.stlouisfed.org/series/T10Y2Y",
            "error": str(exc),
        }


def analyze_dollar_index() -> dict:
    try:
        hist = yf.download("DX-Y.NYB", period="2mo", interval="1d", progress=False, auto_adjust=False, timeout=15)
        if hist is None or hist.empty:
            raise ValueError("Keine Kursdaten für DX-Y.NYB erhalten.")
        close = hist["Close"].dropna()
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        if close.empty:
            raise ValueError("Keine Kursdaten für DX-Y.NYB erhalten.")
        current = float(close.iloc[-1])
        ref_idx = max(0, len(close) - 22)
        month_ago = float(close.iloc[ref_idx])
        change = (current / month_ago - 1) * 100 if month_ago else 0.0
        status = "grün" if abs(change) < 1.5 else ("rot" if abs(change) > 3 else "gelb")
        richtung = "stärker" if change > 0 else "schwächer" if change < 0 else "unverändert"
        return {
            "label": "US-Dollar-Index (DXY)",
            "value": f"{current:.1f} Punkte – {richtung} ({change:+.1f} % / 1 Monat)",
            "status": status,
            "source": "Yahoo Finance, Ticker DX-Y.NYB",
        }
    except Exception as exc:
        return {
            "label": "US-Dollar-Index (DXY)",
            "value": "nicht abrufbar – manuell prüfen",
            "status": "gelb",
            "source": "https://finance.yahoo.com/quote/DX-Y.NYB/",
            "error": str(exc),
        }


def analyze_central_bank_calendar(cb_config: dict) -> list[dict]:
    today = date.today()
    rows = []
    for key, label, source_key in [("fed", "Fed (USA)", "quelle_fed"), ("ezb", "EZB", "quelle_ezb"), ("boj", "BoJ (Japan)", "quelle_boj")]:
        raw_dates = cb_config.get(key, [])
        parsed = []
        for value in raw_dates:
            try:
                parsed.append(datetime.strptime(value, "%Y-%m-%d").date())
            except ValueError:
                continue
        future = sorted(d for d in parsed if d >= today)
        if not future:
            continue
        days = (future[0] - today).days
        status = "rot" if days <= 3 else ("gelb" if days <= 14 else "grün")
        rows.append({
            "label": f"Nächste Zinssitzung: {label}",
            "value": f"{future[0].isoformat()} (in {days} Tagen)",
            "status": status,
            "source": cb_config.get(source_key, ""),
        })
    return rows


def analyze_elections(election_config: dict, max_items: int = 3) -> list[dict]:
    today = date.today()
    entries = []
    for item in election_config.get("termine", []):
        try:
            entries.append((datetime.strptime(item["datum"], "%Y-%m-%d").date(), item))
        except (KeyError, ValueError):
            continue
    upcoming = sorted((d, item) for d, item in entries if d >= today)[:max_items]
    rows = []
    for d, item in upcoming:
        days = (d - today).days
        status = "rot" if days <= 3 else ("gelb" if days <= 14 else "grün")
        rows.append({
            "label": f"Wahl: {item.get('land', '?')} – {item.get('typ', '')}",
            "value": f"{d.isoformat()} (in {days} Tagen)",
            "status": status,
            "source": election_config.get("quelle", ""),
        })
    if not rows:
        rows.append({"label": "Wahlen", "value": "keine markt­relevante Wahl in der gepflegten Liste anstehend", "status": "grün", "source": election_config.get("quelle", "")})
    return rows


EARNINGS_SEASON_WINDOWS = [
    ((1, 10), (2, 20)),   # Q4-Berichtssaison
    ((4, 10), (5, 20)),   # Q1-Berichtssaison
    ((7, 10), (8, 20)),   # Q2-Berichtssaison
    ((10, 10), (11, 20)), # Q3-Berichtssaison
]


def analyze_earnings_season() -> dict:
    today = date.today()
    for (m1, d1), (m2, d2) in EARNINGS_SEASON_WINDOWS:
        start, end = date(today.year, m1, d1), date(today.year, m2, d2)
        if start <= today <= end:
            return {
                "label": "Berichtssaison",
                "value": f"aktiv – erhöhte Einzeltitel-Volatilität rund um Quartalszahlen (ca. bis {end.isoformat()})",
                "status": "gelb",
                "source": "Kalender-Heuristik (typische Quartalsberichtsfenster)",
            }
    starts = [date(today.year if m1 >= today.month else today.year + 1, m1, d1) for (m1, d1), _ in EARNINGS_SEASON_WINDOWS]
    next_start = min(s for s in starts if s >= today) if any(s >= today for s in starts) else min(starts)
    return {
        "label": "Berichtssaison",
        "value": f"aktuell nicht aktiv – nächste ab ca. {next_start.isoformat()}",
        "status": "grün",
        "source": "Kalender-Heuristik (typische Quartalsberichtsfenster)",
    }


def analyze_seasonality() -> dict:
    today = date.today()
    notes = []
    if date(today.year, 5, 1) <= today <= date(today.year, 10, 31):
        notes.append("„Sell in May“ – saisonal historisch schwächere Phase (Mai–Oktober)")
        status = "gelb"
    else:
        notes.append("saisonal historisch stärkere „Winterrallye“-Phase (November–April)")
        status = "grün"
    if date(today.year, 12, 15) <= today <= date(today.year, 12, 31):
        notes.append("Santa-Claus-Rally-Fenster (letzte Handelstage im Dezember)")
    if today.month == 1 and today.day <= 15:
        notes.append("Januar-Effekt-Fenster (Nebenwerte historisch stärker)")
    cycle_index = (today.year - 2024) % 4
    cycle_label = {0: "Wahljahr", 1: "Nachwahljahr", 2: "Midterm-Jahr", 3: "Vorwahljahr"}.get(cycle_index, "")
    if cycle_label:
        extra = " – historisch oft volatiler mit stärkerem Jahresendspurt" if cycle_label == "Midterm-Jahr" else ""
        notes.append(f"US-Präsidentschaftszyklus: {cycle_label} {today.year}{extra}")
    return {
        "label": "Jahreszyklus / Saisonalität",
        "value": " | ".join(notes),
        "status": status,
        "source": "Saisonale Kalenderregeln (heuristisch, keine Garantie)",
    }


CONFLICT_KEYWORDS = [
    "krieg", "angriff", "offensive", "invasion", "konflikt", "waffenruhe", "eskalation",
    "truppen", "raketen", "luftangriff", "militär", "sanktionen", "frontlinie", "geiseln",
    "drohnen", "besatzung", "bombardierung",
]
CONFLICT_FEEDS = [
    "https://www.tagesschau.de/ausland/index~rss2.xml",
    "https://feeds.bbci.co.uk/news/world/rss.xml",
]


def fetch_conflict_headlines(max_items: int = 6) -> tuple[list[str], str]:
    for url in CONFLICT_FEEDS:
        try:
            response = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0 Aktien-App"})
            response.raise_for_status()
            root = ET.fromstring(response.content)
            items = []
            for item in root.iter("item"):
                title_el = item.find("title")
                title = (title_el.text or "").strip() if title_el is not None else ""
                if title and any(keyword in title.lower() for keyword in CONFLICT_KEYWORDS):
                    items.append(title)
                if len(items) >= max_items:
                    break
            if items:
                return items, url
        except Exception:
            continue
    return [], ""


def analyze_conflicts(conflict_config: dict) -> dict:
    baseline = conflict_config.get("konflikte", [])
    headlines, source = fetch_conflict_headlines()
    status = "rot" if len(headlines) >= 4 else "gelb"
    value = f"{len(baseline)} dauerhafte Risikoherde beobachtet"
    if headlines:
        value += f" | {len(headlines)} aktuelle Schlagzeilen mit Konfliktbezug"
    elif not source:
        value += " | aktuelle Schlagzeilen nicht abrufbar"
    return {
        "label": "Kriege / geopolitische Konflikte",
        "value": value,
        "status": status,
        "source": source or conflict_config.get("quelle", ""),
        "details": [f"{item['name']} – {item['relevanz']}" for item in baseline] + headlines,
    }


def fetch_market_situation() -> list[dict]:
    """Sammelt alle Kernpunkte aus Schritt 1 des Konzepts (Marktsituation)."""
    cb_config = _load_json("zentralbank_termine.json", {})
    election_config = _load_json("wahltermine.json", {})
    conflict_config = _load_json("konflikte_basis.json", {})

    items: list[dict] = []
    items.append(analyze_fed_rate())
    items.append(analyze_ecb_rate())
    items.append(analyze_boj_rate(cb_config))
    items.extend(analyze_central_bank_calendar(cb_config))
    items.append(analyze_us_inflation())
    items.append(analyze_yield_curve())
    items.append(analyze_dollar_index())
    items.extend(analyze_elections(election_config))
    items.append(analyze_earnings_season())
    items.append(analyze_seasonality())
    items.append(analyze_conflicts(conflict_config))
    items.append({
        "label": "Aktiensplits / Dividenden",
        "value": "wird je Kandidat im Asset-Screener (Schritt 2) aus den Kursdaten geprüft",
        "status": "neutral",
        "source": "lokale Kursdaten (Yahoo Finance Corporate Actions)",
    })
    return items


def market_situation_summary(items: list[dict]) -> tuple[str, str]:
    counts = {"rot": 0, "gelb": 0, "grün": 0, "neutral": 0}
    for item in items:
        counts[item.get("status", "neutral")] = counts.get(item.get("status", "neutral"), 0) + 1
    if counts["rot"] >= 3:
        overall, status = "Erhöhte Vorsicht – mehrere belastende Faktoren gleichzeitig", "rot"
    elif counts["rot"] >= 1 or counts["gelb"] >= 4:
        overall, status = "Gemischtes Bild – einzelne Risikofaktoren im Blick behalten", "gelb"
    else:
        overall, status = "Kein akuter Belastungsfaktor erkennbar", "grün"
    text = f"{overall}  ({counts['rot']} rot / {counts['gelb']} gelb / {counts['grün']} grün / {counts['neutral']} neutral)"
    return text, status


# =============================================================================
# SCHRITT 2 – Asset-Screener: Support/Widerstand, Chartmuster, Ausbruch
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


def resistance_level(high: pd.Series, atr: pd.Series, current: float, lookback: int = 180) -> tuple[float, int]:
    return find_level(high, atr, current, "above", lookback)


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
    man einen Kanal auch manuell in TradingView einzeichnen würde. Rein visuell, unabhängig
    von der horizontalen Kanal-Erkennung in detect_channel().
    """
    highs = [p for p in pivots if p[2] == "H"]
    lows = [p for p in pivots if p[2] == "L"]
    result: dict = {"oben": None, "unten": None}
    if len(highs) >= 2:
        (i1, p1), (i2, p2) = highs[-2][:2], highs[-1][:2]
        if i2 != i1:
            result["oben"] = {"punkte": [(i1, p1), (i2, p2)], "steigung": (p2 - p1) / (i2 - i1)}
    if len(lows) >= 2:
        (i1, p1), (i2, p2) = lows[-2][:2], lows[-1][:2]
        if i2 != i1:
            result["unten"] = {"punkte": [(i1, p1), (i2, p2)], "steigung": (p2 - p1) / (i2 - i1)}
    return result


def detect_channel(support_touches: int, support_center: float, resistance: float, resistance_tests: int) -> dict | None:
    """Kanal = Support UND Widerstand jeweils mehrfach getestet (Konzeptpunkt „Widerstand mehrfach getestet")."""
    if resistance_tests >= 2 and support_touches >= 2 and np.isfinite(resistance) and resistance > support_center:
        breite_pct = (resistance - support_center) / support_center * 100
        return {
            "typ": "Handelskanal (Support & Widerstand jeweils mehrfach bestätigt)",
            "oben": resistance, "unten": support_center, "breite_pct": breite_pct,
        }
    return None


ELLIOTT_LABELS_BY_LENGTH = {
    2: ["0", "1"],
    3: ["0", "1", "2"],
    4: ["0", "1", "2", "3"],
    5: ["0", "1", "2", "3", "4"],
    6: ["0", "1", "2", "3", "4", "5"],
    7: ["0", "1", "2", "3", "4", "5", "A"],
    8: ["0", "1", "2", "3", "4", "5", "A", "B"],
    9: ["0", "1", "2", "3", "4", "5", "A", "B", "C"],
}
ELLIOTT_POSITION_BY_LENGTH = {
    2: "Welle 1 (Impuls) läuft",
    3: "Welle 2 (Korrektur von Welle 1) läuft",
    4: "Welle 3 (Impuls) läuft",
    5: "Welle 4 (Korrektur von Welle 3) läuft",
    6: "Impuls 1–5 abgeschlossen – Korrektur A-B-C steht bevor",
    7: "Welle B (Erholung nach Welle A) läuft",
    8: "Welle C (letzte Korrekturwelle) läuft",
    9: "Korrektur A-B-C abgeschlossen – neuer Impuls könnte beginnen",
}


def _validate_elliott_window(pts: list[tuple[int, float, str]]) -> dict | None:
    """Prüft ein Fenster aufeinanderfolgender Zickzack-Punkte gegen die drei harten
    Elliott-Regeln (Welle 2 nicht über 100 % von Welle 1 hinaus, Welle 3 nicht die
    kürzeste, Welle 4 überschneidet nicht das Kursgebiet von Welle 1). Verletzt das
    Fenster eine harte Regel, ist die Zählung ungültig (None). Weiche Richtlinien
    (Alternation, Fibonacci-Verhältnisse) werden nur als Hinweis mitgegeben, nicht
    als Ausschlusskriterium – wie in der Elliott-Wellen-Praxis üblich.
    """
    n = len(pts)
    if n < 2 or n not in ELLIOTT_LABELS_BY_LENGTH:
        return None
    for i in range(1, n):
        if pts[i][2] == pts[i - 1][2]:
            return None  # Zickzack-Garantie verletzt, sollte nicht vorkommen
    up = pts[0][2] == "L"  # Start an einem Tief => Impuls verläuft zunächst aufwärts
    price = [p[1] for p in pts]
    idx = [p[0] for p in pts]

    def leg(a: int, b: int) -> float:
        return abs(price[b] - price[a])

    hints: list[str] = []
    if n >= 3:
        wave2_ok = (price[2] > price[0]) if up else (price[2] < price[0])
        if not wave2_ok:
            return None  # harte Regel: Welle 2 darf Welle 1 nicht zu 100 % zurücklaufen
    if n >= 6:
        w1, w3, w5 = leg(0, 1), leg(2, 3), leg(4, 5)
        if w3 < w1 and w3 < w5:
            return None  # harte Regel: Welle 3 darf nie die kürzeste Impulswelle sein
    elif n >= 4:
        w1, w3 = leg(0, 1), leg(2, 3)
        if w3 < w1 * 0.4:
            hints.append("Welle 3 wirkt im Verhältnis zu Welle 1 ungewöhnlich kurz")
    if n >= 5:
        wave4_ok = (price[4] > price[1]) if up else (price[4] < price[1])
        if not wave4_ok:
            return None  # harte Regel: Welle 4 darf nicht ins Kursgebiet von Welle 1 laufen
    if n >= 8:
        b_exceeds = (price[7] > price[5]) if up else (price[7] < price[5])
        if b_exceeds:
            hints.append("Welle B läuft über das Ende von Welle 5 hinaus (untypisch, ggf. expandierte Flat-Korrektur)")

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
    rückwärts – das längste noch gültige Fenster (9 = Impuls 1–5 + Korrektur A-B-C,
    bis runter auf 2 = Welle 1 läuft) und liefert die aktuelle Wellenposition.

    Kein akademisch unumstrittenes Verfahren (Elliott-Wellen-Zählung ist immer auch
    Auslegungssache), aber eine nachvollziehbare, regelbasierte Näherung statt freihändigem Raten.
    """
    for window in (9, 8, 7, 6, 5, 4, 3, 2):
        if len(pivots) < window:
            continue
        result = _validate_elliott_window(pivots[-window:])
        if result:
            return result
    return None


def detect_breakout(close: pd.Series, high: pd.Series, low: pd.Series, atr: pd.Series, volume: pd.Series, lookback: int) -> dict | None:
    """Ausbruch über einen Widerstand bzw. Ausbruch/Bruch unter einen Support, mit Volumen-Check.

    Das relevante Level wird bewusst relativ zum GESTRIGEN Kurs gesucht (nicht zum heutigen):
    Nach einem echten Ausbruch liegt der gebrochene Widerstand ja bereits unter dem neuen,
    höheren Kurs und würde sonst durch den Ausbruch selbst aus der Trefferliste fallen.
    """
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

    # WICHTIG: Anders als in der Vorgängerversion bricht die Analyse NICHT mehr komplett
    # ab, nur weil aktuell kein mehrfach getesteter Support in Kursnähe existiert (z. B.
    # weil der Kurs bereits deutlich unter jedes alte Support-Level gefallen ist). Genau
    # das war der Hauptgrund, warum "Short-Setup" so gut wie nie vorkam: der komplette
    # Ticker wurde schon hier (return None) verworfen, bevor Kopf-Schulter-, Kanal-,
    # Ausbruchs-, Elliott- oder Momentum-Erkennung (die alle NICHT von "candidates"
    # abhängen) überhaupt zum Zug kamen. Jetzt läuft die Analyse für jeden Ticker mit
    # genug Historie durch; ohne Long-Support-Kandidat bleiben nur die Long-spezifischen
    # Felder (Support, Stop-Idee, Exit-Ziel, Technischer Score) neutral/NaN, wodurch der
    # Ticker automatisch aus dem Long-Ranking herausfällt, aber trotzdem als Short-Setup
    # erkannt werden kann.
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
    support_below, support_below_tests = find_level(lo, at, current, "below", lb)
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
        current > latest_open
        and current > prev_close
        and lower_wick / candle_range >= 0.30
        and long_candidate_found
        and latest_low <= best["center"] * (1 + tolerance * 1.3)
    )
    bearish_reversal_candle = bool(
        current < latest_open
        and current < prev_close
        and upper_wick / candle_range >= 0.30
        and np.isfinite(resistance)
        and latest_high >= resistance * (1 - tolerance * 1.3)
    )
    rsi_turns_up = bool(rv > prev_rsi and (prev_rsi < 45 or rv < 50))
    rsi_turns_down = bool(rv < prev_rsi and (prev_rsi > 55 or rv > 50))
    macd_improves = bool(hv > hp)
    macd_worsens = bool(hv < hp)
    above_ema20 = bool(current > e20)
    reversal_signals = []
    if above_ema20: reversal_signals.append("Schlusskurs über EMA20")
    if macd_improves: reversal_signals.append("MACD verbessert")
    if bullish_reversal_candle: reversal_signals.append("bullische Umkehrkerze")
    if rsi_turns_up: reversal_signals.append("RSI dreht nach oben")
    reversal_confirmed = bool(reversal_signals)
    # Spiegelbildliches bärisches Momentum-Bündel – bisher fehlte dieses Gegenstück,
    # wodurch "Short-Setup" durch die einseitige Stimmenvergabe unten praktisch nie
    # zustande kam (bullisches Momentum gab fast immer kostenlos +1 Stimme, das
    # bärische Gegenstück gab nie eine Stimme).
    bearish_reversal_signals = []
    if not above_ema20: bearish_reversal_signals.append("Schlusskurs unter EMA20")
    if macd_worsens: bearish_reversal_signals.append("MACD verschlechtert")
    if bearish_reversal_candle: bearish_reversal_signals.append("bärische Umkehrkerze")
    if rsi_turns_down: bearish_reversal_signals.append("RSI dreht nach unten")
    bearish_reversal_confirmed = bool(bearish_reversal_signals)

    # --- Chartmuster (Schritt-2-Konzeptpunkte) -----------------------------
    pivots = zigzag_pivots(cl)
    head_shoulders = detect_head_shoulders(pivots)
    inverse_head_shoulders = detect_inverse_head_shoulders(pivots)
    channel = detect_channel(best["touches"], best["center"], resistance, resistance_tests)
    elliott = label_elliott_wave(pivots)
    breakout = detect_breakout(cl, hi, lo, at, vol, lb)
    trend_channel_raw = fit_trend_channel(pivots)

    # Kursziel-Fallback ("Measured Move"): existiert nach einem echten Ausbruch kein
    # historisches Gegenlevel mehr (weil der Kurs bereits die gesamte Historie überwunden hat),
    # wird die Distanz vom Ausgangslevel zum aktuellen Kurs einfach in dieselbe Richtung weiter
    # projiziert. Grobe, aber gängige heuristische Ersatzgröße statt eines undefinierten Ziels.
    exit_target = resistance
    if not np.isfinite(exit_target):
        exit_target = current + abs(current - best["center"]) if long_candidate_found else current + current_atr * 3
    short_target = support_below
    if not np.isfinite(short_target):
        reference = resistance if np.isfinite(resistance) else current + current_atr * 3
        short_target = current - abs(reference - current)
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
    # Elliott-Wellen-Zählung: nur an klaren Entscheidungspunkten stimmberechtigt –
    # Welle 5 oder frisch abgeschlossene A-B-C-Korrektur = Fortsetzung des Impulses
    # (bullisch bei Aufwärts-Impuls), gerade abgeschlossener Impuls 1–5 = fällige
    # Gegenbewegung (also Gegenrichtung zum Impuls). Mitten in Welle 2/3/4/A/B wird
    # bewusst nicht gestimmt, da dort keine eindeutige Richtungsaussage vorliegt.
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
    if bearish_reversal_confirmed:
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
        # Kein Long-Support-Kandidat in Kursnähe -> Long-Score bewusst 0, damit dieser
        # Ticker aus dem Long-Ranking herausfällt (er kann trotzdem als Short-Setup
        # weiterverarbeitet werden, siehe Bearish-Score weiter unten).
        score = 0.0
    d = best["distance"]

    # --- Short-/Bearish-Score (symmetrisches, einfacheres Modell) ----------
    # short_target enthält bereits den Measured-Move-Fallback von weiter oben.
    # Short-Stop: NICHT einfach das am weitesten entfernte historische Widerstandslevel
    # nehmen (das kann bei einem Titel, der schon lange im Abwärtstrend ist, sehr weit
    # weg liegen und macht den Stop unrealistisch groß) -- stattdessen den NÄHEREN von
    # "nächster getesteter Widerstand" und "letztes Zwischenhoch (Zickzack)" verwenden,
    # plus kleinem Puffer. Sonst wird short_stop faktisch identisch zur Zielprojektion
    # (die im Fallback ebenfalls auf "resistance" basiert) und das CRV landet bei exakt
    # 1.0 -- das war der zweite Grund, warum Short-Kandidaten die 1.25er-CRV-Hürde nie
    # erreicht haben.
    buffer = max(current_atr * 1.0, current * 0.015)
    last_high_pivot = next((p for i, p, t in reversed(pivots) if t == "H"), np.nan)
    swing_stop = (last_high_pivot if np.isfinite(last_high_pivot) and last_high_pivot > current else latest_high) + buffer
    short_stop = min(resistance, swing_stop) if np.isfinite(resistance) else swing_stop
    short_risk, short_reward = short_stop - current, current - short_target if np.isfinite(short_target) else np.nan
    short_crv = short_reward / short_risk if short_risk > 0 and np.isfinite(short_reward) and short_reward > 0 else np.nan
    bearish_score = 0.0
    if breakout and "bärisch" in breakout["typ"]:
        bearish_score += 25 if breakout["volumen_bestaetigt"] else 14
    if head_shoulders:
        bearish_score += 20
    if elliott_decisive is False:
        bearish_score += 10
    bearish_score += 10 if current < e20 else -6
    bearish_score += 8 if current < e50 else -4
    bearish_score += 6 if current < e200 else 0
    bearish_score += 6 if slope < -0.01 else 2 if slope < 0 else -4
    bearish_score += 6 if rsi_turns_down and rv <= 60 else 0
    bearish_score += 5 if macd_worsens else -5
    bearish_score += 6 if bearish_reversal_candle else 0
    bearish_score += 8 if np.isfinite(short_crv) and short_crv >= 3 else 5 if np.isfinite(short_crv) and short_crv >= 2 else 2 if np.isfinite(short_crv) and short_crv >= 1.5 else 0
    bearish_score = round(max(0, min(100, bearish_score)), 1)

    status = "Support gebrochen" if best["broken"] or d < -0.025 else "Vorprüfung"
    recent_volume = volume.iloc[-20:]
    vol20 = float(recent_volume.mean())
    median_vol20 = float(recent_volume.median())
    traded_value = (close * volume).replace([np.inf, -np.inf], np.nan)
    avg_traded_value20 = float(traded_value.iloc[-20:].mean())
    median_traded_value20 = float(traded_value.iloc[-20:].median())

    # Splits/Dividenden aus den bereits geladenen Kursdaten (Konzeptpunkt Schritt 1,
    # hier je Kandidat konkret ausgewertet statt nur global angezeigt).
    dividend_recent = False
    split_recent = False
    if "Dividends" in g.columns:
        recent_g = g.iloc[-30:]
        dividend_recent = bool((recent_g["Dividends"].astype(float) > 0).any())
    if "Stock Splits" in g.columns:
        recent_g90 = g.iloc[-90:]
        split_recent = bool((recent_g90["Stock Splits"].astype(float) > 0).any())

    # --- Chart-Overlay: exakte Koordinaten für die Visualisierung in Tab 3 -----------
    # Damit man nicht mehr in TradingView nachschauen muss, wo genau Trendkanal, SKS-Punkte,
    # ABC-Punkte und Ausbruchslevel liegen, werden hier die Datumsangaben (statt fragiler
    # Positions-Indizes) für alle erkannten Chartmuster mitgespeichert.
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
            "typ": sks_pattern["typ"], "neckline": sks_pattern["neckline"], "punkte": sks_points,
            "neckline_punkte": [list(_pt(i, p)) for i, p in sks_pattern["neckline_punkte"]],
        }
    if elliott:
        elliott_points = []
        for i, p, label in elliott["punkte"]:
            dt, price = _pt(i, p)
            elliott_points.append([dt, price, label])
        overlay["elliott"] = {
            "richtung": elliott["richtung"], "aktuelle_position": elliott["aktuelle_position"],
            "anzahl_wellen": elliott["anzahl_wellen"], "hinweise": elliott["hinweise"], "punkte": elliott_points,
        }
    if breakout:
        overlay["breakout"] = {
            "typ": breakout["typ"], "level": float(breakout["level"]),
            "volumen_bestaetigt": bool(breakout["volumen_bestaetigt"]), "datum": dates.iloc[-1].date().isoformat(),
        }
    chart_overlay_json = json.dumps(overlay, ensure_ascii=False)

    return {
        "Ticker": str(g["Ticker"].iloc[-1]), "Name": str(g["Name"].iloc[-1]) if "Name" in g else str(g["Ticker"].iloc[-1]), "Index": str(g["Index"].iloc[-1]) if "Index" in g else "", "Letztes Datum": pd.to_datetime(g["Date"].iloc[-1]).date().isoformat(),
        "Kurs": current, "Support": best["center"], "Supportzone unten": best["center"] * (1 - tolerance),
        "Supportzone oben": best["center"] * (1 + tolerance), "Abstand Support %": d * 100,
        "Support-Tests": best["touches"], "Letzter Test vor Tagen": best["days"], "Nächster Widerstand": resistance,
        "Nächster Support darunter": support_below,
        "Stop-Idee": stop, "Entry-Idee": current, "Exit-Ziel": exit_target, "CRV bis Widerstand": crv,
        "EMA20": e20, "EMA50": e50, "EMA200": e200,
        "EMA50 Trend 1M %": slope * 100, "RSI14": rv, "MACD Histogramm": hv,
        "MACD verbessert": macd_improves, "Bullische Umkehrkerze": bullish_reversal_candle,
        "RSI dreht hoch": rsi_turns_up, "Umkehr bestätigt": reversal_confirmed,
        "Umkehrsignale": ", ".join(reversal_signals) if reversal_signals else "Keine",
        "Umkehrsignal-Anzahl": len(reversal_signals),
        "Über EMA20": above_ema20, "Über EMA50": bool(current > e50), "Über EMA200": bool(current > e200),
        "Volumen vs 20T": float(volume.iloc[-1] / vol20) if vol20 else np.nan,
        "Ø Aktienvolumen 20T": vol20, "Median Aktienvolumen 20T": median_vol20,
        "Ø Handelswert 20T": avg_traded_value20, "Median Handelswert 20T": median_traded_value20,
        "Technischer Score": score, "Score": score, "Status": status,
        "Testdaten": ", ".join(dates.iloc[i].date().isoformat() for i, _ in best["points"][-6:]),
        "Erkannte Muster": ", ".join(patterns) if patterns else "Keine",
        "Signaltyp": signal_type,
        "Bearish-Score": bearish_score, "Short-Ziel": short_target, "Short-Stop-Idee": short_stop, "Short-CRV": short_crv,
        "Kopf-Schulter erkannt": bool(head_shoulders), "Inverse Kopf-Schulter erkannt": bool(inverse_head_shoulders),
        "Kanal erkannt": bool(channel),
        "Elliott-Position": elliott["aktuelle_position"] if elliott else "Keine gültige Zählung gefunden",
        "Elliott-Richtung": elliott["richtung"] if elliott else "",
        "Elliott-Wellenanzahl": elliott["anzahl_wellen"] if elliott else 0,
        "Ausbruch erkannt": breakout["typ"] if breakout else "Nein",
        "Dividende letzte 30 Tage": dividend_recent, "Split letzte 90 Tage": split_recent,
        "Chart-Overlay": chart_overlay_json,
    }


# =============================================================================
# GUI
# =============================================================================

class TradingScannerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1280x860")
        self.minsize(1040, 740)
        self.data: pd.DataFrame | None = None
        self.results: pd.DataFrame | None = None
        self.market_situation: list[dict] = []
        self.current_chart_canvas = None
        self.status_var = tk.StringVar(value="Bereit")
        self.progress_var = tk.DoubleVar(value=0)
        self.output_var = tk.StringVar(value=str(Path.home() / "Downloads" / "Trading_Scanner"))
        self.period_var = tk.StringVar(value="1y")
        self.include_sp500_var = tk.BooleanVar(value=True)
        self.include_nasdaq_var = tk.BooleanVar(value=True)
        self.include_dax_var = tk.BooleanVar(value=True)
        self.include_csi300_var = tk.BooleanVar(value=True)
        self.csv_var = tk.StringVar(value="")
        self.filter_var = tk.StringVar(value="Long-Kandidat A")
        self.auto_start_var = tk.BooleanVar(value=True)
        self.daily_cache_var = tk.BooleanVar(value=True)
        self.workflow_running = False
        self.market_situation_var = tk.StringVar(value="Noch nicht geladen.")
        self._build_ui()
        self.after(1200, self._auto_start_if_enabled)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        ttk.Label(self, text="Aktien App – Marktsituation, Asset-Screener, Entry & Risiko", font=("Segoe UI", 17, "bold")).pack(anchor="w", padx=16, pady=(12, 2))
        topbar = ttk.Frame(self)
        topbar.pack(fill="x", padx=16, pady=(0, 8))
        ttk.Label(topbar, text="Konzept: erst Marktsituation prüfen, dann Asset-Screener laufen lassen, dann Entry/Chancen/Risiken je Kandidat bewerten.").pack(side="left")
        ttk.Button(topbar, text="Alles jetzt aktualisieren", command=self.start_full_workflow).pack(side="right")
        ttk.Checkbutton(topbar, text="Beim Start automatisch", variable=self.auto_start_var).pack(side="right", padx=10)
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=12, pady=6)
        self.market_tab = ttk.Frame(notebook)
        self.screener_tab = ttk.Frame(notebook)
        self.results_tab = ttk.Frame(notebook)
        notebook.add(self.market_tab, text="1. Marktsituation")
        notebook.add(self.screener_tab, text="2. Asset-Screener")
        notebook.add(self.results_tab, text="3. Entry, Chancen & Risiken")
        self._build_market_tab()
        self._build_screener_tab()
        self._build_results_tab()
        footer = ttk.Frame(self)
        footer.pack(fill="x", padx=16, pady=(0, 10))
        ttk.Progressbar(footer, variable=self.progress_var, maximum=100).pack(side="left", fill="x", expand=True)
        ttk.Label(footer, textvariable=self.status_var, width=48).pack(side="left", padx=(12, 0))

    # --- Tab 1: Marktsituation ----------------------------------------
    def _build_market_tab(self) -> None:
        top = ttk.Frame(self.market_tab)
        top.pack(fill="x", padx=14, pady=(12, 4))
        ttk.Label(top, text="Immer vorab klären: In welcher Marktsituation befinden wir uns?", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(top, textvariable=self.market_situation_var, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(2, 6))
        ttk.Button(top, text="Marktsituation aktualisieren", command=self.start_market_situation).pack(anchor="w")
        scroll_container = ttk.Frame(self.market_tab)
        scroll_container.pack(fill="both", expand=True, padx=14, pady=8)
        canvas = tk.Canvas(scroll_container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(scroll_container, orient="vertical", command=canvas.yview)
        self.market_tiles_frame = ttk.Frame(canvas)
        self.market_tiles_frame.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.market_tiles_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.market_canvas = canvas

    def _render_market_situation(self) -> None:
        for widget in self.market_tiles_frame.winfo_children():
            widget.destroy()
        columns = 2
        for i, item in enumerate(self.market_situation):
            row, col = divmod(i, columns)
            status = item.get("status", "neutral")
            tile = tk.Frame(self.market_tiles_frame, bg=AMPEL_FARBEN.get(status, AMPEL_FARBEN["neutral"]), bd=1, relief="solid")
            tile.grid(row=row, column=col, sticky="nsew", padx=6, pady=6, ipadx=8, ipady=6)
            self.market_tiles_frame.columnconfigure(col, weight=1, minsize=420)
            fg = AMPEL_TEXTFARBEN.get(status, AMPEL_TEXTFARBEN["neutral"])
            tk.Label(tile, text=item["label"], bg=tile["bg"], fg=fg, font=("Segoe UI", 10, "bold"), anchor="w", justify="left", wraplength=420).pack(fill="x")
            tk.Label(tile, text=item["value"], bg=tile["bg"], fg=fg, wraplength=420, justify="left", anchor="w").pack(fill="x")
            source = item.get("source", "")
            if source:
                tk.Label(tile, text=f"Quelle: {source}", bg=tile["bg"], fg=fg, font=("Segoe UI", 8), wraplength=420, justify="left", anchor="w").pack(fill="x")
            if item.get("error"):
                tk.Label(tile, text=f"Hinweis: {item['error']}", bg=tile["bg"], fg=fg, font=("Segoe UI", 8, "italic"), wraplength=420, justify="left", anchor="w").pack(fill="x")
            for detail in item.get("details", [])[:8]:
                tk.Label(tile, text=f"• {detail}", bg=tile["bg"], fg=fg, font=("Segoe UI", 8), wraplength=420, justify="left", anchor="w").pack(fill="x")

    def start_market_situation(self) -> None:
        self.run_thread(self._market_situation_worker)

    def _market_situation_worker(self, silent: bool = False) -> None:
        try:
            self.status_var.set("Marktsituation wird geladen ...")
            items = fetch_market_situation()
            self.market_situation = items
            summary_text, _status = market_situation_summary(items)
            self.after(0, lambda: self.market_situation_var.set(summary_text))
            self.after(0, self._render_market_situation)
            self.status_var.set("Marktsituation aktualisiert")
        except Exception as exc:
            self.status_var.set("Fehler bei der Marktsituation")
            if not silent:
                messagebox.showerror("Fehler", f"{exc}\n\n{traceback.format_exc(limit=2)}")

    # --- Tab 2: Asset-Screener -----------------------------------------
    def _path_row(self, parent, row, label, variable, command, button="Auswählen"):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=10, pady=8)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=10, pady=8)
        ttk.Button(parent, text=button, command=command).grid(row=row, column=2, padx=10, pady=8)

    def _build_screener_tab(self):
        frame = ttk.Frame(self.screener_tab)
        frame.pack(fill="x", padx=12, pady=12)
        frame.columnconfigure(1, weight=1)
        self._path_row(frame, 0, "Ausgabeordner:", self.output_var, self.choose_output)
        ttk.Label(frame, text="Zeitraum:").grid(row=1, column=0, sticky="w", padx=10, pady=8)
        ttk.Combobox(frame, textvariable=self.period_var, values=["6mo", "1y", "2y", "5y"], state="readonly", width=12).grid(row=1, column=1, sticky="w", padx=10, pady=8)
        self._path_row(frame, 2, "Rohdaten-CSV (statt Download):", self.csv_var, self.choose_csv)
        indices = ttk.LabelFrame(self.screener_tab, text="Indizes")
        indices.pack(fill="x", padx=22, pady=6)
        ttk.Checkbutton(indices, text="S&P 500", variable=self.include_sp500_var).pack(side="left", padx=16, pady=10)
        ttk.Checkbutton(indices, text="NASDAQ 100", variable=self.include_nasdaq_var).pack(side="left", padx=16, pady=10)
        ttk.Checkbutton(indices, text="DAX 40", variable=self.include_dax_var).pack(side="left", padx=16, pady=10)
        ttk.Checkbutton(indices, text="CSI 300 (China)", variable=self.include_csi300_var).pack(side="left", padx=16, pady=10)
        actions = ttk.Frame(self.screener_tab)
        actions.pack(fill="x", padx=22, pady=10)
        ttk.Button(actions, text="Kompletten Ablauf starten (Download + Screening)", command=self.start_full_workflow).pack(side="left")
        ttk.Button(actions, text="Nur Daten laden", command=self.start_download).pack(side="left", padx=8)
        ttk.Button(actions, text="Nur vorhandene CSV screenen", command=self.start_analysis).pack(side="left", padx=8)
        ttk.Checkbutton(actions, text="Heute geladene Daten wiederverwenden", variable=self.daily_cache_var).pack(side="left", padx=12)
        text = (
            "Asset-Screener (Konzept Schritt 2): erst mehrfach getestete horizontale Supports/Widerstände, dann "
            "Chartmuster – Kopf-Schulter (SKS) und inverse SKS, Handelskanal (Support & Widerstand mehrfach getestet), "
            "eine heuristische Elliott-ABC-Korrektur sowie Ausbrüche über Widerstand/unter Support mit Volumen-Check. "
            "Daraus ergibt sich je Kandidat ein Long- oder Short-Setup (oder kein klares Setup).\n\n"
            "Long-/Short-Kandidaten werden relativ zum aktuellen Marktuniversum gerankt. Harte Mindestbedingungen "
            "(Trend, Support-Tests, CRV, Liquidität) verhindern, dass reine Liquidität oder Nähe zu einem Level ein "
            "technisch schwaches Setup hochstuft. Die Einstufung ist eine Vorauswahl und kein automatisches Kauf-/Verkaufssignal."
        )
        ttk.Label(self.screener_tab, text=text, wraplength=980, justify="left").pack(anchor="w", padx=22, pady=10)
        ttk.Label(self.screener_tab, text="Ergebnis: Alle_Aktien_OHLCV.csv (Rohdaten) sowie Trading_Support_Scan.csv/.xlsx (Screening-Ergebnis).").pack(anchor="w", padx=22)

    # --- Tab 3: Entry / Chancen & Risiken -------------------------------
    def _build_results_tab(self):
        top = ttk.Frame(self.results_tab)
        top.pack(fill="x", padx=10, pady=8)
        ttk.Label(top, text="Filter:").pack(side="left")
        filter_values = [
            "Alle", "Long-Kandidat A", "Long-Kandidat B", "Short-Kandidat",
            "Support-Kandidat", "Beobachten", "Kein aktuelles Setup", "Support gebrochen",
        ]
        combo = ttk.Combobox(top, textvariable=self.filter_var, values=filter_values, state="readonly", width=22)
        combo.pack(side="left", padx=8)
        combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_table())
        ttk.Button(top, text="Excel exportieren", command=self.export_excel).pack(side="left", padx=8)
        pane = ttk.Panedwindow(self.results_tab, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        left, right = ttk.Frame(pane), ttk.Frame(pane)
        pane.add(left, weight=3); pane.add(right, weight=2)
        columns = (
            "Ticker", "Name", "Index", "Status", "Signaltyp", "Score", "Bearish-Score", "Erkannte Muster",
            "Liquidität", "Kurs", "Support", "Nächster Widerstand", "Abstand Support %", "RSI14", "CRV bis Widerstand",
        )
        self.tree = ttk.Treeview(left, columns=columns, show="headings", height=20)
        widths = {
            "Ticker": 75, "Name": 200, "Index": 110, "Status": 130, "Signaltyp": 110, "Score": 65,
            "Bearish-Score": 95, "Erkannte Muster": 300, "Liquidität": 100, "Kurs": 85, "Support": 85,
            "Nächster Widerstand": 120, "Abstand Support %": 110, "RSI14": 70, "CRV bis Widerstand": 100,
        }
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=widths.get(col, 100), anchor="w" if col in {"Name", "Index", "Erkannte Muster", "Liquidität", "Status", "Signaltyp"} else "center")
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<<TreeviewSelect>>", self.on_select_result)
        self.chart_frame = right
        ttk.Label(right, text="Wähle links einen Kandidaten aus.").pack(pady=20)

    def _build_entry_panel(self, parent, row: pd.Series) -> None:
        for widget in parent.winfo_children():
            widget.destroy()
        long_short = row.get("Signaltyp", "Kein klares Setup")
        entry = float(row.get("Entry-Idee", row.get("Kurs", np.nan)))
        if long_short == "Short-Setup":
            stop = float(row.get("Short-Stop-Idee", np.nan))
            ziel = float(row.get("Short-Ziel", np.nan))
        else:
            stop = float(row.get("Stop-Idee", np.nan))
            ziel = float(row.get("Exit-Ziel", np.nan))
        panel = ttk.LabelFrame(parent, text="Schritt 3: Entry, Chancen & Risiken")
        panel.pack(fill="x", pady=(6, 0))
        ttk.Label(panel, text=f"Signaltyp: {long_short}", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=8, pady=(6, 2))
        ttk.Label(panel, text=f"Entry-Idee: {entry:.2f}   |   Stop-Loss-Idee: {stop:.2f}   |   Exit-/Kursziel: {ziel:.2f}").pack(anchor="w", padx=8)
        crv = row.get("Short-CRV") if long_short == "Short-Setup" else row.get("CRV bis Widerstand")
        crv_text = f"{crv:.2f}" if pd.notna(crv) else "n/a"
        ttk.Label(panel, text=f"Chance/Risiko-Verhältnis (CRV): {crv_text}").pack(anchor="w", padx=8)
        elliott_position = row.get("Elliott-Position", "")
        elliott_richtung = row.get("Elliott-Richtung", "")
        if elliott_position:
            elliott_text = f"Elliott-Wellen: {elliott_position}" + (f" (Impuls {elliott_richtung})" if elliott_richtung else "")
            ttk.Label(panel, text=elliott_text, wraplength=460, justify="left").pack(anchor="w", padx=8, pady=(2, 0))
        note = (
            "Erinnerung: Stop-Loss- und Exit-Order direkt nach dem Einstieg beim Broker setzen, "
            "um Angst/Zögern in der Position zu minimieren. Diese App setzt keine Order automatisch."
        )
        ttk.Label(panel, text=note, wraplength=460, justify="left", foreground="#664d03").pack(anchor="w", padx=8, pady=(4, 4))
        order_note = (
            f"{row['Ticker']} | Signal: {long_short} | Entry: {entry:.2f} | "
            f"Stop: {stop:.2f} | Ziel: {ziel:.2f} | CRV: {crv_text}"
        )
        self._order_note_text = order_note

        def copy_note():
            self.clipboard_clear()
            self.clipboard_append(order_note)

        ttk.Button(panel, text="Order-Notiz in Zwischenablage kopieren", command=copy_note).pack(anchor="w", padx=8, pady=(0, 8))

    def _auto_start_if_enabled(self):
        if self.auto_start_var.get() and not self.workflow_running:
            self.start_full_workflow()

    def start_full_workflow(self):
        if self.workflow_running:
            return
        self.workflow_running = True
        self.run_thread(self._full_workflow_worker)

    def _full_workflow_worker(self):
        try:
            self._market_situation_worker(silent=True)
            out_file = Path(self.output_var.get()) / "Alle_Aktien_OHLCV.csv"
            reuse = False
            if self.daily_cache_var.get() and out_file.exists():
                modified = datetime.fromtimestamp(out_file.stat().st_mtime).date()
                reuse = modified == date.today()
            if reuse:
                self.csv_var.set(str(out_file))
                self.status_var.set("Heutige Rohdaten werden verwendet ...")
            else:
                self._download_worker(silent=True)
            self._analysis_worker(silent=True, auto_export=True)
            self.after(0, lambda: self.filter_var.set("Long-Kandidat A"))
            self.after(0, self.refresh_table)
            self.after(0, lambda: messagebox.showinfo(
                "Automatische Auswertung fertig",
                "Marktsituation, Download, Screening und Ergebnisexport sind abgeschlossen.\n"
                "Tab 1 zeigt die Marktsituation, Tab 3 die Long-Kandidaten A."
            ))
        except Exception as exc:
            self.after(0, lambda: messagebox.showerror("Automatik-Fehler", f"{exc}"))
        finally:
            self.workflow_running = False

    def choose_output(self):
        path = filedialog.askdirectory()
        if path: self.output_var.set(path)

    def choose_csv(self):
        path = filedialog.askopenfilename(filetypes=[("CSV-Datei", "*.csv")])
        if path: self.csv_var.set(path)

    def run_thread(self, target):
        threading.Thread(target=target, daemon=True).start()

    def start_download(self):
        self.run_thread(self._download_worker)

    def _download_worker(self, silent: bool = False):
        try:
            self.progress_var.set(0); self.status_var.set("Watchlist wird vorbereitet ...")
            member_frames = []
            selections = [("S&P 500", self.include_sp500_var.get()), ("NASDAQ-100", self.include_nasdaq_var.get()), ("DAX", self.include_dax_var.get()), ("CSI 300", self.include_csi300_var.get())]
            for name, enabled in selections:
                if enabled:
                    self.status_var.set(f"Lade aktuelle {name}-Mitglieder ...")
                    member_frames.append(fetch_index_members(name))
            members = merge_member_frames(member_frames)
            if members.empty:
                raise ValueError("Keine Ticker ausgewählt.")
            tickers = members["Ticker"].tolist()
            metadata = members.set_index("Ticker")[["Name", "Index"]].to_dict("index")
            out_dir = Path(self.output_var.get())
            out_dir.mkdir(parents=True, exist_ok=True)
            frames, errors = [], []
            for i, ticker in enumerate(tickers, start=1):
                self.status_var.set(f"{i}/{len(tickers)}: {ticker}")
                self.progress_var.set(i / len(tickers) * 100)
                try:
                    df = yf.download(ticker, period=self.period_var.get(), interval="1d", auto_adjust=False, actions=True, progress=False, threads=False)
                    if df.empty:
                        errors.append((ticker, "Keine Daten")); continue
                    info = metadata.get(ticker, {})
                    frames.append(normalize_download_frame(df, ticker, info.get("Name", ticker), info.get("Index", "")))
                except Exception as exc:
                    errors.append((ticker, str(exc)))
            if not frames:
                raise RuntimeError("Es konnten keine Kursdaten geladen werden.")
            combined = pd.concat(frames, ignore_index=True, sort=False)
            output_file = out_dir / "Alle_Aktien_OHLCV.csv"
            combined.to_csv(output_file, index=False)
            members.to_csv(out_dir / "Verwendete_Indizes_und_Ticker.csv", index=False)
            if errors:
                pd.DataFrame(errors, columns=["Ticker", "Fehler"]).to_csv(out_dir / "Fehlgeschlagene_Ticker.csv", index=False)
            self.csv_var.set(str(output_file))
            self.data = combined
            self.status_var.set(f"Fertig: {len(frames)} Assets, {len(combined):,} Datensätze")
            self.progress_var.set(100)
            if not silent:
                messagebox.showinfo("Download abgeschlossen", f"Datei gespeichert:\n{output_file}\n\nErfolgreich: {len(frames)}\nFehler: {len(errors)}")
        except Exception as exc:
            self.status_var.set("Fehler beim Download")
            if not silent:
                messagebox.showerror("Fehler", f"{exc}\n\n{traceback.format_exc(limit=2)}")
            raise

    def start_analysis(self):
        self.run_thread(self._analysis_worker)

    def _analysis_worker(self, silent: bool = False, auto_export: bool = False):
        try:
            path = Path(self.csv_var.get())
            if not path.exists():
                raise FileNotFoundError("Bitte zuerst eine Rohdaten-CSV auswählen.")
            self.status_var.set("CSV wird geladen ..."); self.progress_var.set(0)
            data = pd.read_csv(path, parse_dates=["Date"])
            required = {"Ticker", "Date", "High", "Low", "Close"}
            if not required.issubset(data.columns):
                raise ValueError(f"Fehlende Spalten: {', '.join(sorted(required - set(data.columns)))}")
            self.data = data
            results, errors = [], []
            groups = list(data.groupby("Ticker", sort=False))
            for i, (ticker, group) in enumerate(groups, start=1):
                self.status_var.set(f"Analysiere {i}/{len(groups)}: {ticker}")
                self.progress_var.set(i / len(groups) * 100)
                try:
                    result = analyze_ticker(group)
                    if result: results.append(result)
                except Exception as exc:
                    errors.append((ticker, str(exc)))
            if not results:
                raise RuntimeError("Keine auswertbaren Muster gefunden.")
            self.results = pd.DataFrame(results)
            volume_pct = self.results["Ø Aktienvolumen 20T"].rank(pct=True, method="average").fillna(0)
            value_pct = self.results["Ø Handelswert 20T"].rank(pct=True, method="average").fillna(0)
            liquidity_raw = 0.35 * volume_pct + 0.65 * value_pct
            self.results["Liquiditäts-Score"] = (liquidity_raw * 10).round(1)
            self.results["Liquidität"] = pd.cut(
                self.results["Liquiditäts-Score"],
                bins=[-0.01, 2, 4, 6, 8, 10.01],
                labels=["Sehr niedrig", "Niedrig", "Mittel", "Hoch", "Sehr hoch"]
            ).astype(str)
            self.results["Max. Positionswert bei 1% ADV"] = self.results["Ø Handelswert 20T"] * 0.01
            self.results["Score"] = self.results["Technischer Score"].round(1)

            crv = pd.to_numeric(self.results["CRV bis Widerstand"], errors="coerce")
            signal_count = pd.to_numeric(self.results["Umkehrsignal-Anzahl"], errors="coerce").fillna(0)
            trend_ok = self.results["Über EMA200"].astype(bool)
            midtrend_ok = self.results["Über EMA50"].astype(bool) | (self.results["EMA50 Trend 1M %"] > 0)
            reversal_ok = signal_count >= 1
            not_broken = (self.results["Status"] != "Support gebrochen") & (self.results["Abstand Support %"] >= -2.5)
            tradable = self.results["Liquiditäts-Score"] >= 2.0
            long_signal_ok = self.results["Signaltyp"] != "Short-Setup"
            base_eligible = (
                not_broken
                & (self.results["Support-Tests"] >= 3)
                & self.results["Abstand Support %"].between(-1.5, 7.0)
                & trend_ok
                & tradable
                & long_signal_ok
                & crv.ge(1.25)
                & (reversal_ok | midtrend_ok)
                & (self.results["Technischer Score"] >= 58)
            )
            self.results["Ranking-Score"] = (
                self.results["Technischer Score"] * 0.82
                + self.results["Liquiditäts-Score"] * 1.2
                + np.minimum(signal_count, 3) * 2.0
                + np.minimum(self.results["Support-Tests"], 5) * 0.8
            ).round(1)

            short_crv = pd.to_numeric(self.results["Short-CRV"], errors="coerce")
            bearish_eligible = (
                (self.results["Signaltyp"] == "Short-Setup")
                & (self.results["Bearish-Score"] >= 55)
                & short_crv.fillna(0).ge(1.25)
                & tradable
            )

            self.results["Status"] = "Kein aktuelles Setup"
            self.results.loc[not_broken & (self.results["Abstand Support %"] <= 12) & (self.results["Support-Tests"] >= 2), "Status"] = "Beobachten"
            self.results.loc[not_broken & (self.results["Abstand Support %"] <= 6) & (self.results["Support-Tests"] >= 3), "Status"] = "Support-Kandidat"
            self.results.loc[~not_broken, "Status"] = "Support gebrochen"

            eligible_idx = self.results.index[base_eligible].tolist()
            long_a_idx, long_b_idx = [], []
            if eligible_idx:
                ranked = self.results.loc[eligible_idx].sort_values("Ranking-Score", ascending=False)
                a_count = min(12, max(3, math.ceil(len(ranked) * 0.15)))
                long_a_idx = ranked.head(a_count).index
                long_b_idx = ranked.iloc[a_count:].head(max(10, math.ceil(len(ranked) * 0.35))).index
                self.results.loc[long_b_idx, "Status"] = "Long-Kandidat B"
                self.results.loc[long_a_idx, "Status"] = "Long-Kandidat A"

            short_idx = self.results.index[bearish_eligible].tolist()
            if short_idx:
                ranked_short = self.results.loc[short_idx].sort_values("Bearish-Score", ascending=False)
                short_count = min(15, max(3, math.ceil(len(ranked_short) * 0.25)))
                short_top_idx = ranked_short.head(short_count).index
                # Long-Einstufung hat Vorrang, falls ein Titel (selten) in beiden Listen auftaucht.
                short_top_idx = short_top_idx.difference(long_a_idx).difference(long_b_idx)
                self.results.loc[short_top_idx, "Status"] = "Short-Kandidat"

            status_order = {
                "Long-Kandidat A": 0, "Long-Kandidat B": 1, "Short-Kandidat": 2, "Support-Kandidat": 3,
                "Beobachten": 4, "Kein aktuelles Setup": 5, "Support gebrochen": 6,
            }
            self.results = self.results.sort_values(
                ["Status", "Ranking-Score", "Abstand Support %"],
                key=lambda c: c.map(status_order) if c.name == "Status" else c,
                ascending=[True, False, True]
            ).reset_index(drop=True)
            self.results["A-Kriterien erfüllt"] = self.results["Status"] == "Long-Kandidat A"

            out_file = path.with_name("Trading_Support_Scan.csv")
            self.results.to_csv(out_file, index=False)
            if auto_export:
                excel_file = path.with_name(f"Trading_Support_Scan_{datetime.now():%Y-%m-%d}.xlsx")
                with pd.ExcelWriter(excel_file, engine="openpyxl") as writer:
                    self.results.to_excel(writer, sheet_name="Alle Ergebnisse", index=False)
                    for status, name in [("Long-Kandidat A", "Long-Kandidaten A"), ("Long-Kandidat B", "Long-Kandidaten B"), ("Short-Kandidat", "Short-Kandidaten"), ("Support-Kandidat", "Support-Kandidaten"), ("Beobachten", "Beobachten")]:
                        self.results[self.results["Status"] == status].to_excel(writer, sheet_name=name, index=False)
            self.after(0, self.refresh_table)
            self.status_var.set(f"Screening fertig: {len(self.results)} Assets, {len(errors)} Fehler")
            self.progress_var.set(100)
            if not silent:
                messagebox.showinfo("Screening abgeschlossen", f"{len(self.results)} Assets ausgewertet.\nErgebnis gespeichert:\n{out_file}")
        except Exception as exc:
            self.status_var.set("Fehler beim Screening")
            if not silent:
                messagebox.showerror("Fehler", f"{exc}\n\n{traceback.format_exc(limit=2)}")
            raise

    def filtered_results(self) -> pd.DataFrame:
        if self.results is None:
            return pd.DataFrame()
        selected = self.filter_var.get()
        return self.results if selected == "Alle" else self.results[self.results["Status"] == selected]

    def refresh_table(self):
        for item in self.tree.get_children(): self.tree.delete(item)
        df = self.filtered_results()
        for _, row in df.iterrows():
            values = []
            for col in self.tree["columns"]:
                value = row.get(col, "")
                if isinstance(value, (float, np.floating)):
                    if not math.isfinite(value):
                        value = ""
                    else:
                        value = f"{value:.2f}"
                values.append(value)
            self.tree.insert("", "end", iid=str(row.name), values=values)

    def on_select_result(self, _event=None):
        if self.results is None or self.data is None or not self.tree.selection(): return
        index = int(self.tree.selection()[0]); row = self.results.loc[index]
        ticker = row["Ticker"]
        group = self.data[self.data["Ticker"] == ticker].sort_values("Date").tail(180).copy()
        self.draw_chart(group, row)

    @staticmethod
    def _extend_line(p1: tuple, p2: tuple, last_date: "pd.Timestamp") -> tuple[list, list]:
        """Verlängert eine durch zwei Punkte definierte Linie bis zum letzten Chart-Datum
        (einfache lineare Extrapolation) – so wie eine manuell gezogene Trendlinie."""
        t1, y1 = p1
        t2, y2 = p2
        if t2 == t1:
            return [t1, t2], [y1, y2]
        days = (t2 - t1).days or 1
        slope = (y2 - y1) / days
        if last_date > t2:
            y_last = y2 + slope * (last_date - t2).days
            return [t1, last_date], [y1, y_last]
        return [t1, t2], [y1, y2]

    def draw_chart(self, group: pd.DataFrame, row: pd.Series):
        for widget in self.chart_frame.winfo_children(): widget.destroy()
        figure = plt.Figure(figsize=(6.4, 5.4), dpi=100)
        axis = figure.add_subplot(111)
        dates = pd.to_datetime(group["Date"])
        close = group["Adj Close"] if "Adj Close" in group else group["Close"]
        last_date = dates.max()
        axis.plot(dates, close, linewidth=1.4, label="Kurs", color="#1f4e79", zorder=3)
        # Support-Kandidat kann bei reinen Short-Setups fehlen (kein Long-Support in
        # Kursnähe gefunden) -> Supportzone dann einfach nicht einzeichnen statt mit
        # NaN-Werten zu crashen.
        if pd.notna(row["Supportzone unten"]) and pd.notna(row["Supportzone oben"]):
            axis.axhspan(float(row["Supportzone unten"]), float(row["Supportzone oben"]), alpha=0.18, color="#2e8b57", label="Supportzone")
        is_short = row.get("Signaltyp") == "Short-Setup"
        stop_val = row["Short-Stop-Idee"] if is_short else row["Stop-Idee"]
        ziel_val = row["Short-Ziel"] if is_short else row["Exit-Ziel"]
        if pd.notna(stop_val):
            axis.axhline(float(stop_val), linestyle=":", linewidth=1.3, color="#c00000", label="Stop-Idee")
        if pd.notna(ziel_val):
            axis.axhline(float(ziel_val), linestyle="-.", linewidth=1.3, color="#2e8b57", label="Exit-/Kursziel")
        elif pd.notna(row["Nächster Widerstand"]):
            axis.axhline(float(row["Nächster Widerstand"]), linestyle="--", linewidth=1, color="#808080", label="Widerstand")

        try:
            overlay = json.loads(row.get("Chart-Overlay") or "{}")
        except (TypeError, ValueError):
            overlay = {}

        pivot_points = [(pd.Timestamp(d), p) for d, p, _t in overlay.get("pivots", []) if d and pd.Timestamp(d) >= dates.min()]
        if len(pivot_points) >= 2:
            px, py = zip(*pivot_points)
            axis.plot(px, py, linewidth=0.8, linestyle="-", color="#999999", marker="o", markersize=3, alpha=0.7, label="Swing-Punkte", zorder=2)

        kanal_oben = overlay.get("kanal_oben")
        if kanal_oben and all(d for d, _p in kanal_oben):
            p1, p2 = (pd.Timestamp(kanal_oben[0][0]), kanal_oben[0][1]), (pd.Timestamp(kanal_oben[1][0]), kanal_oben[1][1])
            lx, ly = self._extend_line(p1, p2, last_date)
            axis.plot(lx, ly, linestyle="--", linewidth=1.3, color="#e07b00", label="Trendkanal oben", zorder=2)
        kanal_unten = overlay.get("kanal_unten")
        if kanal_unten and all(d for d, _p in kanal_unten):
            p1, p2 = (pd.Timestamp(kanal_unten[0][0]), kanal_unten[0][1]), (pd.Timestamp(kanal_unten[1][0]), kanal_unten[1][1])
            lx, ly = self._extend_line(p1, p2, last_date)
            axis.plot(lx, ly, linestyle="--", linewidth=1.3, color="#e0a000", label="Trendkanal unten", zorder=2)

        sks = overlay.get("sks")
        if sks:
            sks_points = [(pd.Timestamp(d), p, lbl) for d, p, lbl in sks.get("punkte", []) if d]
            if sks_points:
                sx, sy, labels = zip(*sks_points)
                axis.plot(sx, sy, linestyle="-", linewidth=1, color="#7030a0", marker="^", markersize=6, label=sks["typ"], zorder=4)
                for x, y, lbl in sks_points:
                    axis.annotate(lbl, (x, y), textcoords="offset points", xytext=(0, 8), fontsize=7, color="#7030a0", ha="center")
            neck_points = [(pd.Timestamp(d), p) for d, p in sks.get("neckline_punkte", []) if d]
            if len(neck_points) == 2:
                lx, ly = self._extend_line(neck_points[0], neck_points[1], last_date)
                axis.plot(lx, ly, linestyle="-.", linewidth=1.2, color="#7030a0", label="Nackenlinie", zorder=2)

        elliott = overlay.get("elliott")
        if elliott:
            elliott_points = [(pd.Timestamp(d), p, lbl) for d, p, lbl in elliott.get("punkte", []) if d]
            if elliott_points:
                ex_, ey_, labels = zip(*elliott_points)
                elliott_label = f"Elliott-Wellen ({elliott.get('richtung', '')})"
                axis.plot(ex_, ey_, linestyle=":", linewidth=1.1, color="#0070c0", marker="s", markersize=5, label=elliott_label, zorder=4)
                for x, y, lbl in elliott_points:
                    axis.annotate(lbl, (x, y), textcoords="offset points", xytext=(0, -12), fontsize=7.5, color="#0070c0", ha="center", fontweight="bold")

        breakout_info = overlay.get("breakout")
        if breakout_info and breakout_info.get("datum"):
            bx, by = pd.Timestamp(breakout_info["datum"]), breakout_info["level"]
            axis.plot([bx], [by], marker="*", markersize=14, color="black", label="Ausbruchspunkt", zorder=5)

        axis.set_title(f"{row['Ticker']} – {row.get('Name', '')} | {row['Status']} | Signal: {row.get('Signaltyp','')} | Score {row['Score']}", fontsize=9)
        axis.grid(True, alpha=0.2)
        handles, labels = axis.get_legend_handles_labels()
        seen = set()
        dedup = [(h, l) for h, l in zip(handles, labels) if not (l in seen or seen.add(l))]
        axis.legend([h for h, _ in dedup], [l for _, l in dedup], loc="best", fontsize=6.5, ncol=2)
        figure.autofmt_xdate()
        canvas = FigureCanvasTkAgg(figure, master=self.chart_frame)
        canvas.draw(); canvas.get_tk_widget().pack(fill="both", expand=True)
        support_txt = (
            f"Support {row['Support']:.2f} | Abstand {row['Abstand Support %']:.2f}% | Tests {int(row['Support-Tests'])}"
            if pd.notna(row["Support"]) else "kein Long-Support in Kursnähe"
        )
        details = (f"Kurs {row['Kurs']:.2f} | {support_txt} | "
                   f"RSI {row['RSI14']:.1f} | Signaltyp {row.get('Signaltyp','')} | "
                   f"Muster: {row.get('Erkannte Muster','')} | "
                   f"Liquidität {row.get('Liquidität', '')} ({row.get('Liquiditäts-Score', np.nan):.1f}/10)")
        ttk.Label(self.chart_frame, text=details, wraplength=520, justify="left").pack(pady=6)
        self.current_chart_canvas = canvas
        # Eigener Unter-Frame fuer das Entry-Panel: _build_entry_panel raeumt seinen
        # Inhalt bei jedem Aufruf selbst auf und darf dabei nicht den Chart-Canvas
        # (der im selben chart_frame haengt) mit zerstoeren.
        entry_container = ttk.Frame(self.chart_frame)
        entry_container.pack(fill="x")
        self._build_entry_panel(entry_container, row)

    def export_excel(self):
        if self.results is None:
            messagebox.showwarning("Keine Ergebnisse", "Bitte zuerst einen Asset-Screen durchführen."); return
        path = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")], initialfile=f"Trading_Support_Scan_{datetime.now():%Y-%m-%d}.xlsx")
        if not path: return
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            self.results.to_excel(writer, sheet_name="Alle Ergebnisse", index=False)
            for status, name in [("Long-Kandidat A", "Long-Kandidaten A"), ("Long-Kandidat B", "Long-Kandidaten B"), ("Short-Kandidat", "Short-Kandidaten"), ("Support-Kandidat", "Support-Kandidaten"), ("Beobachten", "Beobachten")]:
                self.results[self.results["Status"] == status].to_excel(writer, sheet_name=name, index=False)
        messagebox.showinfo("Export abgeschlossen", f"Excel-Datei gespeichert:\n{path}")


if __name__ == "__main__":
    TradingScannerApp().mainloop()
