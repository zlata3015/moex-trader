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


def load_divs(sym, a):
    if a.files_dir:
        p = os.path.join(a.files_dir, "%s_DIV.json" % sym)
        if os.path.exists(p):
            return C.load_json(p, [])
    try:
        return moex.dividends(sym)
    except C.NetError:
        return []


def apply_dividends(trades, daily, divs, sym=None):
    """Дивиденды в результате сделок: лонг через дату отсечки получает дивиденд, шорт — платит.
    Последний день с дивидендом — торговый день перед датой закрытия реестра (до 31.07.2023 — за два дня, режим T+2)."""
    if not divs:
        return trades
    dates = [b["t"][:10] for b in daily]
    exd = []
    for d in divs:
        rd = d.get("date")
        if not rd or not d.get("value"):
            continue
        if d.get("last_day"):
            exd.append((d["last_day"], float(d["value"])))
            continue
        before = [x for x in dates if x < rd]
        back = 1 if rd >= "2023-07-31" else 2
        if len(before) >= back:
            exd.append((before[-back], float(d["value"])))   # держим на закрытии этого дня -> получаем дивиденд
    for t in trades:
        got = sum(v for x, v in exd if t["date"] <= x < t["exit_date"] or (t["outcome"] == "открыта" and t["date"] <= x))
        if got:
            risk = abs(t["entry"] - t["stop"])
            adj = got / risk * (1 if t["dir"] == "long" else -1)
            t["dividend"] = round(got, 4)
            t["r"] = round(t["r"] + adj, 3)
    return trades


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
    daily, divs, ev = moex.adjust_splits(daily, load_divs(sym, a))
    if monthly:
        monthly, _, _ = moex.adjust_splits(monthly)
    if getattr(a, "since", None):
        pass
    spec["divs"] = divs
    spec["splits"] = ev
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


def run(daily, monthly, spec, regime, cfg, p, start_date=None):
    tr = trend.backtest(daily, spec, cfg, monthly=monthly, stop_atr=p["stop_atr"], trail_atr=p["trail_atr"],
                        allow_short=p["allow_short"], max_hold=p["max_hold"], setups=p["setups"], regime=regime,
                        use_month=p["use_month"], start_date=start_date)
    return apply_dividends(tr, daily, spec.get("divs"))


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
    tr = run(daily, monthly, spec, regime, cfg, p, a.since)
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
    rows, all_r, all_t = [], [], []
    for sym in symbols(a):
        a.symbol = sym
        try:
            daily, monthly, spec, regime = load(sym, a)
        except (C.NetError, FileNotFoundError) as e:
            print("%s: %s" % (sym, e))
            continue
        tr = run(daily, monthly, spec, regime, cfg, params(a), a.since)
        st = sim.stats([t["r"] for t in tr])
        all_r += [t["r"] for t in tr]
        all_t.extend(dict(t, sym=sym) for t in tr)
        rows.append([sym, st.get("trades", 0), C.pct(st.get("winrate"), 0) if st.get("trades") else "—",
                     r(st["avg_r"]) if st.get("trades") else "—", r(st["total_r"]) if st.get("trades") else "—",
                     C.rub(st.get("total_r", 0) * risk_rub, 0), r(st["max_dd_r"]) if st.get("trades") else "—"])
        record(sym, params(a), st)
    print("Тренд сверху вниз по бумагам (одни и те же параметры для всех — без подгонки):")
    print(C.table(rows, ["тикер", "сделок", "прибыльных", "среднее", "итого", "≈ ₽", "просадка"]))
    tot = sim.stats(all_r)
    print("\nВсе бумаги вместе: " + fmt(tot, risk_rub))
    if a.by_year and all_t:
        years = sorted({t["date"][:4] for t in all_t})
        yr = []
        for y in years:
            ys = sim.stats([t["r"] for t in all_t if t["date"].startswith(y)])
            yr.append([y, ys["trades"], C.pct(ys["winrate"], 0), r(ys["avg_r"]), r(ys["total_r"]), C.rub(ys["total_r"] * risk_rub, 0)])
        print("\nПо годам (все бумаги):")
        print(C.table(yr, ["год", "сделок", "прибыльных", "среднее", "итого", "≈ ₽"]))
    C.save_json(C.tpath("backtest", "trend_trades.json"), all_t)


