"""Проверка гипотез: одна мерка для всех, реестр всех попыток, вердикт по заранее заданным порогам.

Расчёт только на стандартной библиотеке. Ряды — списки (дата 'ГГГГ-ММ-ДД', значение) по возрастанию дат.
"""
import datetime as dt
import json
import random

VERDICTS = ("planned", "rejected", "risk_filter", "candidate", "confirmed", "sandbox", "live")
FOR_TRADING = ("confirmed", "sandbox", "live")
MANUAL = ("sandbox", "live")

CRITERIA = {
    "candidate_p": 0.20,        # кандидат: можно в список на доработку, но не в рекомендации
    "confirmed_alpha": 0.05,    # делится на число гипотез в семействе (поправка Бонферрони)
    "min_positive_periods": 2,  # кандидат: премия к деньгам плюс минимум в 2 периодах
    "min_trades_frequent": 100, # частый сигнал: этап 3 проекта
    "min_legs_rare": 5,         # редкий сигнал: минимум независимых заходов
    "min_win_share_rare": 2 / 3,# редкий сигнал: доля заходов лучше денег
    "filter_dd_ratio": 0.5,     # фильтр риска: просадка вдвое меньше постоянного удержания
}


def days_between(a, b):
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def step_values(calendar, series):
    """Значение ряда на каждую дату календаря: последнее известное, до первого — None."""
    out, i, cur = [], 0, None
    for d in calendar:
        while i < len(series) and series[i][0] <= d:
            cur = series[i][1]
            i += 1
        out.append(cur)
    return out


def money_index(calendar, key_rate, spread=1.0):
    """Деньги: ключевая ставка минус spread, начисление по календарным дням по вчерашней ставке."""
    k = step_values(calendar, key_rate)
    out = [1.0]
    for i in range(1, len(calendar)):
        r = max((k[i - 1] if k[i - 1] is not None else 0.0) - spread, 0.0) / 100 / 365
        out.append(out[-1] * (1 + r) ** days_between(calendar[i - 1], calendar[i]))
    return out


def positions(calendar, signal, lag=2):
    """Сигнал на дату t: покупка закрытием t+1, доход с t+2. lag задаёт этот сдвиг в торговых днях."""
    raw = [1.0 if v else 0.0 for v in step_values(calendar, signal)]
    return ([0.0] * lag + raw)[:len(calendar)]


def daily_returns(calendar, asset, money, pos, cost):
    """Доход по дням: в рынке — актив, вне рынка — деньги, cost за сторону при смене позиции."""
    out = []
    for i in range(1, len(calendar)):
        ra = asset[i] / asset[i - 1] - 1
        rm = money[i] / money[i - 1] - 1
        out.append(pos[i] * ra + (1 - pos[i]) * rm - abs(pos[i] - pos[i - 1]) * cost)
    return out


def annual(dates, rets):
    if not rets:
        return 0.0
    v = 1.0
    for r in rets:
        v *= 1 + r
    yrs = days_between(dates[0], dates[-1]) / 365.25
    return (v ** (1 / yrs) - 1) * 100 if yrs > 0 else 0.0


def max_drawdown(rets):
    v = peak = 1.0
    worst = 0.0
    for r in rets:
        v *= 1 + r
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    return worst * 100


def after_tax_annual(dates, rets, tax=0.13):
    """Налог с положительного результата каждого календарного года, без переноса убытков."""
    years = {}
    for d, r in zip(dates, rets):
        years[d[:4]] = years.get(d[:4], 1.0) * (1 + r)
    v = 1.0
    for y in sorted(years):
        g = years[y] - 1
        v *= 1 + (g * (1 - tax) if g > 0 else g)
    yrs = days_between(dates[0], dates[-1]) / 365.25
    return (v ** (1 / yrs) - 1) * 100


def monthly_sum(dates, values):
    m = {}
    for d, x in zip(dates, values):
        m[d[:7]] = m.get(d[:7], 0.0) + x
    return [m[k] for k in sorted(m)]


