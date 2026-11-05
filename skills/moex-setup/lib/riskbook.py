"""Риск портфеля целиком: открытый риск всех позиций (в том числе свинга прошлых дней), дневной и недельный
результат, кривая капитала и просадка от максимума, корреляция позиций, концентрация на эмитенте.

Источники — локальный учёт набора: trader/portfolio.json (позиции и их стопы), trader/state/broker.json
(отправленные заявки и исполнения с результатом).
"""
from datetime import datetime, timedelta

import common as C
import costs

# Отраслевые группы: бумаги одной группы обычно двигаются вместе (нефть, банки, металлы…).
SECTORS = {
    "нефть и газ": ["LKOH", "ROSN", "TATN", "TATNP", "SNGS", "SNGSP", "NVTK", "GAZP", "SIBN", "BANE", "BANEP", "TRNFP", "RNFT"],
    "банки и финансы": ["SBER", "SBERP", "VTBR", "T", "TCSG", "BSPB", "CBOM", "SVCB", "MOEX", "SPBE", "RENI", "SFIN", "LEAS"],
    "металлы и добыча": ["GMKN", "PLZL", "CHMF", "NLMK", "MAGN", "RUAL", "ALRS", "UGLD", "SELG", "MTLR", "MTLRP", "ENPG", "TRMK", "RASP"],
    "технологии": ["YDEX", "OZON", "POSI", "VKCO", "HEAD", "ASTR", "DIAS", "IVAT", "SOFL", "WUSH", "CNRU", "DATA"],
    "потребительский сектор": ["MGNT", "X5", "LENT", "FIXR", "MVID", "BELU", "ABRD", "AQUA", "HNFG", "EUTR", "OKEY"],
    "энергетика": ["IRAO", "HYDR", "FEES", "UPRO", "MSNG", "OGKB", "TGKA", "ELFV", "MRKC", "LSNG", "LSNGP"],
    "строительство": ["PIKK", "SMLT", "LSRG", "ETLN"],
    "телеком": ["MTSS", "RTKM", "RTKMP"],
    "удобрения и химия": ["PHOR", "AKRN", "KAZT", "NKNC", "NKNCP"],
    "транспорт": ["AFLT", "FLOT", "NMTP", "FESH", "GLTR"],
}
SECTOR_OF = {s: name for name, lst in SECTORS.items() for s in lst}


def sector(symbol):
    return SECTOR_OF.get(symbol.upper())


def _events():
    return (C.load_json(C.tpath("state", "broker.json"), {}) or {}).get("events", [])


def _portfolio():
    return C.load_json(C.tpath("portfolio.json"), {}) or {}


def positions():
    """Открытые позиции из учёта: {symbol: {...}}."""
    return {s: p for s, p in (_portfolio().get("positions") or {}).items() if p.get("lots")}


def position_risk(sym, p):
    """₽, которые позиция может потерять до своего стопа. Стоп за точкой входа в прибыль даёт 0 (прибыль уже защищена).
    Без стопа — None (это нарушение правил)."""
    stop = p.get("stop")
    if stop is None:
        return None
    units = p["lots"]
    spec = {"kind": p.get("kind", "shares"), "lot": p.get("lot_size", 1), "stepprice": p.get("stepprice"),
            "minstep": p.get("minstep"), "facevalue": p.get("facevalue")}
    rpp = costs.rub_per_point(spec)
    if p.get("side", "long") == "long":
        return max(0.0, (p["avg"] - stop) * units * rpp)
    return max(0.0, (stop - p["avg"]) * units * rpp)


def pending_today(now):
    """Отправленные сегодня заявки на открытие, по которым ещё нет исполнения."""
    d = now.strftime("%Y-%m-%d")
    ev = _events()
    filled = {e.get("order_id") for e in ev if e.get("type") == "fill"}
    cancelled = {e.get("order_id") for e in ev if e.get("type") == "cancelled"}
    return [e for e in ev if e.get("type") == "placed" and not e.get("closing") and (e.get("at") or "").startswith(d)
            and e.get("order_id") not in filled and e.get("order_id") not in cancelled]


def realized_between(start, end):
    return sum(e.get("pnl") or 0 for e in _events() if e.get("type") == "fill" and start <= (e.get("at") or "")[:10] <= end)


