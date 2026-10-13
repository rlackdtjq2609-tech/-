#!/usr/bin/env python3
"""업비트 1분봉에서 펫핑거(순간 급락) 기록을 찾는다.

판정 기준: 1분봉 저가가 시가 또는 직전 봉 종가 대비 THRESHOLD% 이상 낮으면 기록.
업비트 공개 API만 사용하므로 API 키가 필요 없다 (표준 라이브러리만 사용).

예시:
  python3 upbit_fatfinger.py                       # KRW 전체 마켓, 최근 24시간, 10%
  python3 upbit_fatfinger.py --hours 168           # 최근 7일
  python3 upbit_fatfinger.py --markets KRW-BTC,KRW-XRP --threshold 5
  python3 upbit_fatfinger.py --quote BTC --csv result.csv
"""
import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.upbit.com/v1"
KST = timezone(timedelta(hours=9))
REQUEST_INTERVAL = 0.12  # 캔들 API 제한(초당 10회) 이하로 유지


def get(path, params=None, retries=5):
    url = f"{API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                time.sleep(1 + attempt)
                continue
            raise
        except urllib.error.URLError:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise


def list_markets(quote):
    markets = get("/market/all", {"isDetails": "false"})
    return [m["market"] for m in markets if m["market"].startswith(quote + "-")]


def fetch_candles(market, since_utc):
    """since_utc 이후의 1분봉을 시간 오름차순으로 반환."""
    candles = []
    to = None
    while True:
        params = {"market": market, "count": 200}
        if to:
            params["to"] = to
        page = get("/candles/minutes/1", params)
        time.sleep(REQUEST_INTERVAL)
        if not page:
            break
        candles.extend(page)
        oldest = page[-1]["candle_date_time_utc"]
        if datetime.fromisoformat(oldest).replace(tzinfo=timezone.utc) <= since_utc or len(page) < 200:
            break
        to = oldest + "Z"
    candles.sort(key=lambda c: c["candle_date_time_utc"])
    return [
        c for c in candles
        if datetime.fromisoformat(c["candle_date_time_utc"]).replace(tzinfo=timezone.utc) >= since_utc
    ]


def find_drops(market, candles, threshold):
    hits = []
    prev_close = None
    for c in candles:
        o, low, close = c["opening_price"], c["low_price"], c["trade_price"]
        base = max(o, prev_close) if prev_close else o
        drop = (low / base - 1) * 100
        if drop <= -threshold:
            hits.append({
                "market": market,
                "time_kst": c["candle_date_time_kst"].replace("T", " "),
                "base": base,
                "open": o,
                "low": low,
                "close": close,
                "drop_pct": round(drop, 2),
                "recovery_pct": round((close / low - 1) * 100, 2),
                "volume": c["candle_acc_trade_volume"],
            })
        prev_close = close
    return hits


def main():
    ap = argparse.ArgumentParser(description="업비트 1분봉 펫핑거(급락) 탐지")
    ap.add_argument("--quote", default="KRW", help="마켓 구분: KRW, BTC, USDT (기본 KRW)")
    ap.add_argument("--markets", help="쉼표로 구분한 마켓 목록 (예: KRW-BTC,KRW-ETH)")
    ap.add_argument("--hours", type=float, default=24, help="조회 기간(시간), 기본 24")
    ap.add_argument("--threshold", type=float, default=10, help="급락 기준(%%), 기본 10")
    ap.add_argument("--csv", help="결과를 저장할 CSV 경로")
    args = ap.parse_args()

    markets = args.markets.split(",") if args.markets else list_markets(args.quote)
    since = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    print(f"{len(markets)}개 마켓, {since.astimezone(KST):%Y-%m-%d %H:%M} KST 이후, "
          f"-{args.threshold}% 이상 급락 탐색", file=sys.stderr)

    hits = []
    for i, market in enumerate(markets, 1):
        try:
            candles = fetch_candles(market, since)
        except Exception as e:  # 한 마켓 실패로 전체를 멈추지 않는다
            print(f"  [{i}/{len(markets)}] {market} 실패: {e}", file=sys.stderr)
            continue
        found = find_drops(market, candles, args.threshold)
        hits.extend(found)
        print(f"  [{i}/{len(markets)}] {market}: 봉 {len(candles)}개, 급락 {len(found)}건", file=sys.stderr)

    hits.sort(key=lambda h: h["drop_pct"])
    if not hits:
        print("조건에 맞는 급락이 없습니다.")
    else:
        print(f"\n{'마켓':<14}{'시각(KST)':<21}{'기준가':>14}{'저가':>14}{'종가':>14}{'하락%':>9}{'회복%':>9}")
        for h in hits:
            print(f"{h['market']:<14}{h['time_kst']:<21}{h['base']:>14,.8g}{h['low']:>14,.8g}"
                  f"{h['close']:>14,.8g}{h['drop_pct']:>9}{h['recovery_pct']:>9}")
        print(f"\n총 {len(hits)}건")

    if args.csv:
        fields = ["market", "time_kst", "base", "open", "low", "close", "drop_pct", "recovery_pct", "volume"]
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(hits)
        print(f"CSV 저장: {args.csv}", file=sys.stderr)


if __name__ == "__main__":
    main()
