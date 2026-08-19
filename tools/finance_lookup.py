#!/usr/bin/env python3
"""finance_lookup — amele tool (subprocess) for live market data.

stdin:  JSON request, one of:
          {"type": "fx"}                        → USD/EUR/GBP/CHF/JPY vs TRY
          {"type": "gold"}                      → gram gold + XAU (oz) in TRY
          {"type": "crypto", "ids": [...]}      → crypto prices (USD/TRY)
          {"type": "bond"}                      → Turkey gov bond yield
          {"type": "all"}                       → fx + gold + bitcoin/ethereum
stdout: JSON — the data, or {"error": "..."}
Env:    none required. Outbound HTTPS to www.tcmb.gov.tr and
        api.coingecko.com must be allowed.
"""
import json
import sys
import urllib.request
from typing import Optional

TCMB_URL = "https://www.tcmb.gov.tr/kurlar/today.xml"
COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
YAHOO_CHART = "https://query2.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=5d"
GOLD_SYMBOL = "GC=F"          # gold futures, USD/oz
BOND_SYMBOLS = ["TR1Y.TR", "TR10Y.TR"]  # Turkey government bonds (if listed)

CRYPTO_DEFAULT = ["bitcoin", "ethereum"]


def _get(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) kahya/0.2"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _yahoo_price(symbol: str) -> Optional[float]:
    """One price from Yahoo Finance, or None (symbol unknown/blocked)."""
    try:
        data = json.loads(_get(YAHOO_CHART.format(sym=symbol)))
        meta = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
        return meta.get("regularMarketPrice")
    except Exception:
        return None


def fetch_fx() -> dict:
    """TCMB official daily rates. Returns {CODE: {"buy":..,"sell":..}}.
    TCMB occasionally answers blank to back-to-back requests — retry."""
    import time
    import xml.etree.ElementTree as ET
    last_err = None
    for attempt in range(3):
        try:
            root = ET.fromstring(_get(TCMB_URL))
            out = {}
            for cur in root.findall("Currency"):
                code = cur.get("CurrencyCode") or cur.get("Kod")
                if code not in ("USD", "EUR", "GBP", "CHF", "JPY"):
                    continue
                try:
                    buy = float(cur.findtext("ForexBuying") or 0)
                    sell = float(cur.findtext("ForexSelling") or 0)
                except (TypeError, ValueError):
                    continue
                if sell:
                    out[code] = {"buy": round(buy, 4), "sell": round(sell, 4)}
            if out:
                return out
            last_err = "no rates parsed"
        except Exception as e:
            last_err = str(e)
        time.sleep(1.5 * (attempt + 1))
    return {"error": f"TCMB: {last_err}"}


def fetch_gold(usd_try: Optional[float] = None) -> dict:
    """Gold in TRY — oz price from Yahoo (GC=F), converted with the
    TCMB USD/TRY rate (TCMB publishes no gold rate in today.xml)."""
    usd_oz = _yahoo_price(GOLD_SYMBOL)
    if usd_oz is None:
        return {"error": "gold: price unavailable"}
    if usd_try is None:
        fx = fetch_fx()
        if "error" in fx or "USD" not in fx:
            return {"error": "gold: USD/TRY unavailable"}
        usd_try = fx["USD"]["sell"]
    return {
        "gram_try": round(usd_oz * usd_try / 31.1035, 2),
        "oz_try": round(usd_oz * usd_try, 2),
        "oz_usd": round(usd_oz, 2),
    }


def fetch_crypto(ids: list[str]) -> dict:
    if not ids:
        ids = CRYPTO_DEFAULT
    url = (f"{COINGECKO_URL}?ids={','.join(ids)}"
           f"&vs_currencies=usd,try&precision=2")
    try:
        raw = _get(url)
    except Exception as e:
        return {"error": f"CoinGecko: {e}"}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "CoinGecko: bad response"}


def fetch_bond() -> dict:
    """Turkey government bond yield (Yahoo Finance, if listed)."""
    for sym in BOND_SYMBOLS:
        price = _yahoo_price(sym)
        if price is not None:
            return {"bond": sym, "yield_pct": round(price, 2)}
    return {"error": "bond: Turkey bond yield is not available from "
                     "free public APIs right now"}


def main():
    try:
        req = json.loads(sys.stdin.read().strip() or "{}")
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"bad JSON: {e}"}))
        return 1
    t = req.get("type", "all")
    if t == "fx":
        out = fetch_fx()
    elif t == "gold":
        out = fetch_gold()
    elif t == "crypto":
        out = fetch_crypto(req.get("ids") or [])
    elif t == "bond":
        out = fetch_bond()
    elif t == "all":
        fx = fetch_fx()
        usd_try = fx.get("USD", {}).get("sell") if "error" not in fx else None
        gold = fetch_gold(usd_try=usd_try)
        crypto = fetch_crypto(CRYPTO_DEFAULT)
        out = {"fx": fx, "gold": gold, "crypto": crypto}
    else:
        out = {"error": f"unknown type: {t}"}
    print(json.dumps(out, ensure_ascii=False))
    return 0 if "error" not in out else 1


if __name__ == "__main__":
    sys.exit(main())
