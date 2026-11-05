"""Торговля по тренду сверху вниз: месяц → неделя → день задают направление, вход по сигналу в тренде,
выход по подтягиваемому стопу (позицию держим, пока тренд жив).

Все расчёты причинные: на дату i используются только данные до i включительно, а старшие
таймфреймы — только по уже закрытым неделям и месяцам.
"""
from datetime import datetime

import common as C
import ta

# параметры средних для каждого таймфрейма: (быстрая, медленная)
EMA = {"1M": (6, 12), "1w": (10, 20), "1d": (20, 50), "4h": (20, 50), "1h": (20, 50), "10m": (20, 50), "5m": (20, 50)}
DIR_RU = {"up": "вверх", "down": "вниз", "flat": "неясно"}


# ---------------------------------------------------------------- пересборка

def period_key(t, period):
    d = datetime.strptime(t[:10], "%Y-%m-%d")
    if period == "W":
        y, w, _ = d.isocalendar()
        return "%04d-W%02d" % (y, w)
    return t[:7]


def resample_period(daily, period):
    """Дневки -> недели ('W') или месяцы ('M'). t бара = дата первого дня периода."""
    out, key = [], None
    for b in daily:
        k = period_key(b["t"], period)
        if k != key:
            out.append(dict(b, key=k))
            key = k
        else:
            cur = out[-1]
            cur["h"] = max(cur["h"], b["h"])
            cur["l"] = min(cur["l"], b["l"])
            cur["c"] = b["c"]
            cur["v"] = cur.get("v", 0) + b.get("v", 0)
    return out


