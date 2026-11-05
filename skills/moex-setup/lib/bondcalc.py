"""Расчёты по облигациям: денежный поток, эффективная доходность, дюрация, доходность после налога."""
from datetime import datetime


def _d(s):
    return datetime.strptime(s[:10], "%Y-%m-%d")


def pv(flows, settle, y):
    return sum(a / (1 + y) ** ((_d(d) - settle).days / 365.0) for d, a in flows)


def irr(flows, price, settle):
    """Эффективная годовая доходность в долях: приведённая стоимость потока равна цене с НКД."""
    if not flows or price <= 0:
        return None
    lo, hi = -0.9, 100.0
    if (pv(flows, settle, lo) - price) * (pv(flows, settle, hi) - price) > 0:
        return None
    for _ in range(100):
        mid = (lo + hi) / 2
        if pv(flows, settle, mid) > price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def duration(flows, settle, y):
    """(дюрация Маколея в годах, модифицированная дюрация)."""
    total = pv(flows, settle, y)
    mac = sum((_d(d) - settle).days / 365.0 * a / (1 + y) ** ((_d(d) - settle).days / 365.0) for d, a in flows) / total
    return mac, mac / (1 + y)


def build_flows(coupons, amorts, offers, settle, facevalue):
    """Поток на одну облигацию до оферты put или погашения.

    Возвращает {'flows': [(дата, ₽)], 'coupons': [(дата, ₽)], 'horizon': дата, 'horizon_type', 'unknown': неизвестных купонов}.
    """
    s = settle.strftime("%Y-%m-%d")
    put = sorted(o["offerdate"] for o in offers if o.get("offerdate") and o["offerdate"] > s
                 and "call" not in str(o.get("offertype") or "").lower() and "колл" not in str(o.get("offertype") or "").lower())
    mat = max([a["amortdate"] for a in amorts if a.get("amortdate")] or [s])
    horizon = put[0] if put and put[0] < mat else mat
    cps, unknown = [], 0
    for c in coupons:
        d = c.get("coupondate")
        if not d or d <= s or d > horizon:
            continue
        v = c.get("value_rub") if c.get("value_rub") is not None else c.get("value")
        if v is None:
            unknown += 1
            continue
        cps.append((d, float(v)))
    am = []
    left = float(facevalue)
    for a in sorted(amorts, key=lambda x: x.get("amortdate") or ""):
        d = a.get("amortdate")
        if not d or d <= s:
            continue
        v = float(a.get("value_rub") if a.get("value_rub") is not None else a.get("value") or 0)
        if d < horizon:
            am.append((d, v))
            left -= v
    am.append((horizon, left))
    return {"flows": sorted(cps + am), "coupons": sorted(cps), "horizon": horizon,
            "horizon_type": "оферта" if horizon != mat else "погашение", "unknown": unknown}


def net_yield(info, clean_rub, accrued, settle, tax=0.13, fee=0.0005):
    """Доходность после налога и комиссии покупки.

    НДФЛ удерживается с купонов (НКД при покупке уменьшает первый купон) и с разницы между выплатой номинала и
    ценой покупки, если она положительная. Убыток от цены и зачёт с другими сделками не учитываются.
    """
    flows = []
    left_nkd = accrued
    cps = dict()
    for d, a in info["coupons"]:
        cps[d] = cps.get(d, 0) + a
    for d in sorted(cps):
        base = max(0.0, cps[d] - left_nkd)
        left_nkd = max(0.0, left_nkd - cps[d])
        flows.append((d, cps[d] - base * tax))
    principal = principal_part(info)
    total_nom = sum(a for _, a in principal)
    for d, a in principal:
        cost = clean_rub * a / total_nom if total_nom else 0
        gain = max(0.0, a - cost)
        flows.append((d, a - gain * tax))
    return irr(sorted(flows), (clean_rub + accrued) * (1 + fee), settle)


def principal_part(info):
    """Выплаты номинала: поток без купонов."""
    rest = list(info["flows"])
    for c in info["coupons"]:
        rest.remove(c)
    return rest


def price_change(mod_dur, dy_pp):
    """Изменение цены в %, если доходность изменится на dy_pp процентных пунктов (первое приближение)."""
    return -mod_dur * dy_pp
