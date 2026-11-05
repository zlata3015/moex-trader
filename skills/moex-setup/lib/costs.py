"""Издержки сделки: комиссия брокера/биржи и проскальзывание. Всё в цене инструмента и в рублях."""
import common as C


def per_unit(spec, price, cfg=None):
    """Издержки туда-обратно на 1 единицу цены инструмента (акцию, контракт, облигацию), в единицах цены."""
    cfg = cfg or C.config()
    step = spec.get("minstep") or 0.01
    slip = 2 * cfg.get("slippage_ticks", 1) * step
    if spec.get("kind") == "futures":
        sp = spec.get("stepprice") or step
        fee_pts = 2 * cfg.get("futures_fee_rub", 2.0) / (sp / step)
        return fee_pts + slip
    return price * cfg.get("fee_pct", 0.05) / 100 * 2 + slip


def rub_per_point(spec):
    """Сколько рублей приносит движение цены на 1 пункт для 1 лота / контракта / облигации."""
    k = spec.get("kind")
    if k == "futures":
        return (spec.get("stepprice") or spec.get("minstep") or 1) / (spec.get("minstep") or 1)
    if k == "bonds":
        return (spec.get("facevalue") or 1000) / 100.0
    return float(spec.get("lot") or 1)


def in_r(spec, entry, risk_per_unit, cfg=None):
    """Издержки сделки, выраженные в долях риска R."""
    return per_unit(spec, entry, cfg) / risk_per_unit if risk_per_unit else 0.0
