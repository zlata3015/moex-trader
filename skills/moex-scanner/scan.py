#!/usr/bin/env python3
"""moex-scanner: сканер Мосбиржи.

  scan.py universe --kind shares            ликвидные инструменты: оборот, изменение
  scan.py orb --at 09:30 --range 15         пробои диапазона открытия на сегодня
  scan.py swing                             дневные сетапы: пробой, откат к EMA20, отскок, сжатие, золотой крест
  scan.py intraday --tf 15m                 внутридневные сетапы: пробой, VWAP, отскок, сжатие
  scan.py ta --tf 1h --top 15               рейтинг по оценке технического анализа
  scan.py find сбер                         поиск тикера
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "moex-setup", "lib"))
import common as C  # noqa: E402
import moex  # noqa: E402
import setups as S  # noqa: E402
import ta  # noqa: E402


def universe(kind, min_turnover, limit):
    rows = moex.board_snapshot(kind)
    if kind == "futures":
        today = C.now_msk().strftime("%Y-%m-%d")
        best = {}
        for r in rows:
            if (r.get("LASTTRADEDATE") or "") < today:
                continue
            a = r.get("ASSETCODE")
            if a not in best or (r.get("OPENPOSITION") or 0) > (best[a].get("OPENPOSITION") or 0):
                best[a] = r
        rows = list(best.values())
    out = []
    for r in rows:
        turn = r.get("VALTODAY") or r.get("VALTODAY_RUR") or 0
        if kind == "index":
            turn = turn or 1
        if turn < min_turnover:
            continue
        last = r.get("LAST") or r.get("MARKETPRICE") or r.get("PREVPRICE") or r.get("PREVSETTLEPRICE")
        out.append({"symbol": r["SECID"], "name": r.get("SHORTNAME"), "last": last, "turnover": turn,
                    "change": r.get("LASTTOPREVPRICE") or r.get("LASTCHANGEPRCNT"), "lot": r.get("LOTSIZE"),
                    "kind": kind})
    out.sort(key=lambda x: -(x["turnover"] or 0))
    return out[:limit]


def kinds_of(arg):
    return ["shares", "futures", "currency", "etf", "bonds", "corp"] if arg == "all" else [arg]


def cmd_universe(a):
    allu = []
    for k in kinds_of(a.kind):
        allu += universe(k, a.min_turnover, a.limit)
    rows = [[u["symbol"], (u["name"] or "")[:22], C.px(u["last"]), C.rub(u["turnover"], 0), C.pct(u["change"]),
             u["kind"]] for u in allu]
    print(C.table(rows, ["тикер", "название", "цена", "оборот сегодня", "изм.", "рынок"]))
    print("\nДанные ISS без подписки — с задержкой ~15 минут.")


def _bars(sym, tf, a, kind, days=None):
    return moex.candles(sym, tf, days=days, end=C.now_msk(a.now), kind=kind, source=a.source)


def cmd_orb(a):
    cfg = C.config()
    now = C.now_msk(a.now)
    at = C.parse_dt(now.strftime("%Y-%m-%d ") + a.at) if a.at else now
    start = a.start or cfg["orb_start"]
    found, checked = [], 0
    uni = []
    for k in kinds_of(a.kind):
        uni += universe(k, a.min_turnover, a.limit)
    for u in uni:
        try:
            bars = _bars(u["symbol"], "5m", a, u["kind"], days=2)
        except C.NetError as e:
            sys.stderr.write("%s: %s\n" % (u["symbol"], e))
            continue
        checked += 1
        day = [b for b in bars if b["t"][:10] == at.strftime("%Y-%m-%d")]
        r = S.orb(day, 5, start, a.range, at=at, allow_short=cfg["allow_short"] or a.short)
        if r.get("status") == "сработал":
            r.update(symbol=u["symbol"], kind=u["kind"], turnover=u["turnover"])
            found.append(r)
        time.sleep(0.02)
    found.sort(key=lambda r: r["trigger_time"])
    print("Пробой диапазона открытия %s + %d мин, на %s МСК: %d из %d" % (start, a.range, C.fmt_dt(at)[11:], len(found), checked))
    rows = [[r["symbol"], "лонг" if r["direction"] == "long" else "шорт", C.px(r["range_low"]) + " – " + C.px(r["range_high"]),
             r["trigger_time"][11:], C.px(r["trigger_price"]), C.pct(r["over_pct"])] for r in found]
    if rows:
        print(C.table(rows, ["тикер", "направление", "диапазон", "бар", "закрытие", "за границей"]))
    save("orb", found, at)


def cmd_setups(a, tf, which, title):
    found, uni = [], []
    for k in kinds_of(a.kind):
        uni += universe(k, a.min_turnover, a.limit)
    for u in uni:
        try:
            bars = _bars(u["symbol"], tf, a, u["kind"])
        except C.NetError as e:
            sys.stderr.write("%s: %s\n" % (u["symbol"], e))
            continue
        for r in S.detect(bars, which):
            an = ta.analyze(bars, intraday=tf in moex.INTRADAY)
            r.update(symbol=u["symbol"], kind=u["kind"], tf=tf, score=an.get("score"), verdict=an.get("verdict"))
            found.append(r)
        time.sleep(0.02)
    found.sort(key=lambda r: -(r.get("score") or 0))
    print("%s (%s): найдено %d" % (title, tf, len(found)))
    rows = [[r["symbol"], S.SETUPS[r["setup"]], C.px(r["trigger_price"]), "%+d" % (r["score"] or 0), r["verdict"]] for r in found]
    if rows:
        print(C.table(rows, ["тикер", "сетап", "цена", "ТА", "вывод"]))
    save(title, found, C.now_msk(a.now))


def cmd_swing(a):
    cmd_setups(a, "1d", ("breakout", "pullback", "rsi-bounce", "squeeze", "golden-cross"), "Свинг-сетапы")


def cmd_intraday(a):
    cmd_setups(a, a.tf, ("breakout", "vwap-reclaim", "rsi-bounce", "squeeze", "pullback"), "Внутридневные сетапы")


def cmd_ta(a):
    res, uni = [], []
    for k in kinds_of(a.kind):
        uni += universe(k, a.min_turnover, a.limit)
    for u in uni:
        try:
            bars = _bars(u["symbol"], a.tf, a, u["kind"])
        except C.NetError as e:
            sys.stderr.write("%s: %s\n" % (u["symbol"], e))
            continue
        an = ta.analyze(bars, intraday=a.tf in moex.INTRADAY)
        if "error" in an:
            continue
        res.append({"symbol": u["symbol"], "kind": u["kind"], "score": an["score"], "verdict": an["verdict"],
                    "trend": an["trend"], "price": an["price"], "rsi": an["indicators"]["rsi14"],
                    "adx": an["indicators"]["adx"], "patterns": an["patterns"]})
    res.sort(key=lambda r: -r["score"])
    top = res[:a.top] + ([] if a.long_only else [r for r in res[-a.top:] if r not in res[:a.top]])
    print("Рейтинг ТА (%s): %d инструментов" % (a.tf, len(res)))
    rows = [[r["symbol"], "%+d" % r["score"], r["verdict"], r["trend"], C.px(r["price"]), C.num(r["rsi"], 0),
             C.num(r["adx"], 0), ", ".join(r["patterns"])[:30]] for r in top]
    print(C.table(rows, ["тикер", "оценка", "вывод", "тренд", "цена", "RSI", "ADX", "паттерны"]))
    save("ta-" + a.tf, res, C.now_msk(a.now))


def cmd_find(a):
    rows = moex.search(a.query)
    print(C.table([[r.get("secid"), r.get("shortname"), (r.get("name") or "")[:40], r.get("primary_boardid"), r.get("group")]
                   for r in rows], ["тикер", "кратко", "название", "режим", "группа"]))


def save(kind, items, at):
    C.save_json(C.tpath("scan.json"), {"scan": kind, "at": C.fmt_dt(at), "results": items})


def main():
    cfg = C.config()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def u(p):
        p.add_argument("--kind", default="shares", choices=["shares", "futures", "currency", "etf", "bonds", "corp", "all"])
        p.add_argument("--min-turnover", type=float, default=cfg["min_turnover_rub"], help="₽ оборота за день")
        p.add_argument("--limit", type=int, default=60, help="сколько самых ликвидных проверять")
        p.add_argument("--source", choices=["auto", "iss", "tinvest"])
        p.add_argument("--now", help="'YYYY-MM-DD HH:MM' МСК")
    p = sub.add_parser("universe"); u(p); p.set_defaults(fn=cmd_universe)
    p = sub.add_parser("orb"); u(p); p.add_argument("--at"); p.add_argument("--start")
    p.add_argument("--range", type=int, default=15); p.add_argument("--short", action="store_true"); p.set_defaults(fn=cmd_orb)
    p = sub.add_parser("swing"); u(p); p.set_defaults(fn=cmd_swing)
    p = sub.add_parser("intraday"); u(p); p.add_argument("--tf", default="15m", choices=sorted(moex.INTRADAY)); p.set_defaults(fn=cmd_intraday)
    p = sub.add_parser("ta"); u(p); p.add_argument("--tf", default="1h", choices=list(moex.TF))
    p.add_argument("--top", type=int, default=10); p.add_argument("--long-only", action="store_true"); p.set_defaults(fn=cmd_ta)
    p = sub.add_parser("find"); p.add_argument("query"); p.set_defaults(fn=cmd_find)
    a = ap.parse_args()
    try:
        a.fn(a)
    except C.NetError as e:
        C.die(str(e))


if __name__ == "__main__":
    main()
