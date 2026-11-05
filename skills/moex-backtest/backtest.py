#!/usr/bin/env python3
"""moex-backtest: проверка стратегии на истории с комиссией и проскальзыванием.

  backtest.py orb SBER --days 120                       пробой диапазона открытия
  backtest.py orb SBER --trend --regime --volume 1.5    с фильтрами: тренд дня, режим IMOEX, объём на пробое
  backtest.py swing SBER --setup pullback               дневные сетапы: breakout, pullback, rsi-bounce, squeeze, golden-cross
  backtest.py optimize SBER --strategy orb              подбор параметров на первых 70% истории, проверка на последних 30%
  backtest.py universe --strategy orb --limit 20        прогон по ликвидным бумагам, рейтинг по мат. ожиданию
"""
import argparse
import itertools
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "moex-setup", "lib"))
import common as C  # noqa: E402
import moex  # noqa: E402
import sim  # noqa: E402
import setups as S  # noqa: E402


def data(sym, a, strategy):
    end = C.now_msk(a.now)
    spec = moex.spec(sym, a.kind)
    if strategy == "orb":
        bars = moex.candles(sym, a.tf, days=a.days, end=end, kind=a.kind, source="iss")
        daily = moex.candles(sym, "1d", days=a.days + 120, end=end, kind=a.kind, source="iss")
    else:
        bars = None
        daily = moex.candles(sym, "1d", days=a.days, end=end, kind=a.kind, source="iss")
    regime = None
    if getattr(a, "regime", False):
        idx = moex.candles("IMOEX", "1d", days=(a.days + 120), end=end, kind="index", source="iss")
        regime = sim.regime_map(idx)
    return spec, bars, daily, regime


def run_orb(spec, bars, daily, regime, cfg, p):
    tfm = {"1m": 1, "5m": 5, "10m": 10, "15m": 15}[p["tf"]]
    return sim.backtest_orb(bars, spec, cfg, tfm, p["start"], p["range"], p["stop"], p["target"], cfg["flat_by"],
                            p["last_entry"], daily=daily, trend_filter=p["trend"], regime=regime if p["regime"] else None,
                            volume_filter=p["volume"], allow_short=p["short"])


def run_swing(spec, daily, regime, cfg, p):
    return sim.backtest_swing(daily, spec, cfg, p["setup"], p["stop_atr"], p["target"], p["hold"],
                              regime=regime if p["regime"] else None)


def fmt_stats(st, risk_rub):
    if not st.get("trades"):
        return "сделок нет"
    return ("сделок %d · прибыльных %s · среднее %s · профит-фактор %s · итого %s (≈ %s при риске %s на сделку) · "
            "макс. просадка %s" % (st["trades"], C.pct(st["winrate"], 0), r(st["avg_r"]),
                                   C.num(st["profit_factor"], 2) if st["profit_factor"] else "∞",
                                   r(st["total_r"]), C.rub(st["total_r"] * risk_rub, 0), C.rub(risk_rub, 0), r(st["max_dd_r"])))


def r(x):
    return ("+" if x >= 0 else "") + C.num(x, 2) + "R"


def judge(st, oos=None):
    if not st.get("trades") or st["trades"] < 20:
        return "мало сделок для вывода (нужно хотя бы 20–30)"
    t = st.get("t_stat") or 0
    if st["avg_r"] > 0.05 and t > 2:
        v = "есть статистически заметное преимущество"
    elif st["avg_r"] > 0.05:
        v = "в плюсе, но может быть случайностью (t = %s)" % C.num(t, 1)
    else:
        v = "преимущества нет — после издержек стратегия не зарабатывает"
    if oos is not None:
        if oos.get("trades", 0) >= 10 and oos["avg_r"] <= 0:
            v += "; на отложенном периоде НЕ подтвердилось — вероятна подгонка"
        elif oos.get("trades", 0) >= 10:
            v += "; на отложенном периоде подтвердилось"
    return v


def params_from(a, strategy):
    cfg = C.config()
    if strategy == "orb":
        return {"tf": a.tf, "start": a.start or cfg["orb_start"], "range": a.range, "stop": a.stop, "target": a.target,
                "last_entry": a.last_entry, "trend": a.trend, "regime": a.regime, "volume": a.volume, "short": a.short}
    return {"setup": a.setup, "stop_atr": a.stop_atr, "target": a.target, "hold": a.hold, "regime": a.regime}


def record(sym, strategy, p, st, oos=None):
    path = C.tpath("backtest", "summary.json")
    s = C.load_json(path, {}) or {}
    key = "%s|%s" % (sym, strategy if strategy == "orb" else "swing:" + p.get("setup", ""))
    s[key] = {"params": p, "stats": st, "oos": oos, "at": C.fmt_dt(C.now_msk())}
    C.save_json(path, s)


