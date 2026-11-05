"""Движок симуляции: прогон сделки по барам, бэктест стратегий с издержками, статистика."""
import math
import random

import common as C
import setups as S
import ta


def run_trade(bars, entry, stop, target, long_=True, flat_by=None, max_bars=None):
    """Прогон открытой позиции по барам после входа. Если в одном баре задеты и стоп, и цель — считаем стоп (консервативно).
    Гэп через стоп исполняется по цене открытия. -> (исход, цена выхода, баров удержано)."""
    for i, b in enumerate(bars):
        if flat_by is not None and C.minutes_of(C.parse_dt(b["t"])) >= flat_by:
            return "время", b["o"], i
        if long_:
            if b["o"] <= stop:
                return "стоп", b["o"], i + 1
            if b["o"] >= target:
                return "цель", b["o"], i + 1
            if b["l"] <= stop:
                return "стоп", stop, i + 1
            if b["h"] >= target:
                return "цель", target, i + 1
        else:
            if b["o"] >= stop:
                return "стоп", b["o"], i + 1
            if b["o"] <= target:
                return "цель", b["o"], i + 1
            if b["h"] >= stop:
                return "стоп", stop, i + 1
            if b["l"] <= target:
                return "цель", target, i + 1
        if max_bars and i + 1 >= max_bars:
            return "время", b["c"], i + 1
    if bars:
        return "время", bars[-1]["c"], len(bars)
    return "время", entry, 0


def r_multiple(entry, exit_, risk, long_, cost_r=0.0):
    gross = ((exit_ - entry) if long_ else (entry - exit_)) / risk
    return gross - cost_r


def stats(rs):
    n = len(rs)
    if not n:
        return {"trades": 0}
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    eq, peak, dd = 0.0, 0.0, 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    gl = -sum(losses)
    mean = sum(rs) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in rs) / (n - 1)) if n > 1 else 0
    return {"trades": n, "winrate": len(wins) / n * 100, "avg_r": mean,
            "avg_win": sum(wins) / len(wins) if wins else 0, "avg_loss": sum(losses) / len(losses) if losses else 0,
            "profit_factor": (sum(wins) / gl) if gl > 0 else None, "total_r": sum(rs), "max_dd_r": dd,
            "t_stat": (mean / (sd / math.sqrt(n))) if sd > 0 else None}


# ---------------------------------------------------------------- стратегии

def _daily_trend_ok(daily, date, ema_n=50):
    """Тренд старшего ТФ: закрытие предыдущего дня выше EMA50 (только данные до даты входа)."""
    past = [b for b in daily if b["t"][:10] < date]
    if len(past) < ema_n + 1:
        return True
    e = ta.ema([b["c"] for b in past], ema_n)
    return past[-1]["c"] > e[-1]


def backtest_orb(bars, spec, cfg, tf_min=5, start="09:00", range_min=15, stop_rule="or-mid", target_r=1.5,
                 flat_by="18:30", last_entry="12:00", daily=None, trend_filter=False, regime=None,
                 volume_filter=0.0, allow_short=False):
    import costs
    days = {}
    for b in bars:
        days.setdefault(b["t"][:10], []).append(b)
    trades = []
    for d in sorted(days):
        day = days[d]
        r = S.orb(day, tf_min, start, range_min, allow_short=allow_short)
        if r.get("status") != "сработал":
            continue
        if C.minutes_of(C.parse_dt(r["trigger_time"])) >= C.hm(last_entry):
            continue
        long_ = r["direction"] == "long"
        if trend_filter and daily is not None:
            up = _daily_trend_ok(daily, d)
            if long_ != up:
                continue
        if regime is not None and long_ and not regime.get(d, True):
            continue
        idx = next(i for i, b in enumerate(day) if b["t"] == r["trigger_time"])
        if volume_filter:
            prev = [x["v"] for x in day[max(0, idx - 20):idx]] or [1]
            if day[idx]["v"] < volume_filter * (sum(prev) / len(prev)):
                continue
        entry = r["trigger_price"]
        if stop_rule == "or-mid":
            stop = r["range_mid"]
        elif stop_rule == "or-low":
            stop = r["range_low"] if long_ else r["range_high"]
        else:  # atr:K по барам до входа
            k = float(stop_rule.split(":")[1]) if ":" in stop_rule else 1.0
            hist = [x for x in bars if x["t"] <= r["trigger_time"]][-200:]
            a = ta.atr([x["h"] for x in hist], [x["l"] for x in hist], [x["c"] for x in hist])[-1] or 0
            stop = entry - k * a if long_ else entry + k * a
        risk = (entry - stop) if long_ else (stop - entry)
        if risk <= 0:
            continue
        target = entry + target_r * risk if long_ else entry - target_r * risk
        out, px, held = run_trade(day[idx + 1:], entry, stop, target, long_, flat_by=C.hm(flat_by))
        cost_r = costs.in_r(spec, entry, risk, cfg)
        trades.append({"date": d, "time": r["trigger_time"][11:], "dir": r["direction"], "entry": entry, "stop": stop,
                       "target": target, "exit": px, "outcome": out, "cost_r": round(cost_r, 3),
                       "r": round(r_multiple(entry, px, risk, long_, cost_r), 3)})
    return trades


_SIG_CACHE = {}


