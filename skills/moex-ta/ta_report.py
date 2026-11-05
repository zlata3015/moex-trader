#!/usr/bin/env python3
"""moex-ta: технический анализ инструмента Мосбиржи.

  ta_report.py analyze SBER --tf 1h          разбор одного таймфрейма
  ta_report.py mtf SBER --mode intraday      мульти-таймфрейм: 1d/1h/15m/5m (swing: 1w/1d/4h/1h)
  ta_report.py levels SBER --tf 1d           уровни, пивоты, Фибоначчи
  ta_report.py chart SBER --tf 1h            HTML-график -> trader/charts/SBER-1h.html
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "moex-setup", "lib"))
import common as C  # noqa: E402
import moex  # noqa: E402
import ta  # noqa: E402

IND_NAMES = [
    ("ema20", "EMA 20"), ("ema50", "EMA 50"), ("ema200", "EMA 200"), ("vwap", "VWAP дня"),
    ("rsi14", "RSI 14"), ("macd", "MACD"), ("macd_signal", "MACD сигнальная"), ("macd_hist", "MACD гистограмма"),
    ("stoch_k", "Стохастик %K"), ("stoch_d", "Стохастик %D"), ("adx", "ADX"), ("plus_di", "+DI"), ("minus_di", "-DI"),
    ("bb_upper", "Боллинджер верх"), ("bb_mid", "Боллинджер середина"), ("bb_lower", "Боллинджер низ"),
    ("bb_width_pct", "Ширина полос, %"), ("atr14", "ATR 14"), ("atr_pct", "ATR, % цены"),
    ("supertrend", "Supertrend"), ("volume_ratio", "Объём / средний"),
]


def load(sym, tf, a):
    end = C.now_msk(a.now)
    return moex.candles(sym, tf, end=end, kind=a.kind, source=a.source, file=a.file)


def print_one(sym, tf, res, short=False):
    if "error" in res:
        print("%s %s: %s" % (sym, tf, res["error"]))
        return
    print("%s · %s · %s · цена %s" % (sym, tf, res["time"], C.px(res["price"])))
    print("Итог: %s (оценка %+d из ±100), тренд: %s" % (res["verdict"], res["score"], res["trend"]))
    if not short:
        print("\nИндикаторы:")
        for k, name in IND_NAMES:
            v = res["indicators"].get(k)
            if v is not None:
                print("  %-22s %s" % (name, C.num(v, 4)))
    s, r = res["support"], res["resistance"]
    print("\nБлижайшая поддержка: %s   сопротивление: %s" % (
        ("%s (касаний %d)" % (C.px(s["price"]), s["touches"])) if s else "—",
        ("%s (касаний %d)" % (C.px(r["price"]), r["touches"])) if r else "—"))
    if res["patterns"]:
        print("Свечные паттерны: " + ", ".join(res["patterns"]))
    print("\nСигналы:")
    for sg in sorted(res["signals"], key=lambda x: -abs(x["weight"])):
        mark = "▲" if sg["weight"] > 0 else "▼" if sg["weight"] < 0 else "•"
        print("  %s %s" % (mark, sg["text"]))


def cmd_analyze(a):
    bars = load(a.symbol, a.tf, a)
    res = ta.analyze(bars, intraday=a.tf in moex.INTRADAY)
    if a.json:
        C.print_json(res)
    else:
        print_one(a.symbol, a.tf, res)
    save(a.symbol, {a.tf: res})


def cmd_mtf(a):
    per = {}
    for tf, w, role in ta.MTF[a.mode]:
        try:
            bars = load(a.symbol, tf, a)
            per[tf] = ta.analyze(bars, intraday=tf in moex.INTRADAY)
        except C.NetError as e:
            per[tf] = {"error": str(e)}
    comb = ta.combine(per, a.mode)
    out = {"symbol": a.symbol, "mode": a.mode, "combined": comb, "timeframes": per,
           "at": C.fmt_dt(C.now_msk(a.now))}
    save(a.symbol, per, comb, a.mode)
    if a.json:
        C.print_json(out)
        return
    title = "внутри дня" if a.mode == "intraday" else "свинг"
    print("%s — мульти-таймфрейм (%s)\n" % (a.symbol, title))
    rows = []
    for tf, w, role in ta.MTF[a.mode]:
        r = per.get(tf, {})
        if "error" in r:
            rows.append([tf, role, "—", "—", "—", r["error"][:60]])
            continue
        ind = r["indicators"]
        rows.append([tf, role, "%+d" % r["score"], r["verdict"], r["trend"],
                     "RSI %s · ADX %s · ATR %s%%" % (C.num(ind["rsi14"], 0), C.num(ind["adx"], 0), C.num(ind["atr_pct"], 2))])
    print(C.table(rows, ["ТФ", "роль", "оценка", "вывод", "тренд", "ключевое"]))
    print("\nСводно: %s (оценка %+d)%s" % (comb["verdict"], comb["score"],
                                          " — все таймфреймы смотрят в одну сторону" if comb["aligned"] else ""))
    entry_tf = ta.MTF[a.mode][-1][0]
    if entry_tf in per and "error" not in per[entry_tf]:
        print("\nТаймфрейм входа %s:" % entry_tf)
        print_one(a.symbol, entry_tf, per[entry_tf], short=True)


def cmd_levels(a):
    bars = load(a.symbol, a.tf, a)
    lv = ta.levels(bars, max_levels=10)
    price = bars[-1]["c"]
    print("%s · %s · цена %s\n" % (a.symbol, a.tf, C.px(price)))
    rows = [[C.px(x["price"]), x["type"], x["touches"], x["strength"],
             ("зеркальный " if x.get("flip") else "") + (x.get("note") or ""),
             C.pct((x["price"] - price) / price * 100)] for x in reversed(lv)]
    print(C.table(rows, ["уровень", "тип", "касаний", "сила", "примечание", "до цены"]))
    # пивоты по предыдущему дню
    try:
        d = moex.candles(a.symbol, "1d", end=C.now_msk(a.now), days=10, kind=a.kind, source="iss") if not a.file else None
    except C.NetError:
        d = None
    if d and len(d) >= 2:
        today = C.now_msk(a.now).strftime("%Y-%m-%d")
        prev = d[-2] if d[-1]["t"][:10] == today else d[-1]
        pv = ta.pivots(prev["h"], prev["l"], prev["c"])
        print("\nПивоты (по дню %s): " % prev["t"][:10] + "  ".join("%s %s" % (k, C.px(pv[k]))
                                                            for k in ("S3", "S2", "S1", "P", "R1", "R2", "R3")))
    fb = ta.fibonacci(bars)
    print("Фибоначчи (движение %s, %s → %s): " % (fb["direction"], C.px(fb["low"]), C.px(fb["high"])) +
          "  ".join("%s %s" % (k, C.px(v)) for k, v in fb["levels"].items()))


def cmd_chart(a):
    bars = load(a.symbol, a.tf, a)
    lv = ta.levels(bars[-max(a.bars, 120):])
    plan = None
    if a.plan:
        p = C.load_json(C.tpath("plan.json"))
        if p and p.get("symbol") == a.symbol:
            plan = p
    path = C.tpath("charts", "%s-%s.html" % (a.symbol, a.tf))
    import chart
    res = ta.analyze(bars, intraday=a.tf in moex.INTRADAY)
    title = "%s · %s · %s · %s (%+d)" % (a.symbol, a.tf, bars[-1]["t"], res.get("verdict", ""), res.get("score", 0))
    chart.render(bars, title, path, n=a.bars, levels=lv, plan=plan)
    print("График: %s" % path)


def save(sym, per, comb=None, mode=None):
    p = C.tpath("ta", "%s.json" % sym)
    cur = C.load_json(p, {}) or {}
    cur.setdefault("timeframes", {}).update(per)
    if comb:
        cur["combined"] = comb
        cur["mode"] = mode
    cur["symbol"] = sym
    C.save_json(p, cur)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common_args(p):
        p.add_argument("symbol")
        p.add_argument("--kind", choices=list(moex.KINDS), help="вид инструмента, если тикер неоднозначен")
        p.add_argument("--source", choices=["auto", "iss", "tinvest"])
        p.add_argument("--file", help="свечи из файла (CSV Финама, JSON ISS)")
        p.add_argument("--now", help="'YYYY-MM-DD HH:MM' МСК — разбор на момент в прошлом")
        p.add_argument("--json", action="store_true")
    p = sub.add_parser("analyze"); common_args(p); p.add_argument("--tf", default="1h", choices=list(moex.TF))
    p.set_defaults(fn=cmd_analyze)
    p = sub.add_parser("mtf"); common_args(p); p.add_argument("--mode", default="intraday", choices=list(ta.MTF))
    p.set_defaults(fn=cmd_mtf)
    p = sub.add_parser("levels"); common_args(p); p.add_argument("--tf", default="1d", choices=list(moex.TF))
    p.set_defaults(fn=cmd_levels)
    p = sub.add_parser("chart"); common_args(p); p.add_argument("--tf", default="1h", choices=list(moex.TF))
    p.add_argument("--bars", type=int, default=150); p.add_argument("--plan", action="store_true", help="нанести вход/стоп/цель из plan.json")
    p.set_defaults(fn=cmd_chart)
    a = ap.parse_args()
    try:
        a.fn(a)
    except C.NetError as e:
        C.die(str(e))


if __name__ == "__main__":
    main()