def cmd_single(a, strategy):
    cfg = C.config()
    risk_rub = C.money_limit(cfg["max_loss_per_trade"], cfg["account_size"])
    spec, bars, daily, regime = data(a.symbol, a, strategy)
    p = params_from(a, strategy)
    tr = run_orb(spec, bars, daily, regime, cfg, p) if strategy == "orb" else run_swing(spec, daily, regime, cfg, p)
    st = sim.stats([t["r"] for t in tr])
    span = (tr[0]["date"] + " … " + tr[-1]["date"]) if tr else ""
    print("Бэктест %s · %s · %s" % (a.symbol, "пробой диапазона открытия" if strategy == "orb" else S.SETUPS[a.setup], span))
    print("Параметры: " + ", ".join("%s=%s" % kv for kv in p.items()))
    print("Издержки: комиссия %s%% за сторону + %d шаг(а) проскальзывания" % (C.num(cfg["fee_pct"], 3), cfg["slippage_ticks"])
          if spec["kind"] != "futures" else "Издержки: %s за контракт за сторону + проскальзывание" % C.rub(cfg["futures_fee_rub"]))
    print(fmt_stats(st, risk_rub))
    if tr:
        avg_cost = sum(t["cost_r"] for t in tr) / len(tr)
        print("Издержки съедают в среднем %sR на сделку" % C.num(avg_cost, 2))
    print("Вывод: " + judge(st))
    if a.trades:
        print(C.table([[t["date"], t.get("time", ""), t["dir"], C.px(t["entry"]), C.px(t["exit"]), t["outcome"], r(t["r"])]
                       for t in tr], ["дата", "время", "напр.", "вход", "выход", "исход", "R"]))
    record(a.symbol, strategy, p, st)
    C.save_json(C.tpath("backtest", "%s-%s.json" % (a.symbol, strategy)), {"params": p, "stats": st, "trades": tr})


def grid(strategy, a):
    if strategy == "orb":
        for stop, tgt, rng, trend, vol in itertools.product(["or-mid", "or-low", "atr:1.0"], [1.0, 1.5, 2.0, 3.0],
                                                             [15, 30], [False, True], [0.0, 1.5]):
            yield {"tf": a.tf, "start": a.start or C.config()["orb_start"], "range": rng, "stop": stop, "target": tgt,
                   "last_entry": a.last_entry, "trend": trend, "regime": a.regime, "volume": vol, "short": a.short}
    else:
        for setup, sa, tgt, hold in itertools.product(list(S.DETECTORS.keys() - {"vwap-reclaim"}), [1.5, 2.0, 3.0],
                                                      [1.5, 2.0, 3.0], [5, 10, 20]):
            yield {"setup": setup, "stop_atr": sa, "target": tgt, "hold": hold, "regime": a.regime}


def optimize(sym, a, strategy, quiet=False):
    cfg = C.config()
    spec, bars, daily, regime = data(sym, a, strategy)
    if strategy == "orb":
        dates = sorted({b["t"][:10] for b in bars})
    else:
        dates = [b["t"][:10] for b in daily]
    cut = dates[int(len(dates) * 0.7)]
    best = None
    for p in grid(strategy, a):
        tr = run_orb(spec, bars, daily, regime, cfg, p) if strategy == "orb" else run_swing(spec, daily, regime, cfg, p)
        ins = [t["r"] for t in tr if t["date"] < cut]
        oos = [t["r"] for t in tr if t["date"] >= cut]
        si = sim.stats(ins)
        if si.get("trades", 0) < a.min_trades:
            continue
        score = si["avg_r"] * (si["trades"] ** 0.5)  # мат. ожидание с поправкой на число сделок
        if best is None or score > best[0]:
            best = (score, p, si, sim.stats(oos), sim.stats(ins + oos))
    if not best:
        if not quiet:
            print("%s: ни одна комбинация не дала %d сделок на обучающем периоде" % (sym, a.min_trades))
        return None
    _, p, si, so, sa = best
    if not quiet:
        risk_rub = C.money_limit(cfg["max_loss_per_trade"], cfg["account_size"])
        print("Оптимизация %s · %s · обучение до %s, проверка с %s" % (sym, strategy, cut, cut))
        print("Лучшие параметры: " + ", ".join("%s=%s" % kv for kv in p.items()))
        print("Обучение:  " + fmt_stats(si, risk_rub))
        print("Проверка:  " + fmt_stats(so, risk_rub))
        print("Вывод: " + judge(si, so))
        print("Важно: перебрано много комбинаций, поэтому результат на обучении завышен. Смотрите на «Проверку».")
    record(sym, strategy, p, sa, so)
    return p, si, so