def key_rate_history(start="2018-12-01"):
    import re
    body = ('<?xml version="1.0" encoding="utf-8"?><soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
            '<KeyRate xmlns="http://web.cbr.ru/"><fromDate>%s</fromDate><ToDate>%s</ToDate></KeyRate></soap:Body></soap:Envelope>'
            % (start, C.now_msk().strftime("%Y-%m-%d"))).encode()
    raw = C.http("https://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx", data=body,
                 headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": "http://web.cbr.ru/KeyRate"}).decode("utf-8", "replace")
    return sorted((d[:10], float(r.replace(",", "."))) for d, r in re.findall(r"<DT>([^<]+)</DT>\s*<Rate>([\d.,]+)</Rate>", raw))


def cmd_portfolio(a):
    """Один счёт: все сделки стратегии по бумагам, риск X% капитала, лимиты позиций; свободные деньги в фонде
    денежного рынка (ключевая ставка минус 1%). Сравнение с фондом и индексом."""
    from datetime import datetime
    cfg = C.config()
    tr = []
    for sym in symbols(a):
        try:
            daily, monthly, spec, regime = load(sym, a)
        except (C.NetError, FileNotFoundError):
            continue
        for t in run(daily, monthly, spec, regime, cfg, params(a), a.since):
            tr.append(dict(t, sym=sym, risk_pct=abs(t["entry"] - t["stop"]) / t["entry"]))
    tr.sort(key=lambda t: t["date"])
    try:
        kr = key_rate_history()
    except C.NetError:
        kr = [("2000-01-01", float(cfg.get("key_rate") or 0))]
    rate_on = lambda d: next((r for x, r in reversed(kr) if x <= d), kr[0][1])
    if a.files_dir and os.path.exists(os.path.join(a.files_dir, "IMOEX_D.csv")):
        idx_bars = moex.load_file(os.path.join(a.files_dir, "IMOEX_D.csv"))
    else:
        idx_bars = moex.candles("IMOEX", "1d", days=a.days + 400, kind="index", source="iss")
    idx = {b["t"][:10]: b["c"] for b in idx_bars}
    start = a.since or (tr[0]["date"] if tr else min(idx))
    days = [d for d in sorted(idx) if d >= start]
    cap0 = float(a.capital)
    eq, mm, open_pos, taken, skipped, prev, peak, dd = cap0, cap0, [], [], 0, None, cap0, 0.0
    i = 0
    for d in days:
        if prev:
            dt = (datetime.strptime(d, "%Y-%m-%d") - datetime.strptime(prev, "%Y-%m-%d")).days
            y = max(0.0, rate_on(prev) - 1.0) / 100 * dt / 365
            if not a.no_cash:
                eq += max(0.0, eq - sum(p["capital"] for p in open_pos)) * y
            mm *= 1 + y
        for p in [p for p in open_pos if p["exit_date"] <= d]:
            eq += p["pnl"]
            open_pos.remove(p)
        while i < len(tr) and tr[i]["date"] <= d:
            t = tr[i]
            i += 1
            if len(open_pos) >= cfg["max_open_positions"] or any(p["sym"] == t["sym"] for p in open_pos):
                skipped += 1
                continue
            capital = min(eq * a.risk / 100 / t["risk_pct"], eq * cfg["max_position_pct"] / 100)
            p = dict(t, capital=capital, pnl=t["r"] * capital * t["risk_pct"])
            open_pos.append(p)
            taken.append(p)
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1)
        prev = d
    eq += sum(p["pnl"] for p in open_pos)
    years = max(0.1, (datetime.strptime(days[-1], "%Y-%m-%d") - datetime.strptime(days[0], "%Y-%m-%d")).days / 365.25)
    cagr = lambda x0, x1: ((x1 / x0) ** (1 / years) - 1) * 100
    tp = sum(p["pnl"] for p in taken)
    print("Счёт %s · %s … %s (%.1f лет) · бумаг %d · риск %s%% на сделку, до %d позиций по %d%% счёта" % (
        C.rub(cap0, 0), days[0], days[-1], years, len(set(t["sym"] for t in tr)), C.num(a.risk, 1),
        cfg["max_open_positions"], cfg["max_position_pct"]))
    print("  стратегия%s: %s (%+.1f%% годовых), худшая просадка %s; сделок %d, пропущено из-за лимитов %d" % (
        "" if a.no_cash else " + свободные деньги в фонде денежного рынка", C.rub(eq, 0), cagr(cap0, eq), C.pct(dd * 100, 1),
        len(taken), skipped))
    print("    сделки: %s, проценты фонда: %s" % (C.rub(tp, 0), C.rub(eq - cap0 - tp, 0)))
    print("  только фонд денежного рынка (ставка ЦБ − 1%%): %s (%+.1f%% годовых)" % (C.rub(mm, 0), cagr(cap0, mm)))
    i0, i1 = idx[days[0]], idx[days[-1]]
    print("  индекс IMOEX (без дивидендов): %s (%+.1f%% годовых)" % (C.rub(cap0 * i1 / i0, 0), cagr(i0, i1)))
    by = {}
    for p in taken:
        by[p["exit_date"][:4]] = by.get(p["exit_date"][:4], 0) + p["pnl"]
    print("  сделки по годам: " + ", ".join("%s: %s" % (y, C.rub(v, 0)) for y, v in sorted(by.items())))


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
    p.add_argument("--since", help="считать сделки с даты YYYY-MM-DD (история до неё — только для разгона индикаторов)")
    p.add_argument("--by-year", action="store_true", help="разбивка результата по годам")
    p.add_argument("--capital", type=float, default=100000)
    p.add_argument("--risk", type=float, default=1.0, help="%% капитала риска на сделку (для trend-portfolio)")
    p.add_argument("--no-cash", action="store_true", help="не начислять проценты фонда на свободные деньги")