def block_bootstrap_p(x, block=6, n=2000, seed=1):
    """Доля повторных выборок блоками со средним <= 0: чем меньше, тем надёжнее среднее выше нуля."""
    if not x:
        return 1.0
    rng = random.Random(seed)
    size = len(x)
    bad = 0
    for _ in range(n):
        s, got = 0.0, 0
        while got < size:
            start = rng.randrange(size)
            for j in range(block):
                if got >= size:
                    break
                s += x[(start + j) % size]
                got += 1
        if s <= 0:
            bad += 1
    return bad / n


def legs(calendar, asset, money, pos):
    """Заходы в рынок: (начало, конец, доход актива, доход денег)."""
    out, i, n = [], 1, len(calendar)
    while i < n:
        if pos[i] > 0 and pos[i - 1] == 0:
            j = i
            while j + 1 < n and pos[j + 1] > 0:
                j += 1
            out.append((calendar[i - 1], calendar[j], asset[j] / asset[i - 1] - 1, money[j] / money[i - 1] - 1))
            i = j + 1
        else:
            i += 1
    return out


def evaluate(calendar, asset, key_rate, signal, since, periods, cost=0.001, spread=1.0, money=None):
    """Все показатели гипотезы вида «в рынке по сигналу, иначе деньги». Проценты и п.п."""
    custom_base = money is not None  # база сравнения вместо денег: просадка сравнивается с ней
    money = money or money_index(calendar, key_rate, spread)
    i0 = next(i for i, d in enumerate(calendar) if d >= since)
    cal = calendar[i0:]
    a, m = asset[i0:], money[i0:]

    def run(lag=2, c=cost, sig=signal):
        p = positions(calendar, sig, lag)[i0:]
        return p, daily_returns(cal, a, m, p, c)

    pos, r = run()
    d1 = cal[1:]
    r_money = [m[i] / m[i - 1] - 1 for i in range(1, len(cal))]
    r_asset = [a[i] / a[i - 1] - 1 for i in range(1, len(cal))]
    money_ann = annual(cal, r_money)

    def premium(rr, mm, dd):
        return annual(dd, rr) - annual(dd, mm)

    per = {}
    for name, y0, y1 in periods:
        idx = [i for i, d in enumerate(d1) if y0 <= d[:4] <= y1]
        if len(idx) < 2:
            continue
        dd = [d1[i] for i in idx]
        per[name] = round(premium([r[i] for i in idx], [r_money[i] for i in idx], dd), 2)

    # задержка на месяц: 21 торговый день
    _, r_late = run(lag=23)
    _, r_cost = run(c=cost * 2)
    ex = monthly_sum(d1, [x - y for x, y in zip(r, r_money)])
    lg = legs(cal, a, m, pos)
    return {
        "since": cal[0], "till": cal[-1],
        "annual": round(annual(cal, r), 2),
        "money": round(money_ann, 2),
        "premium": round(annual(cal, r) - money_ann, 2),
        "after_tax": round(after_tax_annual(d1, r), 2),
        "money_after_tax": round(after_tax_annual(d1, r_money), 2),
        "max_dd": round(max_drawdown(r), 2),
        "hold_annual": round(annual(cal, r_asset), 2),
        "hold_dd": round(max_drawdown(r_money if custom_base else r_asset), 2),
        "in_market": round(sum(pos) / len(pos) * 100, 1),
        "switches": sum(1 for i in range(1, len(pos)) if pos[i] != pos[i - 1]),
        "legs": len(lg),
        "legs_won": sum(1 for x in lg if x[2] > x[3]),
        "p": round(block_bootstrap_p(ex), 3),
        "periods": per,
        "premium_late": round(annual(cal, r_late) - money_ann, 2),
        "premium_cost2": round(annual(cal, r_cost) - money_ann, 2),
    }