def cmd_universe(a):
    import importlib.util
    spec_ = importlib.util.spec_from_file_location("scan", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "moex-scanner", "scan.py"))
    scan = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(scan)
    cfg = C.config()
    uni = scan.universe(a.kind, cfg["min_turnover_rub"], a.limit)
    rows = []
    for u in uni:
        a.symbol, a.kind = u["symbol"], u["kind"]
        try:
            res = optimize(u["symbol"], a, a.strategy, quiet=True)
        except C.NetError as e:
            sys.stderr.write("%s: %s\n" % (u["symbol"], e))
            continue
        if res:
            p, si, so = res
            rows.append((so.get("avg_r", -9) if so.get("trades", 0) >= 5 else -9, u["symbol"], p, si, so))
    rows.sort(key=lambda x: -x[0])
    print("Рейтинг по результату на отложенном периоде (%s):" % a.strategy)
    print(C.table([[s, ", ".join("%s=%s" % (k, v) for k, v in p.items() if k in ("setup", "stop", "stop_atr", "target", "range", "trend", "volume", "hold")),
                    r(si["avg_r"]), si["trades"], r(so["avg_r"]) if so.get("trades") else "—", so.get("trades", 0)]
                   for _, s, p, si, so in rows],
                  ["тикер", "параметры", "обучение", "сд.", "проверка", "сд."]))
    print("\nТорговать стоит только то, что в плюсе на проверке и набрало хотя бы 20 сделок в сумме.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def base(p, sym=True):
        if sym:
            p.add_argument("symbol")
        p.add_argument("--kind", choices=list(moex.KINDS)); p.add_argument("--now")
        p.add_argument("--regime", action="store_true", help="лонг только когда IMOEX выше EMA50")
        p.add_argument("--min-trades", type=int, default=15)

    def orb_args(p):
        p.add_argument("--days", type=int, default=120); p.add_argument("--tf", default="5m", choices=["1m", "5m", "10m", "15m"])
        p.add_argument("--start"); p.add_argument("--range", type=int, default=15)
        p.add_argument("--stop", default="or-mid"); p.add_argument("--target", type=float, default=1.5)
        p.add_argument("--last-entry", default="12:00"); p.add_argument("--trend", action="store_true")
        p.add_argument("--volume", type=float, default=0.0); p.add_argument("--short", action="store_true")

    def swing_args(p, days=1500):
        p.add_argument("--setup", default="breakout", choices=list(S.DETECTORS))
        p.add_argument("--stop-atr", type=float, default=2.0); p.add_argument("--hold", type=int, default=10)

    p = sub.add_parser("orb"); base(p); orb_args(p); p.add_argument("--trades", action="store_true")
    p.set_defaults(fn=lambda a: cmd_single(a, "orb"))
    p = sub.add_parser("swing"); base(p); swing_args(p); p.add_argument("--days", type=int, default=1500)
    p.add_argument("--target", type=float, default=2.0); p.add_argument("--trades", action="store_true")
    p.set_defaults(fn=lambda a: cmd_single(a, "swing"))
    for name in ("optimize", "universe"):
        p = sub.add_parser(name); base(p, sym=(name == "optimize"))
        p.add_argument("--strategy", default="orb", choices=["orb", "swing"])
        orb_args(p); p.add_argument("--setup", default="breakout"); p.add_argument("--stop-atr", type=float, default=2.0)
        p.add_argument("--hold", type=int, default=10)
        if name == "universe":
            p.add_argument("--limit", type=int, default=20)
            p.set_defaults(fn=cmd_universe)
        else:
            p.set_defaults(fn=lambda a: optimize(a.symbol, a, a.strategy))
    import trend_bt as T
    p = sub.add_parser("trend", help="тренд сверху вниз + подтягиваемый стоп (дневки)"); T.add_args(p)
    p.set_defaults(fn=T.cmd_trend)
    p = sub.add_parser("trend-optimize"); T.add_args(p); p.set_defaults(fn=lambda a: T.optimize(a.symbol, a))
    p = sub.add_parser("trend-universe"); T.add_args(p, sym=False); p.set_defaults(fn=T.cmd_universe)
    p = sub.add_parser("trend-portfolio", help="один счёт по всем бумагам + фонд денежного рынка")
    T.add_args(p, sym=False); p.set_defaults(fn=T.cmd_portfolio)
    a = ap.parse_args()
    if getattr(a, "strategy", None) == "swing" and a.days == 120:
        a.days = 1500
    if a.cmd == "universe" and not a.kind:
        a.kind = "shares"
    try:
        a.fn(a)
    except C.NetError as e:
        C.die(str(e))


if __name__ == "__main__":
    main()
