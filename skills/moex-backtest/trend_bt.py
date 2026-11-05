"""Бэктест стратегии «тренд сверху вниз + подтягиваемый стоп» на дневках (позиционная торговля).

Вызывается из backtest.py: команды trend, trend-optimize, trend-universe.
"""
import glob
import itertools
import os

import common as C
import moex
import sim
import trend

LOTS = {"SBER": 10, "SBERP": 10, "GAZP": 10, "GMKN": 10, "LKOH": 1, "YDEX": 1, "ROSN": 1, "VTBR": 1, "T": 1,
        "NVTK": 1, "TATN": 1, "PLZL": 1, "MGNT": 1, "CHMF": 1, "NLMK": 10, "ALRS": 10, "MOEX": 10, "AFLT": 10}


def r(x):
    return ("+" if x >= 0 else "") + C.num(x, 2) + "R"


def _minstep(bars):
    px = sorted({x for b in bars for x in (b["o"], b["h"], b["l"], b["c"])})
    diffs = [round(b - a, 6) for a, b in zip(px, px[1:]) if b - a > 1e-9]
    return min(diffs) if diffs else 0.01


def load(sym, a):
    """Дневки + месячные свечи: из папки с файлами (SYM_D.csv, SYM_M.csv) или из ISS."""
    if a.files_dir:
        dpath = os.path.join(a.files_dir, "%s_D.csv" % sym)
        mpath = os.path.join(a.files_dir, "%s_M.csv" % sym)
        daily = moex.load_file(dpath)
        monthly = moex.load_file(mpath) if os.path.exists(mpath) else None
        spec = {"symbol": sym, "kind": "shares", "minstep": _minstep(daily[-60:]), "lot": LOTS.get(sym, 1)}
    else:
        end = C.now_msk(a.now)
        daily = moex.candles(sym, "1d", days=a.days, end=end, source="iss")
        try:
            monthly = moex.candles(sym, "1M", days=3650, end=end, source="iss")
        except C.NetError:
            monthly = None
        spec = moex.spec(sym)
    regime = None
    if a.regime:
        if a.files_dir and os.path.exists(os.path.join(a.files_dir, "IMOEX_D.csv")):
            idx = moex.load_file(os.path.join(a.files_dir, "IMOEX_D.csv"))
        else:
            idx = moex.candles("IMOEX", "1d", days=a.days + 120, end=C.now_msk(a.now), kind="index", source="iss")
        regime = sim.regime_map(idx)
    return daily, monthly, spec, regime


def params(a):
    return {"stop_atr": a.stop_atr, "trail_atr": a.trail_atr, "allow_short": a.short, "max_hold": a.hold,
            "setups": tuple(a.setups.split(",")), "use_month": not a.no_month}


def run(daily, monthly, spec, regime, cfg, p):
    return trend.backtest(daily, spec, cfg, monthly=monthly, stop_atr=p["stop_atr"], trail_atr=p["trail_atr"],
                          allow_short=p["allow_short"], max_hold=p["max_hold"], setups=p["setups"], regime=regime,
                          use_month=p["use_month"])


def fmt(st, risk_rub):
    if not st.get("trades"):
        return "сделок нет"
    return ("сделок %d · прибыльных %s · среднее %s · профит-фактор %s · итого %s (≈ %s при риске %s) · просадка %s" % (
        st["trades"], C.pct(st["winrate"], 0), r(st["avg_r"]),
        C.num(st["profit_factor"], 2) if st["profit_factor"] else "∞", r(st["total_r"]),
        C.rub(st["total_r"] * risk_rub, 0), C.rub(risk_rub, 0), r(st["max_dd_r"])))


def record(sym, p, st, oos=None):
    path = C.tpath("backtest", "summary.json")
    s = C.load_json(path, {}) or {}
    s["%s|trend" % sym] = {"params": {k: (list(v) if isinstance(v, tuple) else v) for k, v in p.items()},
                           "stats": st, "oos": oos, "at": C.fmt_dt(C.now_msk())}
    C.save_json(path, s)


def cmd_trend(a):
    cfg = C.config()
    risk_rub = C.money_limit(cfg["max_loss_per_trade"], cfg["account_size"])
    daily, monthly, spec, regime = load(a.symbol, a)
    p = params(a)
    tr = run(daily, monthly, spec, regime, cfg, p)
    st = sim.stats([t["r"] for t in tr])
    print("Тренд сверху вниз · %s · дневки %s … %s (%d дн.)%s" % (
        a.symbol, daily[0]["t"][:10], daily[-1]["t"][:10], len(daily), ", месяцы из файла" if monthly else ""))
    print("Параметры: стоп %s ATR, подтягиваемый стоп %s ATR, шорты %s, держать до %d дн., сетапы %s%s" % (
        C.num(p["stop_atr"], 1), C.num(p["trail_atr"], 1), "да" if p["allow_short"] else "нет", p["max_hold"],
        ", ".join(p["setups"]), ", фильтр IMOEX" if regime else ""))
    print(fmt(st, risk_rub))
    if tr:
        big = [t for t in tr if t["r"] >= 3]
        print("Средний удержанный ход: %s; лучший %s; сделок ≥ 3R: %d; издержки в среднем %sR" % (
            r(sum(t["r"] for t in tr) / len(tr)), r(max(t["r"] for t in tr)), len(big),
            C.num(sum(t["cost_r"] for t in tr) / len(tr), 2)))
    if a.trades:
        print(C.table([[t["date"], t["exit_date"], t["dir"], t["setup"].replace("trend-", ""), C.px(t["entry"]), C.px(t["exit"]),
                        t["outcome"], t["held"], r(t["r"]), C.num(t["mfe_r"], 1)] for t in tr],
                      ["вход", "выход", "напр.", "сетап", "цена входа", "цена выхода", "исход", "дн.", "R", "макс.R"]))
    record(a.symbol, p, st)
    return tr, st


