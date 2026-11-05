#!/usr/bin/env python3
"""moex-analyst: факты о компании с Мосбиржи — капитализация, динамика, дивиденды и доходность, индексы.

  facts.py SBER
  facts.py SBER --json
Финансовую отчётность (МСФО/РСБУ) Claude ищет сам: сайт компании (раздел для инвесторов), e-disclosure.ru, smart-lab.ru.
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "moex-setup", "lib"))
import common as C  # noqa: E402
import moex  # noqa: E402
import ta  # noqa: E402


def perf(daily, days):
    if not daily:
        return None
    end = C.parse_dt(daily[-1]["t"])
    past = [b for b in daily if C.parse_dt(b["t"]) <= end - timedelta(days=days)]
    return (daily[-1]["c"] / past[-1]["c"] - 1) * 100 if past else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbol"); ap.add_argument("--json", action="store_true"); ap.add_argument("--now")
    a = ap.parse_args()
    now = C.now_msk(a.now)
    try:
        sp = moex.spec(a.symbol)
        daily = moex.candles(a.symbol, "1d", days=800, end=now, source="iss")
    except C.NetError as e:
        C.die(str(e))
    out = {"symbol": a.symbol, "name": sp.get("name"), "price": sp.get("last"), "lot": sp.get("lot")}
    try:
        snap = {r["SECID"]: r for r in moex.board_snapshot("shares")}
        r = snap.get(a.symbol, {})
        out["capitalization"] = r.get("ISSUECAPITALIZATION") or ((r.get("ISSUESIZE") or 0) * (sp.get("last") or 0)) or None
        out["issue_size"] = r.get("ISSUESIZE")
        out["list_level"] = r.get("LISTLEVEL")
    except C.NetError:
        pass
    y = [b for b in daily if C.parse_dt(b["t"]) >= now - timedelta(days=365)]
    if y:
        out["high_52w"] = max(b["h"] for b in y)
        out["low_52w"] = min(b["l"] for b in y)
    out["perf"] = {"1м": perf(daily, 30), "3м": perf(daily, 91), "6м": perf(daily, 182), "1г": perf(daily, 365)}
    try:
        divs = sorted(moex.dividends(a.symbol), key=lambda d: d["date"] or "")
    except C.NetError:
        divs = []
    out["dividends"] = divs[-10:]
    ttm = sum(d["value"] or 0 for d in divs if d["date"] and now - timedelta(days=365) <= C.parse_dt(d["date"]) <= now)
    out["dividend_ttm"] = ttm
    out["dividend_yield_ttm"] = ttm / sp["last"] * 100 if sp.get("last") and ttm else None
    out["next_dividend"] = next((d for d in divs if d["date"] and d["date"] >= now.strftime("%Y-%m-%d")), None)
    try:
        out["indices"] = moex.indices_of(a.symbol)[:12]
    except C.NetError:
        out["indices"] = []
    an = ta.analyze(daily)
    out["ta_daily"] = {"score": an.get("score"), "verdict": an.get("verdict"), "trend": an.get("trend"),
                       "rsi": (an.get("indicators") or {}).get("rsi14")}
    if a.json:
        C.print_json(out)
        return
    print("%s · %s" % (a.symbol, out.get("name") or ""))
    print("  цена %s, лот %s шт.%s" % (C.px(out["price"]), out["lot"],
                                       (", капитализация %s млрд ₽" % C.num(out["capitalization"] / 1e9, 1)) if out.get("capitalization") else ""))
    if out.get("high_52w"):
        print("  52 недели: %s – %s, сейчас %s от максимума" % (C.px(out["low_52w"]), C.px(out["high_52w"]),
                                                              C.pct((out["price"] / out["high_52w"] - 1) * 100)))
    print("  динамика: " + "  ".join("%s %s" % (k, C.pct(v, 1)) for k, v in out["perf"].items()))
    print("  дивиденды за 12 мес.: %s%s" % (C.rub(ttm), (" (доходность %s)" % C.pct(out["dividend_yield_ttm"])) if out["dividend_yield_ttm"] else ""))
    if out["next_dividend"]:
        nd = out["next_dividend"]
        print("  💰 следующая отсечка %s: %s на акцию (≈%s)" % (nd["date"], C.rub(nd["value"]),
                                                               C.pct(nd["value"] / out["price"] * 100) if out["price"] else "—"))
    if divs:
        print("  история: " + ", ".join("%s — %s" % (d["date"], C.rub(d["value"])) for d in divs[-6:]))
    if out["indices"]:
        print("  в индексах: " + ", ".join(out["indices"]))
    t = out["ta_daily"]
    print("  ТА по дневкам: %s (%+d), тренд %s, RSI %s" % (t["verdict"], t["score"] or 0, t["trend"], C.num(t["rsi"], 0)))
    try:
        f = moex.finance_warnings(moex.smartlab_finance(a.symbol))
    except C.NetError:
        f = {"year": None, "warnings": ["годовую отчётность получить не удалось"]}
    out["finance"] = f
    print("  Финансы (предупреждение, не запрет)%s: %s" % (" за %s год" % f["year"] if f["year"] else "",
                                                          "; ".join(f["warnings"]) or "явных тревожных признаков нет"))
    C.save_json(C.tpath("analyst", "%s.json" % a.symbol), out)


if __name__ == "__main__":
    main()
