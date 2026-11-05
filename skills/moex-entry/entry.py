#!/usr/bin/env python3
"""moex-entry: таймер входа — следит за сетапом бар за баром.

  entry.py check SBER --setup orb                 ждём / сработал / истёк
  entry.py check SBER --setup breakout --level 330 --tf 15m
  entry.py watch SBER --setup orb --every 30      проверять каждые 30 с, пока не сработает или не истечёт
Сетапы: orb, breakout, pullback, rsi-bounce, squeeze, vwap-reclaim.
"""
import argparse
import os
import sys
import time
from datetime import timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "moex-setup", "lib"))
import common as C  # noqa: E402
import moex  # noqa: E402
import setups as S  # noqa: E402
import ta  # noqa: E402

TF_MIN = {"1m": 1, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "1h": 60, "4h": 240}


def check(a, now=None):
    cfg = C.config()
    now = now or C.now_msk(a.now)
    tf = a.tf
    bars = moex.candles(a.symbol, tf, days=a.days, end=now, kind=a.kind, source=a.source, file=a.file)
    if tf in TF_MIN:
        bars = [b for b in bars if S.bar_end(b, TF_MIN[tf]) <= now]   # только закрытые бары
    else:
        close = C.hm(cfg["sessions"]["shares"]["main"][1])
        cutoff = now.strftime("%Y-%m-%d") if C.minutes_of(now) >= close else (now.strftime("%Y-%m-%d") + "!")
        bars = [b for b in bars if b["t"][:10] < cutoff[:10] or (b["t"][:10] == cutoff and not cutoff.endswith("!"))]
    if not bars:
        return {"symbol": a.symbol, "status": "нет данных", "at": C.fmt_dt(now)}
    today = now.strftime("%Y-%m-%d")
    res = {"symbol": a.symbol, "setup": a.setup, "tf": tf, "at": C.fmt_dt(now), "last_bar": bars[-1]["t"],
           "last_price": bars[-1]["c"]}
    intraday = tf in TF_MIN
    late = intraday and C.minutes_of(now) >= C.hm(cfg["no_entry_after"])
    if a.setup == "orb":
        day = [b for b in bars if b["t"][:10] == today]
        r = S.orb(day, TF_MIN.get(tf, 5), a.start or cfg["orb_start"], a.range, at=now,
                  allow_short=cfg["allow_short"])
        res.update(r)
        if r.get("status") == "ждём":
            if day and day[-1]["c"] < r["range_low"] and not cfg["allow_short"]:
                res["status"] = "истёк"
                res["why"] = "цена ушла под диапазон — пробой вверх отменяется"
            elif late:
                res["status"] = "истёк"
                res["why"] = "после %s новые входы не открываем" % cfg["no_entry_after"]
    else:
        fn = S.DETECTORS[a.setup]
        r = fn(bars, level=a.level) if a.setup == "breakout" and a.level else fn(bars)
        if r:
            res.update(r)
            res["status"] = "сработал"
        else:
            res["status"] = "истёк" if late else "ждём"
            if late:
                res["why"] = "после %s новые входы не открываем" % cfg["no_entry_after"]
            if a.setup == "breakout":
                lvl = a.level or max(b["h"] for b in bars[-21:-1])
                res["level"] = lvl
                res["distance_pct"] = (lvl - bars[-1]["c"]) / bars[-1]["c"] * 100
    # подтверждение техническим анализом
    an = ta.analyze(bars, intraday=intraday)
    if "error" not in an:
        res["ta"] = {"score": an["score"], "verdict": an["verdict"], "trend": an["trend"],
                     "rsi": an["indicators"]["rsi14"], "vwap": an["indicators"]["vwap"],
                     "volume_ratio": an["indicators"]["volume_ratio"]}
    return res


def show(r):
    st = r.get("status")
    icon = {"сработал": "✅", "ждём": "⏳", "истёк": "⛔"}.get(st, "•")
    print("%s %s · %s · %s · %s" % (icon, r["symbol"], S.SETUPS.get(r.get("setup"), r.get("setup")), r.get("tf"), st.upper() if st else ""))
    print("  время проверки %s, последний закрытый бар %s, цена %s" % (r["at"][11:], r["last_bar"][11:] or r["last_bar"], C.px(r["last_price"])))
    if r.get("range_high") is not None:
        print("  диапазон открытия %s – %s (середина %s)" % (C.px(r["range_low"]), C.px(r["range_high"]), C.px(r["range_mid"])))
    if st == "сработал":
        print("  сигнал: бар %s закрылся на %s%s" % (r["trigger_time"][11:] or r["trigger_time"], C.px(r["trigger_price"]),
                                                  (" (%s за границей)" % C.pct(r["over_pct"])) if r.get("over_pct") else ""))
    if r.get("why"):
        print("  " + r["why"])
    if r.get("distance_pct") is not None and st == "ждём":
        print("  до уровня %s: %s" % (C.px(r["level"]), C.pct(r["distance_pct"])))
    t = r.get("ta")
    if t:
        line = "  ТА: %s (%+d), тренд %s, RSI %s" % (t["verdict"], t["score"], t["trend"], C.num(t["rsi"], 0))
        if t.get("vwap"):
            line += ", цена %s VWAP" % ("выше" if r["last_price"] > t["vwap"] else "ниже")
        if t.get("volume_ratio"):
            line += ", объём ×%s к среднему" % C.num(t["volume_ratio"], 1)
        print(line)
        if st == "сработал" and t["score"] <= -15:
            print("  ⚠ технический анализ против сделки — подумайте дважды")


def cmd_check(a):
    r = check(a)
    show(r)
    C.save_json(C.tpath("entry.json"), r)


def cmd_watch(a):
    t_end = time.time() + a.max_minutes * 60
    last = None
    while True:
        r = check(a)
        if r.get("last_bar") != last:
            show(r)
            last = r.get("last_bar")
        C.save_json(C.tpath("entry.json"), r)
        if r["status"] in ("сработал", "истёк") or time.time() > t_end or a.now:
            break
        time.sleep(a.every)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("check", cmd_check), ("watch", cmd_watch)):
        p = sub.add_parser(name)
        p.add_argument("symbol")
        p.add_argument("--setup", default="orb", choices=["orb"] + list(S.DETECTORS))
        p.add_argument("--tf", default="5m", choices=list(moex.TF))
        p.add_argument("--level", type=float, help="уровень для breakout")
        p.add_argument("--range", type=int, default=15, help="минут диапазона открытия")
        p.add_argument("--start", help="начало диапазона, по умолчанию orb_start из конфига")
        p.add_argument("--days", type=int, help="сколько дней истории загрузить")
        p.add_argument("--kind", choices=list(moex.KINDS))
        p.add_argument("--source", choices=["auto", "iss", "tinvest"])
        p.add_argument("--file")
        p.add_argument("--now", help="'YYYY-MM-DD HH:MM' МСК")
        if name == "watch":
            p.add_argument("--every", type=int, default=30)
            p.add_argument("--max-minutes", type=int, default=120)
        p.set_defaults(fn=fn)
    a = ap.parse_args()
    if a.days is None:
        a.days = {"1m": 2, "5m": 6, "10m": 8, "15m": 10, "30m": 20, "1h": 40}.get(a.tf)
    try:
        a.fn(a)
    except C.NetError as e:
        C.die(str(e))


if __name__ == "__main__":
    main()
