"""Технический анализ: индикаторы, уровни, свечные паттерны, дивергенции, тренд, итоговая оценка.

Все функции принимают списки чисел (или баров {t,o,h,l,c,v}) и возвращают списки той же
длины, где первые значения (период разгона) равны None.
"""
import math

# ---------------------------------------------------------------- базовые индикаторы

def sma(x, n):
    out, s = [None] * len(x), 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n:
            s -= x[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(x, n):
    out = [None] * len(x)
    if len(x) < n:
        return out
    k = 2.0 / (n + 1)
    e = sum(x[:n]) / n
    out[n - 1] = e
    for i in range(n, len(x)):
        e = x[i] * k + e * (1 - k)
        out[i] = e
    return out


def _ema_opt(x, n):
    """EMA для ряда с None в начале."""
    start = next((i for i, v in enumerate(x) if v is not None), len(x))
    tail = ema([v for v in x[start:]], n)
    return [None] * start + tail


def rma(x, n):
    """Сглаживание Уайлдера."""
    out = [None] * len(x)
    if len(x) < n:
        return out
    r = sum(x[:n]) / n
    out[n - 1] = r
    for i in range(n, len(x)):
        r = (r * (n - 1) + x[i]) / n
        out[i] = r
    return out


def rsi(c, n=14):
    if len(c) <= n:
        return [None] * len(c)
    up = [0.0] + [max(c[i] - c[i - 1], 0) for i in range(1, len(c))]
    dn = [0.0] + [max(c[i - 1] - c[i], 0) for i in range(1, len(c))]
    au, ad = rma(up[1:], n), rma(dn[1:], n)
    out = [None]
    for a, d in zip(au, ad):
        if a is None:
            out.append(None)
        elif d == 0:
            out.append(100.0 if a > 0 else 50.0)
        else:
            out.append(100 - 100 / (1 + a / d))
    return out


def macd(c, fast=12, slow=26, sig=9):
    ef, es = ema(c, fast), ema(c, slow)
    line = [(a - b) if a is not None and b is not None else None for a, b in zip(ef, es)]
    signal = _ema_opt(line, sig)
    hist = [(a - b) if a is not None and b is not None else None for a, b in zip(line, signal)]
    return line, signal, hist


def bollinger(c, n=20, k=2.0):
    mid = sma(c, n)
    up, lo, width = [None] * len(c), [None] * len(c), [None] * len(c)
    for i in range(n - 1, len(c)):
        w = c[i - n + 1:i + 1]
        m = mid[i]
        sd = math.sqrt(sum((v - m) ** 2 for v in w) / n)
        up[i], lo[i] = m + k * sd, m - k * sd
        width[i] = (up[i] - lo[i]) / m * 100 if m else None
    return mid, up, lo, width


def true_range(h, l, c):
    tr = [h[0] - l[0]]
    for i in range(1, len(c)):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    return tr


def atr(h, l, c, n=14):
    return rma(true_range(h, l, c), n)


def stochastic(h, l, c, n=14, k_smooth=3, d_smooth=3):
    raw = [None] * len(c)
    for i in range(n - 1, len(c)):
        hh, ll = max(h[i - n + 1:i + 1]), min(l[i - n + 1:i + 1])
        raw[i] = 50.0 if hh == ll else (c[i] - ll) / (hh - ll) * 100
    k = _sma_opt(raw, k_smooth)
    d = _sma_opt(k, d_smooth)
    return k, d


def _sma_opt(x, n):
    start = next((i for i, v in enumerate(x) if v is not None), len(x))
    return [None] * start + sma(x[start:], n)


def adx(h, l, c, n=14):
    pdm, mdm = [0.0], [0.0]
    for i in range(1, len(c)):
        u, d = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm.append(u if u > d and u > 0 else 0.0)
        mdm.append(d if d > u and d > 0 else 0.0)
    tr = true_range(h, l, c)
    atr_, sp, sm = rma(tr, n), rma(pdm, n), rma(mdm, n)
    pdi, mdi, dx = [None] * len(c), [None] * len(c), []
    for i in range(len(c)):
        if atr_[i]:
            pdi[i] = 100 * sp[i] / atr_[i]
            mdi[i] = 100 * sm[i] / atr_[i]
            s = pdi[i] + mdi[i]
            dx.append(100 * abs(pdi[i] - mdi[i]) / s if s else 0.0)
        else:
            dx.append(None)
    start = next((i for i, v in enumerate(dx) if v is not None), len(dx))
    a = [None] * start + rma(dx[start:], n)
    return a, pdi, mdi


def vwap(bars):
    """VWAP с обнулением в начале каждого дня."""
    out, day, pv, vv = [], None, 0.0, 0.0
    for b in bars:
        d = b["t"][:10]
        if d != day:
            day, pv, vv = d, 0.0, 0.0
        tp = (b["h"] + b["l"] + b["c"]) / 3
        pv += tp * b["v"]
        vv += b["v"]
        out.append(pv / vv if vv else tp)
    return out


def obv(c, v):
    out = [0.0]
    for i in range(1, len(c)):
        out.append(out[-1] + (v[i] if c[i] > c[i - 1] else -v[i] if c[i] < c[i - 1] else 0))
    return out


def supertrend(h, l, c, n=10, mult=3.0):
    a = atr(h, l, c, n)
    line, direction = [None] * len(c), [None] * len(c)
    fu = fl = None
    d = 1
    for i in range(len(c)):
        if a[i] is None:
            continue
        mid = (h[i] + l[i]) / 2
        bu, bl = mid + mult * a[i], mid - mult * a[i]
        if fu is None:
            fu, fl = bu, bl
        else:
            fu = bu if (bu < fu or c[i - 1] > fu) else fu
            fl = bl if (bl > fl or c[i - 1] < fl) else fl
        if d == 1 and c[i] < fl:
            d = -1
        elif d == -1 and c[i] > fu:
            d = 1
        line[i] = fl if d == 1 else fu
        direction[i] = d
    return line, direction


def donchian(h, l, n=20):
    up, lo = [None] * len(h), [None] * len(h)
    for i in range(n - 1, len(h)):
        up[i], lo[i] = max(h[i - n + 1:i + 1]), min(l[i - n + 1:i + 1])
    return up, lo


# ---------------------------------------------------------------- уровни

def pivots(ph, pl, pc):
    """Классические пивоты по H/L/C предыдущего периода."""
    p = (ph + pl + pc) / 3
    return {"P": p, "R1": 2 * p - pl, "S1": 2 * p - ph, "R2": p + (ph - pl), "S2": p - (ph - pl),
            "R3": ph + 2 * (p - pl), "S3": pl - 2 * (ph - p)}


def swings(h, l, k=3):
    """Фракталы: локальные максимумы/минимумы с k барами по сторонам. -> [(i, price, 'H'|'L')]."""
    out = []
    for i in range(k, len(h) - k):
        if h[i] == max(h[i - k:i + k + 1]) and h[i] > max(h[i - k:i] + h[i + 1:i + k + 1] or [h[i] - 1]):
            out.append((i, h[i], "H"))
        if l[i] == min(l[i - k:i + k + 1]) and l[i] < min(l[i - k:i] + l[i + 1:i + k + 1] or [l[i] + 1]):
            out.append((i, l[i], "L"))
    return out


def levels(bars, k=3, tol=None, max_levels=8):
    """Уровни поддержки/сопротивления: кластеры свинг-точек. Сила = число касаний (+свежесть)."""
    h, l, c = [b["h"] for b in bars], [b["l"] for b in bars], [b["c"] for b in bars]
    if len(bars) < 2 * k + 3:
        return []
    a = atr(h, l, c, 14)
    last_atr = next((v for v in reversed(a) if v), (max(h) - min(l)) / 20 or 1)
    tol = tol or last_atr * 0.6
    pts = swings(h, l, k)
    clusters = []
    for i, p, kind in sorted(pts, key=lambda x: x[1]):
        if clusters and abs(p - clusters[-1]["price"]) <= tol:
            cl = clusters[-1]
            cl["touches"] += 1
            cl["sum"] += p
            cl["price"] = cl["sum"] / cl["touches"]
            cl["last_i"] = max(cl["last_i"], i)
            cl["kinds"].add(kind)
        else:
            clusters.append({"price": p, "sum": p, "touches": 1, "last_i": i, "kinds": {kind}})
    n = len(bars)
    price = c[-1]
    out = []
    for cl in clusters:
        recency = 1 - (n - 1 - cl["last_i"]) / n
        strength = cl["touches"] + recency + (0.5 if len(cl["kinds"]) == 2 else 0)
        out.append({"price": round(cl["price"], 6), "touches": cl["touches"], "strength": round(strength, 2),
                    "type": "поддержка" if cl["price"] < price else "сопротивление",
                    "flip": len(cl["kinds"]) == 2})
    # добавим экстремумы окна
    out.append({"price": max(h), "touches": 1, "strength": 1.5, "type": "сопротивление" if max(h) > price else "поддержка",
                "flip": False, "note": "максимум окна"})
    out.append({"price": min(l), "touches": 1, "strength": 1.5, "type": "поддержка" if min(l) < price else "сопротивление",
                "flip": False, "note": "минимум окна"})
    out.sort(key=lambda x: (-x["strength"], abs(x["price"] - price)))
    picked = []
    for lv in out:
        if all(abs(lv["price"] - p["price"]) > tol for p in picked):
            picked.append(lv)
        if len(picked) >= max_levels:
            break
    picked.sort(key=lambda x: x["price"])
    return picked


def nearest_levels(lvls, price):
    sup = [x for x in lvls if x["price"] < price]
    res = [x for x in lvls if x["price"] > price]
    return (sup[-1] if sup else None), (res[0] if res else None)


def fibonacci(bars, lookback=120):
    w = bars[-lookback:]
    hi_i = max(range(len(w)), key=lambda i: w[i]["h"])
    lo_i = min(range(len(w)), key=lambda i: w[i]["l"])
    hi, lo = w[hi_i]["h"], w[lo_i]["l"]
    up = lo_i < hi_i  # движение вверх: откаты вниз от максимума
    ratios = [0.236, 0.382, 0.5, 0.618, 0.786]
    if up:
        lv = {("%.1f%%" % (r * 100)): hi - (hi - lo) * r for r in ratios}
    else:
        lv = {("%.1f%%" % (r * 100)): lo + (hi - lo) * r for r in ratios}
    return {"direction": "вверх" if up else "вниз", "high": hi, "low": lo, "levels": lv}


# ---------------------------------------------------------------- свечные паттерны

def patterns(bars, atr_last=None):
    """Паттерны последних свечей -> [(название, +1 бычий / -1 медвежий / 0 нейтральный)]."""
    if len(bars) < 3:
        return []
    out = []
    a, b, c = bars[-3], bars[-2], bars[-1]

    def body(x):
        return abs(x["c"] - x["o"])

    def rng(x):
        return max(x["h"] - x["l"], 1e-12)

    def up(x):
        return x["c"] > x["o"]

    def upper(x):
        return x["h"] - max(x["o"], x["c"])

    def lower(x):
        return min(x["o"], x["c"]) - x["l"]

    trend_down = bars[-1]["c"] < (sum(x["c"] for x in bars[-6:-1]) / 5) if len(bars) >= 6 else False
    trend_up = bars[-1]["c"] > (sum(x["c"] for x in bars[-6:-1]) / 5) if len(bars) >= 6 else False
    big = (atr_last or rng(c)) * 0.3

    if body(c) <= rng(c) * 0.1:
        out.append(("доджи", 0))
    if lower(c) >= 2 * body(c) and upper(c) <= body(c) * 0.6 + 1e-12 and rng(c) > big:
        out.append(("молот" if trend_down else "повешенный", 1 if trend_down else -1))
    if upper(c) >= 2 * body(c) and lower(c) <= body(c) * 0.6 + 1e-12 and rng(c) > big:
        out.append(("падающая звезда" if trend_up else "перевёрнутый молот", -1 if trend_up else 1))
    if up(c) and not up(b) and c["c"] >= b["o"] and c["o"] <= b["c"] and body(c) > body(b):
        out.append(("бычье поглощение", 1))
    if not up(c) and up(b) and c["o"] >= b["c"] and c["c"] <= b["o"] and body(c) > body(b):
        out.append(("медвежье поглощение", -1))
    if not up(a) and body(b) <= rng(b) * 0.3 and up(c) and c["c"] > (a["o"] + a["c"]) / 2 and body(a) > big:
        out.append(("утренняя звезда", 1))
    if up(a) and body(b) <= rng(b) * 0.3 and not up(c) and c["c"] < (a["o"] + a["c"]) / 2 and body(a) > big:
        out.append(("вечерняя звезда", -1))
    if c["h"] <= b["h"] and c["l"] >= b["l"]:
        out.append(("внутренний бар", 0))
    if all(up(x) for x in (a, b, c)) and a["c"] < b["c"] < c["c"] and min(body(a), body(b), body(c)) > big:
        out.append(("три белых солдата", 1))
    if all(not up(x) for x in (a, b, c)) and a["c"] > b["c"] > c["c"] and min(body(a), body(b), body(c)) > big:
        out.append(("три чёрные вороны", -1))
    return out


# ---------------------------------------------------------------- дивергенции

def divergence(bars, osc, k=3, lookback=60):
    h, l = [b["h"] for b in bars], [b["l"] for b in bars]
    n = len(bars)
    pts = [p for p in swings(h, l, k) if p[0] >= n - lookback and osc[p[0]] is not None]
    lows = [p for p in pts if p[2] == "L"][-2:]
    highs = [p for p in pts if p[2] == "H"][-2:]
    out = []
    if len(lows) == 2 and lows[1][1] < lows[0][1] and osc[lows[1][0]] > osc[lows[0][0]]:
        out.append(("бычья дивергенция RSI", 1))
    if len(highs) == 2 and highs[1][1] > highs[0][1] and osc[highs[1][0]] < osc[highs[0][0]]:
        out.append(("медвежья дивергенция RSI", -1))
    return out


# ---------------------------------------------------------------- итоговый анализ

def _last(x, back=0):
    vals = [v for v in x if v is not None]
    return vals[-1 - back] if len(vals) > back else None


def analyze(bars, intraday=False):
    """Полный разбор одного таймфрейма. Возвращает индикаторы, уровни, сигналы и оценку от -100 до +100."""
    if len(bars) < 30:
        return {"error": "мало данных: %d баров (нужно от 30)" % len(bars)}
    o = [b["o"] for b in bars]
    h = [b["h"] for b in bars]
    l = [b["l"] for b in bars]
    c = [b["c"] for b in bars]
    v = [b.get("v", 0) for b in bars]
    price = c[-1]
    e20, e50, e200 = ema(c, 20), ema(c, 50), ema(c, 200)
    r = rsi(c, 14)
    ml, ms, mh = macd(c)
    bm, bu, bl, bw = bollinger(c)
    a = atr(h, l, c, 14)
    k, d = stochastic(h, l, c)
    ax, pdi, mdi = adx(h, l, c)
    st, sd = supertrend(h, l, c)
    vavg = sma(v, 20)
    ob = obv(c, v)
    vw = vwap(bars) if intraday else None
    lv = levels(bars)
    sup, res = nearest_levels(lv, price)
    pats = patterns(bars, _last(a))
    divs = divergence(bars, r)

    sig = []  # (текст, вес)

    def add(text, w):
        sig.append((text, w))

    # тренд по средним
    if _last(e200) is not None:
        if price > _last(e20) > _last(e50) > _last(e200):
            add("цена выше EMA20 > EMA50 > EMA200 — восходящий тренд", 20)
        elif price < _last(e20) < _last(e50) < _last(e200):
            add("цена ниже EMA20 < EMA50 < EMA200 — нисходящий тренд", -20)
        else:
            add("цена %s EMA200" % ("выше" if price > _last(e200) else "ниже"), 6 if price > _last(e200) else -6)
    elif _last(e50) is not None:
        if price > _last(e20) > _last(e50):
            add("цена выше EMA20 > EMA50", 14)
        elif price < _last(e20) < _last(e50):
            add("цена ниже EMA20 < EMA50", -14)
    # пересечения EMA20/50 на последних 3 барах
    for back in range(3):
        i = len(c) - 1 - back
        if e50[i] is not None and e50[i - 1] is not None:
            if e20[i - 1] <= e50[i - 1] and e20[i] > e50[i]:
                add("EMA20 пересекла EMA50 вверх %s" % ("на последнем баре" if back == 0 else "%d бар(а) назад" % back), 10)
            if e20[i - 1] >= e50[i - 1] and e20[i] < e50[i]:
                add("EMA20 пересекла EMA50 вниз %s" % ("на последнем баре" if back == 0 else "%d бар(а) назад" % back), -10)
    # ADX
    adx_v = _last(ax)
    if adx_v is not None:
        if adx_v >= 25:
            add("ADX %.0f — тренд сильный (+DI %s -DI)" % (adx_v, ">" if _last(pdi) > _last(mdi) else "<"),
                8 if _last(pdi) > _last(mdi) else -8)
        elif adx_v < 18:
            add("ADX %.0f — тренда нет, рынок во флэте" % adx_v, 0)
    # RSI
    rv = _last(r)
    if rv is not None:
        if rv < 30:
            add("RSI %.0f — перепроданность" % rv, 10)
        elif rv > 70:
            add("RSI %.0f — перекупленность" % rv, -10)
        elif rv > 55:
            add("RSI %.0f — сила покупателей" % rv, 5)
        elif rv < 45:
            add("RSI %.0f — сила продавцов" % rv, -5)
    # MACD
    if _last(mh) is not None and _last(mh, 1) is not None:
        if _last(mh) > 0 and _last(mh, 1) <= 0:
            add("MACD пересёк сигнальную вверх", 12)
        elif _last(mh) < 0 and _last(mh, 1) >= 0:
            add("MACD пересёк сигнальную вниз", -12)
        elif _last(mh) > 0:
            add("MACD выше сигнальной%s" % (", гистограмма растёт" if _last(mh) > _last(mh, 1) else ""), 5)
        else:
            add("MACD ниже сигнальной%s" % (", гистограмма падает" if _last(mh) < _last(mh, 1) else ""), -5)
    # Боллинджер
    if _last(bu) is not None:
        if price > _last(bu):
            add("закрытие выше верхней полосы Боллинджера — импульс (или перегрев)", 4)
        elif price < _last(bl):
            add("закрытие ниже нижней полосы Боллинджера — распродажа (или перепроданность)", -4)
        ws = [x for x in bw[-120:] if x is not None]
        if ws and len(ws) > 30 and _last(bw) <= sorted(ws)[max(0, len(ws) // 10)]:
            add("полосы Боллинджера сжаты — ждите выхода из диапазона", 0)
    # Стохастик
    if _last(k) is not None and _last(d) is not None and _last(k, 1) is not None:
        if _last(k) < 20 and _last(k) > _last(d) and _last(k, 1) <= _last(d, 1):
            add("стохастик развернулся вверх из зоны перепроданности", 8)
        if _last(k) > 80 and _last(k) < _last(d) and _last(k, 1) >= _last(d, 1):
            add("стохастик развернулся вниз из зоны перекупленности", -8)
    # Supertrend
    if _last(sd) is not None:
        add("Supertrend: %s" % ("покупки" if _last(sd) == 1 else "продажи"), 6 if _last(sd) == 1 else -6)
        if sd[-1] is not None and sd[-2] is not None and sd[-1] != sd[-2]:
            add("Supertrend только что сменил направление", 8 * sd[-1])
    # VWAP
    if vw:
        add("цена %s VWAP дня (%.4g)" % ("выше" if price > vw[-1] else "ниже", vw[-1]), 6 if price > vw[-1] else -6)
    # объём
    if _last(vavg):
        ratio = v[-1] / _last(vavg) if _last(vavg) else 0
        if ratio >= 2:
            add("объём последнего бара в %.1f раза выше среднего" % ratio, 6 if c[-1] > o[-1] else -6)
    # OBV
    if len(ob) > 20:
        add("OBV %s за 20 баров" % ("растёт" if ob[-1] > ob[-21] else "падает"), 3 if ob[-1] > ob[-21] else -3)
    # уровни
    atr_v = _last(a) or 0
    if sup and atr_v and (price - sup["price"]) <= atr_v * 0.5:
        add("цена у поддержки %.6g (касаний: %d)" % (sup["price"], sup["touches"]), 5)
    if res and atr_v and (res["price"] - price) <= atr_v * 0.5:
        add("цена у сопротивления %.6g (касаний: %d)" % (res["price"], res["touches"]), -5)
    hh20 = max(h[-21:-1])
    ll20 = min(l[-21:-1])
    if c[-1] > hh20:
        add("пробой максимума 20 баров", 10)
    if c[-1] < ll20:
        add("пробой минимума 20 баров", -10)
    for name, bias in pats:
        add("свечной паттерн: " + name, 6 * bias)
    for name, bias in divs:
        add(name, 10 * bias)

    score = max(-100, min(100, sum(w for _, w in sig)))
    verdict = ("сильный бычий" if score >= 40 else "бычий" if score >= 15 else
               "сильный медвежий" if score <= -40 else "медвежий" if score <= -15 else "нейтральный")
    trend = "вверх" if _last(e50) and price > _last(e50) and (_last(e200) is None or _last(e50) > _last(e200)) else \
        "вниз" if _last(e50) and price < _last(e50) and (_last(e200) is None or _last(e50) < _last(e200)) else "боковик"
    rnd = lambda x: None if x is None else round(x, 6)
    return {
        "price": price, "time": bars[-1]["t"], "bars": len(bars),
        "trend": trend, "score": score, "verdict": verdict,
        "indicators": {
            "ema20": rnd(_last(e20)), "ema50": rnd(_last(e50)), "ema200": rnd(_last(e200)),
            "rsi14": rnd(rv), "macd": rnd(_last(ml)), "macd_signal": rnd(_last(ms)), "macd_hist": rnd(_last(mh)),
            "bb_upper": rnd(_last(bu)), "bb_mid": rnd(_last(bm)), "bb_lower": rnd(_last(bl)), "bb_width_pct": rnd(_last(bw)),
            "atr14": rnd(atr_v), "atr_pct": rnd(atr_v / price * 100 if price else None),
            "stoch_k": rnd(_last(k)), "stoch_d": rnd(_last(d)),
            "adx": rnd(adx_v), "plus_di": rnd(_last(pdi)), "minus_di": rnd(_last(mdi)),
            "supertrend": rnd(_last(st)), "supertrend_dir": _last(sd),
            "vwap": rnd(vw[-1]) if vw else None,
            "volume_ratio": rnd(v[-1] / _last(vavg)) if _last(vavg) else None,
        },
        "levels": lv,
        "support": sup, "resistance": res,
        "patterns": [p[0] for p in pats],
        "signals": [{"text": t, "weight": w} for t, w in sig],
    }


MTF = {
    # режим -> [(таймфрейм, вес, роль)]
    "intraday": [("1d", 0.30, "тренд дня"), ("1h", 0.25, "контекст"), ("15m", 0.25, "сетап"), ("5m", 0.20, "вход")],
    "swing": [("1w", 0.30, "глобальный тренд"), ("1d", 0.35, "тренд"), ("4h", 0.20, "сетап"), ("1h", 0.15, "вход")],
}


def combine(per_tf, mode):
    total, wsum, agree = 0.0, 0.0, []
    for tf, w, role in MTF[mode]:
        a = per_tf.get(tf)
        if not a or "error" in a:
            continue
        total += a["score"] * w
        wsum += w
        agree.append(1 if a["score"] >= 15 else -1 if a["score"] <= -15 else 0)
    score = round(total / wsum) if wsum else 0
    aligned = len(agree) > 1 and (all(x == 1 for x in agree) or all(x == -1 for x in agree))
    verdict = ("сильный бычий" if score >= 40 else "бычий" if score >= 15 else
               "сильный медвежий" if score <= -40 else "медвежий" if score <= -15 else "нейтральный")
    return {"score": score, "verdict": verdict, "aligned": aligned}


def suggest_stop(bars, direction="long", mult=1.5):
    """Стоп по ATR и по ближайшему свингу — для moex-exit."""
    h, l, c = [b["h"] for b in bars], [b["l"] for b in bars], [b["c"] for b in bars]
    a = _last(atr(h, l, c, 14)) or 0
    price = c[-1]
    sw = swings(h, l, 3)
    if direction == "long":
        lows = [p for _, p, kd in sw if kd == "L" and p < price]
        return {"atr": a, "atr_stop": price - mult * a, "swing_stop": (lows[-1] if lows else None)}
    highs = [p for _, p, kd in sw if kd == "H" and p > price]
    return {"atr": a, "atr_stop": price + mult * a, "swing_stop": (highs[-1] if highs else None)}
