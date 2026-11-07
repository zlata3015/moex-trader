#!/usr/bin/env python3
"""moex-trend: приоритетные бумаги и поиск сигналов H10. Учебный расчёт, заявок не отправляет.

  priority.py list            приоритетные акции на покупку: закрытый день, ликвидные бумаги
  priority.py h10             сигналы H10 на фьючерсах и отметка приоритета
  priority.py all [--json]    оба списка

Приоритет покупки: оборот за 60 дней от 100 млн ₽, IMOEX выше EMA50, неделя вверх, месяц не вниз, выше EMA20,
MACD выше 0, RSI 14 от 50 до 70. Продажа зеркально, RSI от 30 до 50. Сила к индексу только показывается: критерий
не прошёл проверку Z05. Набор на истории преимущества перед фондом не доказал, это журнал для песочницы.
"""
import argparse
import importlib.util
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "moex-setup", "lib"))
import common as C  # noqa: E402
import moex  # noqa: E402
import ta  # noqa: E402
import trend  # noqa: E402

LIST = "SBER GAZP LKOH ROSN GMKN NVTK TATN VTBR MGNT CHMF".split()
PREFIX = dict(zip(LIST, "SR GZ LK RN GK NK TT VB MN CH".split()))
STOP, TAKE, RISK = 0.03, 0.15, 0.02


def market_dir(closes):
    """1 рынок растёт, -1 падает, 0 направления нет: IMOEX и наклон EMA50 за 5 дней."""
    e = ta.ema(closes, 50)
    if len(closes) < 56:
        return 0
    if closes[-1] > e[-1] > e[-6]:
        return 1
    if closes[-1] < e[-1] < e[-6]:
        return -1
    return 0


def priority_flags(c, ix_ret60):
    """(покупка, продажа, сила к индексу) по закрытиям дня."""
    if len(c) < 61:
        return False, False, None
    e20, rsi, hist = ta.ema(c, 20)[-1], ta.rsi(c)[-1], ta.macd(c)[2][-1]
    if e20 is None or rsi is None or hist is None:
        return False, False, None
    return (c[-1] > e20 and hist > 0 and 50 <= rsi <= 70,
            c[-1] < e20 and hist < 0 and 30 <= rsi <= 50, c[-1] / c[-61] - 1 - ix_ret60)


def daily_from_hours(bars):
    """Дни из часовых свечей 10–18, как в бэктесте H10."""
    by = {}
    for b in bars:
        d, hr = b["t"][:10], int(b["t"][11:13])
        if 10 <= hr <= 18 and date.fromisoformat(d).weekday() < 5:
            by.setdefault(d, []).append(b)
    return [dict(t=d, o=x[0]["o"], h=max(y["h"] for y in x), l=min(y["l"] for y in x), c=x[-1]["c"]) for d, x in sorted(by.items())]


def h10_side(days):
    """Сигнал H10 на последнем дне: 1 покупка, -1 продажа, 0 нет. days: o, h, l, c."""
    if len(days) < 71:
        return 0
    o = [x["o"] for x in days]
    h = [x["h"] for x in days]
    lo = [x["l"] for x in days]
    c = [x["c"] for x in days]
    e50, e = ta.ema(c, 50)[-1], ta.ema(c, 10)
    a, pc = None, None
    for x in days:
        t = x["h"] - x["l"] if pc is None else max(x["h"] - x["l"], abs(x["h"] - pc), abs(x["l"] - pc))
        pc = x["c"]
        a = t if a is None else (a * 13 + t) / 14
    if (c[-1] > e50 and c[-1] > c[-61] and
            ((e[-1] > e[-6] and lo[-1] <= e[-1] + .25 * a and c[-1] > e[-1] and c[-1] > o[-1]) or c[-1] > max(h[-21:-1]))):
        return 1
    if (c[-1] < e50 and c[-1] < c[-61] and
            ((e[-1] < e[-6] and h[-1] >= e[-1] - .25 * a and c[-1] < e[-1] and c[-1] < o[-1]) or c[-1] < min(lo[-21:-1]))):
        return -1
    return 0


def closed_day(now):
    """Последний закрытый день: основная сессия считается закрытой после 19:00 МСК."""
    return now.strftime("%Y-%m-%d") if now.hour >= 19 else (now.date() - timedelta(1)).isoformat()


def ix_state(cut):
    b = [x for x in moex.candles("IMOEX", "1d", days=500, source="iss") if x["t"][:10] <= cut]
    c = [x["c"] for x in b]
    return {"date": b[-1]["t"][:10], "close": c[-1], "dir": market_dir(c), "above": c[-1] > ta.ema(c, 50)[-1], "ret60": c[-1] / c[-61] - 1,
            "dates": [x["t"][:10] for x in b], "closes": c}


def front_contract(sym, day):
    js = moex._get("/engines/futures/markets/forts/securities.json", {"iss.only": "securities", "securities.columns": "SECID,LASTTRADEDATE"})
    rows = [r for r in moex.table(js, "securities") if r["SECID"].startswith(PREFIX[sym]) and len(r["SECID"]) == 4]
    rows = sorted((r for r in rows if (date.fromisoformat(r["LASTTRADEDATE"]) - timedelta(7)).isoformat() >= day), key=lambda r: r["LASTTRADEDATE"])
    return rows[0] if rows else None