def equity(account_size):
    """Кривая капитала по реализованным результатам: (капитал сейчас, максимум, просадка в долях)."""
    eq = peak = float(account_size)
    for e in sorted((e for e in _events() if e.get("type") == "fill" and e.get("pnl") is not None), key=lambda e: e["at"]):
        eq += e["pnl"]
        peak = max(peak, eq)
    return eq, peak, (eq / peak - 1) if peak else 0.0


def drawdown_multiplier(cfg, dd):
    """Множитель объёма по просадке: 1 — норма, 0,5 — урезанный объём, 0 — пауза."""
    if dd <= -float(cfg.get("dd_stop_pct", 10)) / 100:
        return 0.0
    if dd <= -float(cfg.get("dd_half_pct", 7)) / 100:
        return 0.5
    return 1.0


def correlation(sym_a, sym_b, days=90, end=None):
    """Корреляция дневных доходностей за ~90 дней (по данным ISS). None, если истории мало.
    Сбой сети не глотается: иначе связанная позиция прошла бы проверку как независимая."""
    import moex
    a = moex.candles(sym_a, "1d", days=days + 30, end=end, source="iss")
    b = moex.candles(sym_b, "1d", days=days + 30, end=end, source="iss")
    ca = {x["t"][:10]: x["c"] for x in a}
    cb = {x["t"][:10]: x["c"] for x in b}
    dates = sorted(set(ca) & set(cb))[-days:]
    ra = [ca[dates[i]] / ca[dates[i - 1]] - 1 for i in range(1, len(dates))]
    rb = [cb[dates[i]] / cb[dates[i - 1]] - 1 for i in range(1, len(dates))]
    n = len(ra)
    if n < 30:
        return None
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = sum((x - ma) ** 2 for x in ra) ** 0.5
    vb = sum((y - mb) ** 2 for y in rb) ** 0.5
    return cov / (va * vb) if va and vb else None


def correlated_group(symbol, open_pos, cfg, offline=False, now=None):
    """Открытые позиции, которые надо считать вместе с новой: та же отрасль или корреляция выше порога."""
    out = []
    sec = sector(symbol)
    thr = float(cfg.get("corr_threshold", 0.7))
    for s in open_pos:
        if s == symbol:
            continue
        if sec and sector(s) == sec:
            out.append((s, "одна отрасль: %s" % sec))
            continue
        if not offline:
            r = correlation(symbol, s, end=now)
            if r is not None and r >= thr:
                out.append((s, "корреляция %s" % C.num(r, 2)))
    return out


def closing_problem(o, fact):
    """Заявка с пометкой «закрытие» обходит лимиты, поэтому она обязана только уменьшать позицию на счёте.
    fact: {тикер: (штук со знаком, тип)}. -> текст причины или None."""
    qty = (fact.get(o["symbol"]) or (0, None))[0]
    if not qty:
        return "на счёте нет позиции %s" % o["symbol"]
    if (qty > 0) != (o["side"] == "sell"):
        return "заявка увеличила бы позицию %s, а не закрыла" % o["symbol"]
    per_lot = 1 if o.get("kind") in ("futures", "bonds", "corp") else (o.get("lot_size") or 1)
    if o["lots"] * per_lot > abs(qty) + 1e-6:
        return "закрытие %s больше позиции: %s против %s" % (o["symbol"], C.num(o["lots"] * per_lot, 0), C.num(abs(qty), 0))
    return None


def snapshot(cfg, now, offline=False):
    """Сводка риска портфеля на момент now."""
    account = float(cfg["account_size"])
    pos = positions()
    risks = {s: position_risk(s, p) for s, p in pos.items()}
    pend = pending_today(now)
    open_risk = sum(r for r in risks.values() if r) + sum(e.get("max_loss", 0) for e in pend if e["symbol"] not in pos)
    d = now.strftime("%Y-%m-%d")
    monday = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
    eq, peak, dd = equity(account)
    return {
        "account": account, "equity": eq, "peak": peak, "drawdown": dd, "dd_mult": drawdown_multiplier(cfg, dd),
        "positions": pos, "position_risk": risks, "no_stop": [s for s, r in risks.items() if r is None],
        "pending": pend, "open_risk": open_risk,
        "realized_today": realized_between(d, d), "realized_week": realized_between(monday, d),
        "trades_today": len([e for e in _events() if e.get("type") == "placed" and not e.get("closing")
                             and (e.get("at") or "").startswith(d)]),
        "cash": _portfolio().get("cash"),
    }
