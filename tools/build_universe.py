#!/usr/bin/env python3
"""Write tickers.json from the Nasdaq symbol directories plus the curated list.

Common stocks only: test issues, ETFs, warrants, rights, units and preferred shares
are left out. Share classes use Yahoo's hyphen form (BRK.B -> BRK-B).
"""
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
BAD_NAME = re.compile(
    r"\b(warrants?|rights?|units?|preferred|preference|debentures?|notes?)\b",
    re.I,
)
EXCHANGES = {
    "Q": "Nasdaq",
    "G": "Nasdaq",
    "S": "Nasdaq",
    "A": "NYSE American",
    "N": "NYSE",
    "P": "NYSE Arca",
    "Z": "Cboe",
    "V": "IEX",
}
RESERVED = {n for n in ("CON", "PRN", "AUX", "NUL") for n in (n,)} | {
    f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)
}
PINNED = [
    ("TSLA", "Tesla", "Tesla, Inc."),
    ("NVDA", "Nvidia", "NVIDIA Corp."),
    ("AAPL", "Apple", "Apple Inc."),
    ("GME", "GameStop", "GameStop Corp."),
    ("BTC-USD", "Bitcoin", "BTC in USD"),
    ("ETH-USD", "Ethereum", "ETH in USD"),
    ("QQQ", "Nasdaq 100", "Invesco QQQ ETF"),
    ("SPY", "S&P 500", "SPDR S&P 500 ETF"),
    ("AMZN", "Amazon", "Amazon.com, Inc."),
    ("MSFT", "Microsoft", "Microsoft Corp."),
    ("AMD", "AMD", "Advanced Micro Devices"),
    ("DOGE-USD", "Dogecoin", "DOGE in USD"),
]
ETFS = [
    ("DIA", "Dow Jones", "SPDR Dow Jones ETF"),
    ("IWM", "Russell 2000", "iShares Russell 2000 ETF"),
    ("VTI", "Total Market", "Vanguard Total Stock Market ETF"),
    ("SMH", "Semiconductors", "VanEck Semiconductor ETF"),
    ("XLF", "Financials", "Financial Select Sector SPDR"),
    ("XLE", "Energy", "Energy Select Sector SPDR"),
    ("XLK", "Technology", "Technology Select Sector SPDR"),
    ("GLD", "Gold", "SPDR Gold Shares"),
    ("SLV", "Silver", "iShares Silver Trust"),
    ("TLT", "Long Bonds", "iShares 20+ Year Treasury Bond ETF"),
    ("EEM", "Emerging Markets", "iShares MSCI Emerging Markets ETF"),
    ("ARKK", "ARK Innovation", "ARK Innovation ETF"),
]


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def yahoo_symbol(raw):
    sym = raw.strip().upper().replace(".", "-")
    if not sym or sym in RESERVED:
        return ""
    if not all(c.isalnum() or c == "-" for c in sym):
        return ""
    if len(sym) > 12:
        return ""
    return sym


def clean_name(name):
    name = name.split(" - ", 1)[0].strip()
    name = re.sub(r"\s+(Common Stock|Ordinary Shares|Class [A-Z].*)$", "", name, flags=re.I).strip()
    return name[:80] or name


def add(out, seen, sym, name, subtitle):
    sym = yahoo_symbol(sym)
    if not sym or sym in seen:
        return
    if BAD_NAME.search(name):
        return
    seen.add(sym)
    out.append({
        "id": sym,
        "name": clean_name(name) or sym,
        "subtitle": subtitle,
        "source": "yahoo",
        "symbol": sym,
        "unit": "$",
    })


def parse_pipe(text, symbol_i, name_i, test_i, etf_i, exch_i):
    rows = []
    for line in text.splitlines():
        if "|" not in line or line.lower().startswith("symbol") or line.lower().startswith("act symbol"):
            continue
        if line.startswith("File Creation Time"):
            break
        parts = line.split("|")
        if len(parts) <= max(symbol_i, name_i, test_i, etf_i):
            continue
        if parts[test_i].strip().upper() == "Y" or parts[etf_i].strip().upper() == "Y":
            continue
        exch = EXCHANGES.get(parts[exch_i].strip().upper(), "US") if exch_i < len(parts) else "US"
        rows.append((parts[symbol_i], parts[name_i], exch))
    return rows


def main():
    nasdaq = fetch("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt")
    other = fetch("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt")
    # nasdaq: Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot|ETF|NextShares
    # other:  ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot|Test Issue|NASDAQ Symbol
    rows = parse_pipe(nasdaq, 0, 1, 3, 6, 2)
    rows += parse_pipe(other, 0, 1, 6, 4, 2)
    out = []
    seen = set()
    for sym, name, sub in PINNED + ETFS:
        add(out, seen, sym, name, sub)
    rows.sort(key=lambda r: yahoo_symbol(r[0]))
    for sym, name, exch in rows:
        add(out, seen, sym, name, exch)
    (ROOT / "tickers.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"tickers: {len(out)}")


if __name__ == "__main__":
    main()