def to_daily(intraday):
    """Внутридневные бары -> дневки (все сессии дня, включая утреннюю и вечернюю)."""
    out, key = [], None
    for b in intraday:
        k = b["t"][:10]
        if k != key:
            out.append({"t": k + " 00:00", "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b.get("v", 0)})
            key = k
        else:
            cur = out[-1]
            cur["h"] = max(cur["h"], b["h"])
            cur["l"] = min(cur["l"], b["l"])
            cur["c"] = b["c"]
            cur["v"] += b.get("v", 0)
    return out


# ---------------------------------------------------------------- направление тренда

def trend_state(bars, tf="1d"):
    """Направление по средним и структуре свингов. -> {dir, score, why[], ema_fast, ema_slow}."""
    fast, slow = EMA.get(tf, (20, 50))
    c = [b["c"] for b in bars]
    if len(c) < fast + 3:
        return {"dir": "flat", "score": 0, "why": ["мало данных (%d баров)" % len(c)], "tf": tf}
    ef = ta.ema(c, fast)
    es = ta.ema(c, slow) if len(c) >= slow + 1 else [None] * len(c)
    slope = ef[-1] - ef[-4]
    score, why = 0, []
    if c[-1] > ef[-1]:
        score += 1
        why.append("цена выше EMA%d" % fast)
    else:
        score -= 1
        why.append("цена ниже EMA%d" % fast)
    if slope > 0:
        score += 1
        why.append("EMA%d растёт" % fast)
    elif slope < 0:
        score -= 1
        why.append("EMA%d падает" % fast)
    if es[-1] is not None:
        if ef[-1] > es[-1]:
            score += 1
            why.append("EMA%d выше EMA%d" % (fast, slow))
        else:
            score -= 1
            why.append("EMA%d ниже EMA%d" % (fast, slow))
    # структура: последние два свинга
    k = 2 if len(bars) < 60 else 3
    sw = ta.swings([b["h"] for b in bars], [b["l"] for b in bars], k)
    hs = [p for _, p, kd in sw if kd == "H"][-2:]
    ls = [p for _, p, kd in sw if kd == "L"][-2:]
    if len(hs) == 2 and len(ls) == 2:
        if hs[1] > hs[0] and ls[1] > ls[0]:
            score += 1
            why.append("максимумы и минимумы растут")
        elif hs[1] < hs[0] and ls[1] < ls[0]:
            score -= 1
            why.append("максимумы и минимумы снижаются")
    need = 3 if es[-1] is not None else 2
    d = "up" if score >= need else "down" if score <= -need else "flat"
    return {"dir": d, "score": score, "why": why, "tf": tf, "ema_fast": ef[-1], "ema_slow": es[-1], "close": c[-1]}


def combine(states, require=("1w", "1d"), veto=("1M",)):
    """Сделка по тренду, только если обязательные ТФ смотрят в одну сторону, а вето-ТФ не против."""
    dirs = [states[t]["dir"] for t in require if t in states]
    if not dirs or any(d != dirs[0] for d in dirs) or dirs[0] == "flat":
        return {"direction": None, "why": "старшие графики не согласны: " +
                ", ".join("%s %s" % (t, DIR_RU[states[t]["dir"]]) for t in require if t in states)}
    d = dirs[0]
    for t in veto:
        if t in states and states[t]["dir"] not in ("flat", d):
            return {"direction": None, "why": "месячный тренд против (%s), сделку не открываем" % DIR_RU[states[t]["dir"]]}
    return {"direction": "long" if d == "up" else "short",
            "why": "тренд %s на %s" % (DIR_RU[d], ", ".join(t for t in list(require) + list(veto) if t in states and states[t]["dir"] == d))}


def top_down(daily, monthly=None, upto=None):
    """Состояние трендов на дату upto (по умолчанию — последняя дневка). Недели и месяцы — только закрытые."""
    if upto is not None:
        daily = [b for b in daily if b["t"][:10] <= upto]
    if not daily:
        return {}
    cur_w = period_key(daily[-1]["t"], "W")
    cur_m = daily[-1]["t"][:7]
    weeks = [w for w in resample_period(daily, "W") if w["key"] < cur_w]
    if monthly:
        months = [m for m in monthly if m["t"][:7] < cur_m]
    else:
        months = [m for m in resample_period(daily, "M") if m["key"] < cur_m]
    st = {"1M": trend_state(months, "1M"), "1w": trend_state(weeks, "1w"), "1d": trend_state(daily, "1d")}
    st["verdict"] = combine(st)
    return st


# ---------------------------------------------------------------- сигналы входа в тренде

def entry_signal(bars, direction, i=None, lookback=20):
    """Сигнал на закрытии бара i в сторону тренда: откат к EMA20 или пробой N-барного экстремума."""
    if i is None:
        i = len(bars) - 1
    if i < 50 or direction is None:
        return None
    c = [b["c"] for b in bars[:i + 1]]
    e20 = ta.ema(c, 20)[-1]
    a = ta.atr([b["h"] for b in bars[:i + 1]], [b["l"] for b in bars[:i + 1]], c)[-1]
    b = bars[i]
    if direction == "long":
        if b["l"] <= e20 + 0.25 * a and b["c"] > e20 and b["c"] > b["o"]:
            return {"setup": "trend-pullback", "direction": "long", "price": b["c"], "atr": a, "level": e20, "t": b["t"]}
        hh = max(x["h"] for x in bars[i - lookback:i])
        if b["c"] > hh:
            return {"setup": "trend-breakout", "direction": "long", "price": b["c"], "atr": a, "level": hh, "t": b["t"]}
    else:
        if b["h"] >= e20 - 0.25 * a and b["c"] < e20 and b["c"] < b["o"]:
            return {"setup": "trend-pullback", "direction": "short", "price": b["c"], "atr": a, "level": e20, "t": b["t"]}
        ll = min(x["l"] for x in bars[i - lookback:i])
        if b["c"] < ll:
            return {"setup": "trend-breakout", "direction": "short", "price": b["c"], "atr": a, "level": ll, "t": b["t"]}
    return None


# ---------------------------------------------------------------- подтягиваемый стоп

def chandelier(bars, entry_i, entry_px, long_, k=3.0, atr_list=None):
    """Текущий подтягиваемый стоп для открытой позиции: экстремум с момента входа минус k ATR (только в сторону прибыли)."""
    if atr_list is None:
        atr_list = ta.atr([b["h"] for b in bars], [b["l"] for b in bars], [b["c"] for b in bars])
    a = atr_list[-1] or 0
    seg = bars[entry_i:]
    if long_:
        return max(b["h"] for b in seg) - k * a
    return min(b["l"] for b in seg) + k * a


def run_trailing(bars, start, entry, stop, long_, k=3.0, atr_list=None, flat_by=None, max_bars=None,
                 activate_r=0.0):
    """Ведём позицию с бара start. Стоп подтягивается на закрытии каждого бара и действует со следующего.
    Гэп через стоп исполняется по открытию. -> (исход, цена, баров, MFE в R)."""
    if atr_list is None:
        atr_list = ta.atr([b["h"] for b in bars], [b["l"] for b in bars], [b["c"] for b in bars])
    risk = abs(entry - stop)
    ext = entry
    trailing = False
    mfe = 0.0
    for j in range(start, len(bars)):
        b = bars[j]
        if flat_by is not None and C.minutes_of(C.parse_dt(b["t"])) >= flat_by:
            return "время", b["o"], j - start, mfe
        if long_:
            if b["o"] <= stop:
                return ("трейлинг" if trailing else "стоп"), b["o"], j - start + 1, mfe
            if b["l"] <= stop:
                return ("трейлинг" if trailing else "стоп"), stop, j - start + 1, mfe
            ext = max(ext, b["h"])
            mfe = max(mfe, (ext - entry) / risk)
            a = atr_list[j] or 0
            if mfe >= activate_r:
                new = ext - k * a
                if new > stop:
                    stop, trailing = new, True
        else:
            if b["o"] >= stop:
                return ("трейлинг" if trailing else "стоп"), b["o"], j - start + 1, mfe
            if b["h"] >= stop:
                return ("трейлинг" if trailing else "стоп"), stop, j - start + 1, mfe
            ext = min(ext, b["l"])
            mfe = max(mfe, (entry - ext) / risk)
            a = atr_list[j] or 0
            if mfe >= activate_r:
                new = ext + k * a
                if new < stop:
                    stop, trailing = new, True
        if max_bars and j - start + 1 >= max_bars:
            return "время", b["c"], j - start + 1, mfe
    last = bars[-1]["c"] if bars else entry
    return "открыта", last, len(bars) - start, mfe


# ---------------------------------------------------------------- бэктест стратегии тренда (дневки)

def backtest(daily, spec, cfg, monthly=None, stop_atr=2.0, trail_atr=3.0, allow_short=False, max_hold=60,
             setups=("trend-pullback", "trend-breakout"), regime=None, require=("1w", "1d"), use_month=True,
             warmup=60, start_date=None):
    """Позиционная торговля по тренду на дневках: решение на закрытии дня, вход по открытию следующего."""
    import costs
    import sim
    c = [b["c"] for b in daily]
    atr_list = ta.atr([b["h"] for b in daily], [b["l"] for b in daily], c)
    weeks = resample_period(daily, "W")
    months = monthly or resample_period(daily, "M")
    wk_state, mo_state = {}, {}
    for j in range(len(weeks)):
        wk_state[weeks[j]["key"]] = trend_state(weeks[:j + 1], "1w")["dir"]
    for j in range(len(months)):
        mo_state[months[j]["t"][:7]] = trend_state(months[:j + 1], "1M")["dir"]
    wkeys = [w["key"] for w in weeks]
    mkeys = [m["t"][:7] for m in months]
    trades, i = [], warmup
    while i < len(daily) - 1:
        t = daily[i]["t"]
        if start_date and t[:10] < start_date:
            i += 1
            continue
        cw, cm = period_key(t, "W"), t[:7]
        pw = [k for k in wkeys if k < cw]
        pm = [k for k in mkeys if k < cm]
        st = {"1w": {"dir": wk_state[pw[-1]] if pw else "flat"},
              "1d": {"dir": trend_state(daily[max(0, i - 120):i + 1], "1d")["dir"]}}
        if use_month:
            st["1M"] = {"dir": mo_state[pm[-1]] if pm else "flat"}
        v = combine(st, require=require, veto=("1M",) if use_month else ())
        d = v["direction"]
        if d == "short" and not allow_short:
            d = None
        if d == "long" and regime is not None and not regime.get(daily[i + 1]["t"][:10], True):
            d = None
        sig = entry_signal(daily, d, i) if d else None
        if not sig or sig["setup"] not in setups:
            i += 1
            continue
        long_ = d == "long"
        entry = daily[i + 1]["o"]
        a = atr_list[i]
        stop = entry - stop_atr * a if long_ else entry + stop_atr * a
        risk = abs(entry - stop)
        bars2 = list(daily)
        out, px, held, mfe = run_trailing(bars2, i + 1, entry, stop, long_, trail_atr, atr_list, max_bars=max_hold)
        cost_r = costs.in_r(spec, entry, risk, cfg)
        trades.append({"date": daily[i + 1]["t"][:10], "dir": d, "setup": sig["setup"], "entry": entry, "stop": stop,
                       "exit": px, "outcome": out, "held": held, "mfe_r": round(mfe, 2), "cost_r": round(cost_r, 3),
                       "r": round(sim.r_multiple(entry, px, risk, long_, cost_r), 3),
                       "exit_date": daily[min(len(daily) - 1, i + held)]["t"][:10]})
        i += max(1, held)
    return trades