def h10(ix, cut):
    from concurrent.futures import ThreadPoolExecutor

    def one(s):
        days = [x for x in daily_from_hours(moex.candles(s, "1h", days=330, source="iss")) if x["t"] <= cut]
        c = [x["c"] for x in days]
        side = h10_side(days)
        buy, sell, rs = priority_flags(c, ix["ret60"])
        active = side != 0 and side == ix["dir"]
        fr = front_contract(s, (date.fromisoformat(cut) + timedelta(1)).isoformat()) if active else None
        return dict(symbol=s, date=days[-1]["t"], side=side, active=active, contract=fr and fr["SECID"], last_trade=fr and fr["LASTTRADEDATE"],
                    priority=bool((side > 0 and buy) or (side < 0 and sell)), rs=rs, close=c[-1])

    with ThreadPoolExecutor(5) as ex:
        return list(ex.map(one, LIST))


def stock_row(sym, ix, cut):
    b = [x for x in moex.candles(sym, "1d", days=500, source="iss") if x["t"][:10] <= cut]
    if len(b) < 205:
        return None
    b, _, _ = moex.adjust_splits(b, [])
    c = [x["c"] for x in b]
    v = [x.get("val") or x.get("v", 0) * x["c"] for x in b]
    wk = trend.resample_period(b, "W")
    mo = trend.resample_period(b, "M")
    wd = trend.trend_state(wk, "1w")["dir"]
    md = trend.trend_state(mo, "1M")["dir"]
    buy, _, rs = priority_flags(c, ix["ret60"])
    e = ta.ema(c, 10)
    atr = ta.atr([x["h"] for x in b], [x["l"] for x in b], c)[-1]
    x = b[-1]
    pull = e[-1] > e[-6] and x["l"] <= e[-1] + .25 * atr and x["c"] > e[-1] and x["c"] > x["o"]
    brk = x["c"] > max(y["h"] for y in b[-21:-1])
    ok = buy and ix["above"] and wd == "up" and md in ("up", "flat") and sum(v[-60:]) / 60 >= 1e8
    return dict(symbol=sym, date=x["t"][:10], close=x["c"], week=wd, month=md, priority=bool(ok), signal="откат" if pull else "пробой" if brk else "",
                rs=rs, stop=x["c"] - 2 * atr, turnover=sum(v[-60:]) / 60)


def universe(limit):
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "moex-scanner", "scan.py")
    sp = importlib.util.spec_from_file_location("scan", p)
    scan = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(scan)
    return [u["symbol"] for u in scan.universe("shares", C.config()["min_turnover_rub"], limit)]


def show_list(rows):
    rows = sorted((r for r in rows if r["priority"]), key=lambda r: -r["rs"])
    print(C.table([[r["symbol"], C.px(r["close"]), "%+.1f%%" % (r["rs"] * 100), r["signal"] or "ждём", C.px(r["stop"])] for r in rows],
                  ["бумага", "цена", "сила к IMOEX 60д", "сигнал", "стоп 2 ATR"]) if rows else "Приоритетных бумаг нет")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["list", "h10", "all"])
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    now = C.now_msk()
    cut = closed_day(now)
    try:
        ix = ix_state(cut)
    except C.NetError as e:
        C.die("ISS недоступен: %s" % e)
    res = {"cut": ix["date"], "market": ix["dir"], "imoex": ix["close"]}
    if a.cmd in ("list", "all"):
        rows, bad = [], []
        for s in universe(a.limit):
            try:
                r = stock_row(s, ix, cut)
            except (C.NetError, KeyError, IndexError, ValueError, TypeError):
                bad.append(s)
                continue
            if r and r["date"] == ix["date"]:
                rows.append(r)
        res.update(checked=len(rows), failed=bad, priority=sorted((r for r in rows if r["priority"]), key=lambda r: -r["rs"]))
    if a.cmd in ("h10", "all"):
        res["h10"] = h10(ix, cut)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
        return
    print("День %s, IMOEX %s, рынок: %s" % (res["cut"], C.px(ix["close"]), {1: "растёт", -1: "падает", 0: "направления нет"}[ix["dir"]]))
    if "priority" in res:
        print("\nПриоритет покупки, проверено %d, сбой %d" % (res["checked"], len(res["failed"])))
        show_list(res["priority"])
    if "h10" in res:
        act = [r for r in res["h10"] if r["active"]]
        print("\nСигналы H10: %s" % ("нет" if not act else ""))
        for r in act:
            print("  %s %s %s, приоритет: %s, контракт %s, до %s" % (r["symbol"], "покупка" if r["side"] > 0 else "продажа", C.px(r["close"]),
                                                                  "да" if r["priority"] else "нет", r["contract"], r["last_trade"]))
    print("\nЗаявки не отправляются. Цифры — расчёт, а не обещание доходности.")


if __name__ == "__main__":
    main()