def grid(a):
    for sa, ta_, setups, month in itertools.product([1.5, 2.0, 3.0], [2.0, 3.0, 4.0],
                                                     ["trend-pullback", "trend-breakout", "trend-pullback,trend-breakout"],
                                                     [True, False]):
        yield {"stop_atr": sa, "trail_atr": ta_, "allow_short": a.short, "max_hold": a.hold,
               "setups": tuple(setups.split(",")), "use_month": month}


def optimize(sym, a, quiet=False):
    cfg = C.config()
    daily, monthly, spec, regime = load(sym, a)
    cut = daily[int(len(daily) * 0.7)]["t"][:10]
    best = None
    for p in grid(a):
        tr = run(daily, monthly, spec, regime, cfg, p)
        ins = [t["r"] for t in tr if t["date"] < cut]
        oos = [t["r"] for t in tr if t["date"] >= cut]
        si = sim.stats(ins)
        if si.get("trades", 0) < a.min_trades:
            continue
        score = si["avg_r"] * si["trades"] ** 0.5
        if best is None or score > best[0]:
            best = (score, p, si, sim.stats(oos), sim.stats(ins + oos))
    if not best:
        if not quiet:
            print("%s: ни одна комбинация не дала %d сделок на обучающем отрезке" % (sym, a.min_trades))
        return None
    _, p, si, so, sa = best
    if not quiet:
        risk_rub = C.money_limit(cfg["max_loss_per_trade"], cfg["account_size"])
        print("Оптимизация тренда · %s · обучение до %s, проверка с %s" % (sym, cut, cut))
        print("Лучшие параметры: стоп %s ATR, трейлинг %s ATR, сетапы %s, месячный фильтр %s" % (
            p["stop_atr"], p["trail_atr"], ", ".join(p["setups"]), "да" if p["use_month"] else "нет"))
        print("Обучение: " + fmt(si, risk_rub))
        print("Проверка: " + fmt(so, risk_rub))
    record(sym, p, sa, so)
    return p, si, so, sa


def symbols(a):
    if a.files_dir:
        return sorted(os.path.basename(f)[:-6] for f in glob.glob(os.path.join(a.files_dir, "*_D.csv"))
                      if not os.path.basename(f).startswith("IMOEX"))
    import importlib.util
    sp = importlib.util.spec_from_file_location("scan", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "moex-scanner", "scan.py"))
    scan = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(scan)
    return [u["symbol"] for u in scan.universe("shares", C.config()["min_turnover_rub"], a.limit)]


def cmd_universe(a):
    cfg = C.config()
    risk_rub = C.money_limit(cfg["max_loss_per_trade"], cfg["account_size"])
    rows, all_r = [], []
    for sym in symbols(a):
        a.symbol = sym
        try:
            daily, monthly, spec, regime = load(sym, a)
        except (C.NetError, FileNotFoundError) as e:
            print("%s: %s" % (sym, e))
            continue
        tr = run(daily, monthly, spec, regime, cfg, params(a))
        st = sim.stats([t["r"] for t in tr])
        all_r += [t["r"] for t in tr]
        rows.append([sym, st.get("trades", 0), C.pct(st.get("winrate"), 0) if st.get("trades") else "—",
                     r(st["avg_r"]) if st.get("trades") else "—", r(st["total_r"]) if st.get("trades") else "—",
                     C.rub(st.get("total_r", 0) * risk_rub, 0), r(st["max_dd_r"]) if st.get("trades") else "—"])
        record(sym, params(a), st)
    print("Тренд сверху вниз по бумагам (одни и те же параметры для всех — без подгонки):")
    print(C.table(rows, ["тикер", "сделок", "прибыльных", "среднее", "итого", "≈ ₽", "просадка"]))
    tot = sim.stats(all_r)
    print("\nВсе бумаги вместе: " + fmt(tot, risk_rub))


def add_args(p, sym=True):
    if sym:
        p.add_argument("symbol")
    p.add_argument("--files-dir", help="папка с SYM_D.csv (дневки) и SYM_M.csv (месяцы) из ISS")
    p.add_argument("--days", type=int, default=800)
    p.add_argument("--now"); p.add_argument("--kind")
    p.add_argument("--stop-atr", type=float, default=2.0); p.add_argument("--trail-atr", type=float, default=3.0)
    p.add_argument("--short", action="store_true", help="разрешить шорты по нисходящему тренду")
    p.add_argument("--hold", type=int, default=60, help="максимум дней в позиции")
    p.add_argument("--setups", default="trend-pullback,trend-breakout")
    p.add_argument("--no-month", action="store_true", help="не использовать месячный фильтр")
    p.add_argument("--regime", action="store_true", help="лонг только когда IMOEX выше EMA50")
    p.add_argument("--min-trades", type=int, default=8)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--trades", action="store_true")