def swing_signals(daily, setup, warmup=210, window=330):
    """Индексы дней, на закрытии которых сработал сетап (считается один раз на набор данных)."""
    key = (id(daily), len(daily), setup)
    if key in _SIG_CACHE:
        return _SIG_CACHE[key]
    fn = S.DETECTORS[setup]
    out = [i for i in range(warmup, len(daily) - 1) if fn(daily[max(0, i - window):i + 1])]
    _SIG_CACHE[key] = out
    return out


def backtest_swing(daily, spec, cfg, setup="breakout", stop_atr=2.0, target_r=2.0, max_hold=10, regime=None,
                   warmup=210):
    import costs
    sigs = swing_signals(daily, setup, warmup)
    a_all = ta.atr([b["h"] for b in daily], [b["l"] for b in daily], [b["c"] for b in daily])
    trades, busy_until = [], -1
    for i in sigs:
        if i <= busy_until:
            continue  # одна позиция за раз
        if regime is not None and not regime.get(daily[i + 1]["t"][:10], True):
            continue
        a = a_all[i]
        if not a:
            continue
        entry = daily[i + 1]["o"]
        stop = entry - stop_atr * a
        risk = entry - stop
        target = entry + target_r * risk
        fwd = daily[i + 1:]
        first = dict(fwd[0])
        first["o"] = entry
        out, px, held = run_trade([first] + fwd[1:], entry, stop, target, True, max_bars=max_hold)
        cost_r = costs.in_r(spec, entry, risk, cfg)
        trades.append({"date": daily[i + 1]["t"][:10], "dir": "long", "entry": entry, "stop": stop, "target": target,
                       "exit": px, "outcome": out, "held": held, "cost_r": round(cost_r, 3),
                       "r": round(r_multiple(entry, px, risk, True, cost_r), 3)})
        busy_until = i + max(1, held)
    return trades


def regime_map(index_daily, ema_n=50):
    """{дата: True если вчерашнее закрытие индекса выше EMA50} — фильтр режима рынка."""
    c = [b["c"] for b in index_daily]
    e = ta.ema(c, ema_n)
    out = {}
    for i in range(1, len(index_daily)):
        out[index_daily[i]["t"][:10]] = e[i - 1] is None or c[i - 1] > e[i - 1]
    return out


# ---------------------------------------------------------------- машина времени (бутстрэп)

def bootstrap_intraday(hist_days, entry_minute, flat_minute, entry, stop, target, long_, n=10000, seed=19):
    """Каждый будущий бар берётся из того же времени суток случайного прошлого дня (отношения O/пред.C, H/O, L/O, C/O)."""
    slots = {}
    for day in hist_days:
        prev_c = None
        for b in day:
            m = C.minutes_of(C.parse_dt(b["t"]))
            if prev_c and m > entry_minute and m < flat_minute and b["o"] > 0:
                slots.setdefault(m, []).append((b["o"] / prev_c, b["h"] / b["o"], b["l"] / b["o"], b["c"] / b["o"]))
            prev_c = b["c"]
    order = sorted(slots)
    rng = random.Random(seed)
    res = {"цель": 0, "стоп": 0, "время": 0}
    rs = []
    risk = abs(entry - stop)
    for _ in range(n):
        p = entry
        outcome = None
        for m in order:
            go, gh, gl, gc = rng.choice(slots[m])
            o = p * go
            h, l, c = o * gh, o * gl, o * gc
            if long_:
                if o <= stop or l <= stop:
                    outcome, px = "стоп", min(o, stop) if o <= stop else stop
                elif o >= target or h >= target:
                    outcome, px = "цель", max(o, target) if o >= target else target
            else:
                if o >= stop or h >= stop:
                    outcome, px = "стоп", max(o, stop) if o >= stop else stop
                elif o <= target or l <= target:
                    outcome, px = "цель", min(o, target) if o <= target else target
            if outcome:
                break
            p = c
        if not outcome:
            outcome, px = "время", p
        res[outcome] += 1
        rs.append(((px - entry) if long_ else (entry - px)) / risk)
    return res, rs


def bootstrap_daily(daily, days_fwd, entry, stop, target, long_=True, n=10000, seed=19, lookback=500):
    w = daily[-lookback:]
    rel = [(w[i]["o"] / w[i - 1]["c"], w[i]["h"] / w[i]["o"], w[i]["l"] / w[i]["o"], w[i]["c"] / w[i]["o"])
           for i in range(1, len(w)) if w[i]["o"] > 0]
    rng = random.Random(seed)
    res = {"цель": 0, "стоп": 0, "время": 0}
    rs = []
    risk = abs(entry - stop)
    for _ in range(n):
        p, outcome = entry, None
        for d in range(days_fwd):
            go, gh, gl, gc = rng.choice(rel)
            o = p * go if d > 0 else p
            h, l, c = o * gh, o * gl, o * gc
            if long_:
                if l <= stop:
                    outcome, px = "стоп", min(o, stop)
                elif h >= target:
                    outcome, px = "цель", max(o, target) if o >= target else target
            else:
                if h >= stop:
                    outcome, px = "стоп", max(o, stop)
                elif l <= target:
                    outcome, px = "цель", min(o, target) if o <= target else target
            if outcome:
                break
            p = c
        if not outcome:
            outcome, px = "время", p
        res[outcome] += 1
        rs.append(((px - entry) if long_ else (entry - px)) / risk)
    return res, rs
