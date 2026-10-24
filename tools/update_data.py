#!/usr/bin/env python3
"""Fetch ~1 year of daily closes and write one JSON file per route plus a manifest.

Uses Yahoo Finance's public chart endpoint (no API key), the same source the game
reads live. It is unofficial and can rate-limit. Check Yahoo's terms before
publishing the data in a public repo.
"""
import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ROUTES = DATA / "routes"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
DELAY = 1.2


def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_yahoo(symbol):
    quoted = urllib.parse.quote(symbol, safe="")
    last_error = "no response"
    for host in HOSTS:
        url = f"https://{host}/v8/finance/chart/{quoted}?range=1y&interval=1d&includePrePost=false"
        try:
            data = http_json(url)
        except Exception as exc:
            last_error = str(exc)
            continue
        chart = data.get("chart", {})
        results = chart.get("result") or []
        if not results:
            err = chart.get("error") or {}
            last_error = err.get("description") or err.get("code") or "empty chart"
            continue
        row = results[0]
        stamps = row.get("timestamp") or []
        quotes = ((row.get("indicators") or {}).get("quote") or [{}])[0]
        raw = quotes.get("close") or []
        offset = int((row.get("meta") or {}).get("gmtoffset") or 0)
        dates = []
        closes = []
        for i in range(min(len(stamps), len(raw))):
            price = raw[i]
            if price is None or float(price) <= 0:
                continue
            day = datetime.fromtimestamp(int(stamps[i]) + offset, tz=timezone.utc).strftime("%Y-%m-%d")
            rounded = round(float(price), 4)
            if dates and dates[-1] == day:
                closes[-1] = rounded
            else:
                dates.append(day)
                closes.append(rounded)
        if len(closes) < 30:
            last_error = f"only {len(closes)} data points"
            continue
        return dates, closes
    raise RuntimeError(last_error)


def month_label(date_str):
    return datetime.strptime(date_str, "%Y-%m-%d").strftime("%b %Y")


def build_route(cfg, dates, closes):
    return {
        "id": cfg["id"],
        "name": cfg["name"],
        "symbol": cfg["id"],
        "subtitle": cfg.get("subtitle", ""),
        "unit": cfg.get("unit", ""),
        "period": f"{month_label(dates[0])} - {month_label(dates[-1])}",
        "dates": dates,
        "closes": closes,
    }


def version_of(route):
    digest = hashlib.sha1(json.dumps(route["closes"]).encode()).hexdigest()[:6]
    return f"{route['dates'][-1]}.{digest}"


def main():
    cfgs = json.loads((ROOT / "tickers.json").read_text(encoding="utf-8"))
    ROUTES.mkdir(parents=True, exist_ok=True)
    failures = 0

    for i, cfg in enumerate(cfgs):
        if i:
            time.sleep(DELAY)
        try:
            if cfg.get("source") not in ("yahoo", "twelvedata", "coingecko"):
                raise RuntimeError("unknown source " + str(cfg.get("source")))
            # Every listed id is a Yahoo symbol. Crypto uses the Yahoo pair (BTC-USD).
            dates, closes = fetch_yahoo(cfg["symbol"] if cfg.get("source") != "coingecko" else cfg["id"])
            route = build_route(cfg, dates, closes)
            (ROUTES / f"{cfg['id']}.json").write_text(json.dumps(route, separators=(",", ":")), encoding="utf-8")
            print(f"ok   {cfg['id']}: {len(closes)} bars, last {dates[-1]}")
        except Exception as exc:
            failures += 1
            print(f"FAIL {cfg['id']}: {exc}", file=sys.stderr)

    entries = []
    for cfg in cfgs:
        path = ROUTES / f"{cfg['id']}.json"
        if path.exists():
            route = json.loads(path.read_text(encoding="utf-8"))
            entries.append({"id": cfg["id"], "file": path.name, "updated": version_of(route)})
    latest = max((e["updated"][:10] for e in entries), default="")
    (DATA / "manifest.json").write_text(
        json.dumps({"updated": latest, "routes": entries}, indent=1) + "\n",
        encoding="utf-8",
    )
    print(f"manifest: {len(entries)} routes, failures: {failures}")
    sys.exit(1 if entries == [] else 0)


if __name__ == "__main__":
    main()
