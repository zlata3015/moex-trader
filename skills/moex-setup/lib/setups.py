"""Торговые сетапы: пробой диапазона открытия (ORB), пробой уровня, откат к EMA, отскок от перепроданности,
выход из сжатия Боллинджера, золотой крест. Используются сканером, таймером входа и машиной времени."""
from datetime import timedelta

import common as C
import ta

SETUPS = {
    "orb": "пробой диапазона открытия",
    "breakout": "пробой максимума N баров / уровня",
    "pullback": "откат к EMA20 в восходящем тренде",
    "rsi-bounce": "отскок от перепроданности (RSI<30 + разворотная свеча)",
    "squeeze": "выход из сжатия полос Боллинджера",
    "golden-cross": "EMA50 пересекла EMA200 вверх",
    "vwap-reclaim": "возврат цены выше VWAP после пролива",
}


def bar_end(b, tf_min):
    return C.parse_dt(b["t"]) + timedelta(minutes=tf_min)


def orb(day_bars, tf_min=5, start="09:00", range_min=15, at=None, allow_short=False):
    """Диапазон открытия и первый бар, закрывшийся за ним. day_bars — бары одного дня по возрастанию."""
    s = C.hm(start)
    rng = [b for b in day_bars if s <= C.minutes_of(C.parse_dt(b["t"])) < s + range_min]
    res = {"setup": "orb", "start": start, "range_min": range_min}
    if not rng:
        res["status"] = "нет данных диапазона"
        return res
    hi, lo = max(b["h"] for b in rng), min(b["l"] for b in rng)
    res.update({"range_high": hi, "range_low": lo, "range_mid": (hi + lo) / 2})
    end_range = C.parse_dt(rng[0]["t"][:10] + " " + start) + timedelta(minutes=range_min)
    if at and at < end_range:
        res["status"] = "диапазон формируется"
        return res
    for b in day_bars:
        t0 = C.parse_dt(b["t"])
        if t0 < end_range:
            continue
        if at and bar_end(b, tf_min) > at:
            break
        if b["c"] > hi:
            res.update({"status": "сработал", "direction": "long", "trigger_time": b["t"], "trigger_price": b["c"],
                        "over_pct": (b["c"] - hi) / hi * 100})
            return res
        if allow_short and b["c"] < lo:
            res.update({"status": "сработал", "direction": "short", "trigger_time": b["t"], "trigger_price": b["c"],
                        "over_pct": (lo - b["c"]) / lo * 100})
            return res
    res["status"] = "ждём"
    return res


def breakout(bars, lookback=20, level=None):
    if len(bars) < lookback + 2:
        return None
    lvl = level if level is not None else max(b["h"] for b in bars[-lookback - 1:-1])
    last = bars[-1]
    if last["c"] > lvl and bars[-2]["c"] <= lvl:
        return {"setup": "breakout", "direction": "long", "level": lvl, "trigger_price": last["c"], "trigger_time": last["t"]}
    return None


def pullback(bars):
    c = [b["c"] for b in bars]
    if len(c) < 60:
        return None
    e20, e50 = ta.ema(c, 20), ta.ema(c, 50)
    a = ta.atr([b["h"] for b in bars], [b["l"] for b in bars], c)
    r = ta.rsi(c)
    if e20[-1] > e50[-1] and e50[-1] > e50[-10] and bars[-1]["l"] <= e20[-1] + 0.3 * a[-1] and c[-1] > e20[-1] \
            and 38 <= (r[-1] or 50) <= 58 and c[-1] > bars[-1]["o"]:
        return {"setup": "pullback", "direction": "long", "level": e20[-1], "trigger_price": c[-1], "trigger_time": bars[-1]["t"]}
    return None


def rsi_bounce(bars):
    c = [b["c"] for b in bars]
    r = ta.rsi(c)
    if len(c) < 20 or r[-2] is None:
        return None
    pats = ta.patterns(bars)
    bull = [p for p, s in pats if s > 0]
    if min(r[-3:-1]) < 30 and r[-1] > r[-2] and bull:
        return {"setup": "rsi-bounce", "direction": "long", "trigger_price": c[-1], "trigger_time": bars[-1]["t"],
                "pattern": bull[0], "rsi": r[-1]}
    return None


def squeeze(bars):
    c = [b["c"] for b in bars]
    if len(c) < 130:
        return None
    _, up, lo, w = ta.bollinger(c)
    hist = [x for x in w[-120:-1] if x is not None]
    if not hist:
        return None
    thr = sorted(hist)[len(hist) // 5]
    if min(w[-6:-1]) <= thr and c[-1] > up[-1]:
        return {"setup": "squeeze", "direction": "long", "trigger_price": c[-1], "trigger_time": bars[-1]["t"], "level": up[-1]}
    return None


def golden_cross(bars):
    c = [b["c"] for b in bars]
    if len(c) < 205:
        return None
    e50, e200 = ta.ema(c, 50), ta.ema(c, 200)
    for back in range(3):
        i = len(c) - 1 - back
        if e50[i - 1] <= e200[i - 1] and e50[i] > e200[i]:
            return {"setup": "golden-cross", "direction": "long", "trigger_price": c[-1], "trigger_time": bars[-1]["t"]}
    return None


def vwap_reclaim(bars):
    if len(bars) < 10:
        return None
    vw = ta.vwap(bars)
    today = bars[-1]["t"][:10]
    day = [i for i, b in enumerate(bars) if b["t"][:10] == today]
    if len(day) < 6:
        return None
    below = any(bars[i]["c"] < vw[i] for i in day[:-1])
    if below and bars[-1]["c"] > vw[-1] and bars[-2]["c"] <= vw[-2]:
        return {"setup": "vwap-reclaim", "direction": "long", "trigger_price": bars[-1]["c"], "trigger_time": bars[-1]["t"], "level": vw[-1]}
    return None


DETECTORS = {"breakout": breakout, "pullback": pullback, "rsi-bounce": rsi_bounce, "squeeze": squeeze,
             "golden-cross": golden_cross, "vwap-reclaim": vwap_reclaim}


def detect(bars, which=("breakout", "pullback", "rsi-bounce", "squeeze", "golden-cross")):
    out = []
    for name in which:
        r = DETECTORS[name](bars)
        if r:
            out.append(r)
    return out