def verdict(m, family_size, kind="rare", crit=None):
    """Вердикт только по порогам. Возвращает (статус, причины)."""
    c = crit or CRITERIA
    prem, why = m["premium"], []
    per = list(m["periods"].values())
    pos_per = sum(1 for x in per if x > 0)
    legs_n, won = m["legs"], m["legs_won"]
    if kind == "frequent":
        enough = m["switches"] / 2 >= c["min_trades_frequent"]
        size_note = "сделок %d из %d" % (m["switches"] // 2, c["min_trades_frequent"])
    else:
        enough = legs_n >= c["min_legs_rare"] and legs_n and won / legs_n >= c["min_win_share_rare"]
        size_note = "заходов %d, лучше денег %d" % (legs_n, won)
    alpha = c["confirmed_alpha"] / max(family_size, 1)
    if (prem > 0 and per and pos_per == len(per) and m["p"] <= alpha and enough
            and m["premium_late"] > 0 and m["premium_cost2"] > 0):
        return "confirmed", ["все пороги пройдены, p=%.3f <= %.4f, %s" % (m["p"], alpha, size_note)]
    if prem <= 0:
        why.append("премия к деньгам %+.2f" % prem)
    if m["p"] > c["candidate_p"]:
        why.append("p=%.3f выше %.2f" % (m["p"], c["candidate_p"]))
    if pos_per < c["min_positive_periods"]:
        why.append("плюс в %d периодах из %d" % (pos_per, len(per)))
    if m["premium_late"] <= 0:
        why.append("задержка на месяц %+.2f" % m["premium_late"])
    if m["premium_cost2"] <= 0:
        why.append("двойные издержки %+.2f" % m["premium_cost2"])
    if not why:
        return "candidate", ["прошла порог кандидата, до подтверждения: p <= %.4f, все периоды, %s"
                             % (alpha, size_note)]
    if prem >= 0 and abs(m["max_dd"]) <= abs(m["hold_dd"]) * c["filter_dd_ratio"]:
        return "risk_filter", ["просадка %.1f против %.1f при удержании; не сигнал покупки: %s"
                               % (m["max_dd"], m["hold_dd"], "; ".join(why))]
    return "rejected", why


# ---------------------------------------------------------------- реестр

def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path, reg):
    reg["hypotheses"].sort(key=lambda h: h["id"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=1)
        f.write("\n")


def find(reg, hid):
    for h in reg["hypotheses"]:
        if h["id"] == hid:
            return h
    return None


def family_size(reg, family):
    """Сколько гипотез семейства уже проверено или запланировано: отвергнутые тоже считаются."""
    return sum(1 for h in reg["hypotheses"] if h["family"] == family)


def add(reg, hid, claim, family, rule, kind="rare", source="", prereg=True):
    if find(reg, hid):
        raise ValueError("гипотеза %s уже есть, правило после записи не меняется: заведите новую" % hid)
    if kind not in ("rare", "frequent"):
        raise ValueError("kind: rare или frequent")
    if not rule.strip():
        raise ValueError("правило нужно записать до проверки")
    h = {"id": hid, "claim": claim, "family": family, "rule": rule, "kind": kind, "source": source,
         "prereg": prereg, "status": "planned", "result": None, "reasons": []}
    reg["hypotheses"].append(h)
    return h


def record(reg, hid, metrics):
    """Записать результат: статус ставит только verdict. Повторная запись считается новой попыткой."""
    h = find(reg, hid)
    if h is None:
        raise ValueError("нет гипотезы %s: сначала add" % hid)
    if h["status"] in MANUAL:
        raise ValueError("%s уже в статусе %s, результат песочницы и реальных сделок вносится отдельно" % (hid, h["status"]))
    st, why = verdict(metrics, family_size(reg, h["family"]), h["kind"], reg.get("criteria"))
    h["attempts"] = h.get("attempts", 0) + 1
    h["status"], h["reasons"], h["result"] = st, why, metrics
    return h


def reject_external(reg, hid, reason, doc):
    """Отказ, полученный другой проверкой. Вручную можно только отвергнуть, но не подтвердить."""
    h = find(reg, hid)
    if h is None or h["status"] != "planned":
        raise ValueError("переносить можно только запланированную гипотезу")
    h["status"], h["reasons"], h["external"] = "rejected", [reason], doc
    return h


def promote(reg, hid, status):
    """Только вперёд по этапам: confirmed -> sandbox -> live."""
    h = find(reg, hid)
    order = ("confirmed", "sandbox", "live")
    if h is None or h["status"] not in order[:-1] or status != order[order.index(h["status"]) + 1]:
        raise ValueError("перевод запрещён: нужен следующий этап после confirmed")
    h["status"] = status
    return h


def tradable(reg):
    """Единственный список, откуда сигнал может попасть в рекомендацию."""
    return [h for h in reg["hypotheses"] if h["status"] in FOR_TRADING]
