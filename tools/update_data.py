#!/usr/bin/env python3
"""Fetch ~1 year of daily closes and write one JSON file per route plus a manifest.

Uses Yahoo Finance's public chart endpoint (no API key). Set REFRESH=1 to
re-download charts that are already on disk. Otherwise a rerun continues where
it left off. WORKERS and DELAY control the pace. A long streak of network
failures stops the run and keeps every file already saved.
"""
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ROUTES = DATA / "routes"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
REFRESH = os.environ.get("REFRESH", "") == "1"
WORKERS = max(1, int(os.environ.get("WORKERS", "4")))
DELAY = float(os.environ.get("DELAY", "0.2"))
STREAK_LIMIT = int(os.environ.get("STREAK_LIMIT", "40"))


class GiveUp(Exception):
    pass


class NoData(Exception):
    pass


class Pace:
    def __init__(self, delay):
        self.delay = delay
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            if now < self.next_at:
                time.sleep(self.next_at - now)
            self.next_at = time.monotonic() + self.delay


def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (429, 500, 502, 503, 504):
            raise GiveUp(f"HTTP {exc.code}") from exc
        raise NoData(f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise GiveUp(str(exc)) from exc


def fetch_yahoo(symbol):
    quoted = urllib.parse.quote(symbol, safe="")
    last = "no response"
    for host in HOSTS:
        url = f"https://{host}/v8/finance/chart/{quoted}?range=1y&interval=1d&includePrePost=false"
        try:
            data = http_json(url)
        except NoData as exc:
            last = str(exc)
            continue
        chart = data.get("chart", {})
        results = chart.get("result") or []
        if not results:
            err = chart.get("error") or {}
            last = err.get("description") or err.get("code") or "empty chart"
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
            if price is None:
                continue
            try:
                value = float(price)
            except (TypeError, ValueError):
                continue
            if value <= 0:
                continue
            day = datetime.fromtimestamp(int(stamps[i]) + offset, tz=timezone.utc).strftime("%Y-%m-%d")
            rounded = round(value, 4)
            if dates and dates[-1] == day:
                closes[-1] = rounded
            else:
                dates.append(day)
                closes.append(rounded)
        if len(closes) < 30:
            raise NoData(f"only {len(closes)} bars")
        return dates, closes
    raise NoData(last)


def fetch_retry(symbol, pace):
    delay = 2.0
    last = "no response"
    for attempt in range(4):
        pace.wait()
        try:
            return fetch_yahoo(symbol)
        except GiveUp as exc:
            last = str(exc)
            time.sleep(delay)
            delay = min(delay * 2, 30.0)
        except NoData:
            raise
    raise GiveUp(last)


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


def usable(path):
    if not path.exists():
        return False
    try:
        route = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    closes = route.get("closes")
    dates = route.get("dates")
    return isinstance(closes, list) and isinstance(dates, list) and len(closes) >= 30 and len(closes) == len(dates)


def version_of(route):
    digest = hashlib.sha1(json.dumps(route["closes"]).encode()).hexdigest()[:6]
    return f"{route['dates'][-1]}.{digest}"


def main():
    cfgs = json.loads((ROOT / "tickers.json").read_text(encoding="utf-8"))
    ROUTES.mkdir(parents=True, exist_ok=True)
    pace = Pace(DELAY)
    stop = threading.Event()
    lock = threading.Lock()
    streak = {"n": 0}
    counts = {"ok": 0, "cached": 0, "fail": 0, "stopped": 0}

    def work(cfg):
        if stop.is_set():
            return "stopped"
        path = ROUTES / f"{cfg['id']}.json"
        if not REFRESH and usable(path):
            with lock:
                streak["n"] = 0
                counts["cached"] += 1
            return "cached"
        try:
            dates, closes = fetch_retry(cfg["symbol"], pace)
            route = build_route(cfg, dates, closes)
            path.write_text(json.dumps(route, separators=(",", ":")), encoding="utf-8")
            with lock:
                streak["n"] = 0
                counts["ok"] += 1
                done = counts["ok"] + counts["cached"] + counts["fail"]
            if counts["ok"] % 25 == 0:
                print(f"ok   {cfg['id']}: {len(closes)} bars  ({done}/{len(cfgs)})", flush=True)
            return "ok"
        except NoData as exc:
            with lock:
                counts["fail"] += 1
            print(f"FAIL {cfg['id']}: {exc}", file=sys.stderr, flush=True)
            return "fail"
        except GiveUp as exc:
            with lock:
                counts["fail"] += 1
                streak["n"] += 1
                if streak["n"] >= STREAK_LIMIT:
                    stop.set()
            print(f"FAIL {cfg['id']}: {exc}", file=sys.stderr, flush=True)
            return "fail"

    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(work, cfg) for cfg in cfgs]
        for fut in as_completed(futures):
            if fut.result() == "stopped" or stop.is_set():
                counts["stopped"] += 1

    entries = []
    for cfg in cfgs:
        path = ROUTES / f"{cfg['id']}.json"
        if not usable(path):
            continue
        route = json.loads(path.read_text(encoding="utf-8"))
        entries.append({
            "id": cfg["id"],
            "file": path.name,
            "updated": version_of(route),
            "name": cfg.get("name", cfg["id"]),
            "subtitle": cfg.get("subtitle", ""),
        })
    latest = max((e["updated"][:10] for e in entries), default="")
    (DATA / "manifest.json").write_text(
        json.dumps({"updated": latest, "routes": entries}, indent=1) + "\n",
        encoding="utf-8",
    )
    print(
        f"manifest: {len(entries)} routes, downloaded {counts['ok']}, "
        f"already saved {counts['cached']}, failed {counts['fail']}",
        flush=True,
    )
    sys.exit(1 if entries == [] else 0)


if __name__ == "__main__":
    main()
