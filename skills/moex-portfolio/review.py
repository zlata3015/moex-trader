#!/usr/bin/env python3
"""moex-portfolio: обзор всего портфеля.

  review.py              доли рынков, концентрация, открытый риск, ГО, события, стресс-сценарии
  review.py --offline    без обращения к бирже: цены позиций берутся из учёта
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "moex-setup", "lib"))
import common as C  # noqa: E402
import costs  # noqa: E402
import moex  # noqa: E402
import riskbook  # noqa: E402

# Изменения за реальные периоды, %. Источник: расчёт проекта по MCFTR, RGBITR и золоту в рублях.
SCENARIOS = [
    ("февраль–март 2020", {"shares": -32.2, "ofz": -9.8}),
    ("февраль–март 2022", {"shares": -31.9, "ofz": -14.3}),
    ("май–декабрь 2024", {"shares": -26.4, "ofz": -6.0}),
]
GROUP = {"shares": "акции", "etf": "фонды", "bonds": "облигации", "corp": "облигации", "futures": "фьючерсы", "currency": "валюта"}


def value_of(sym, p, offline):
    """(стоимость ₽, цена, спецификация). Для фьючерсов стоимость — полная, не ГО."""
    kind = p.get("kind", "shares")
    spec = {"kind": kind, "lot": p.get("lot_size", 1), "stepprice": p.get("stepprice"), "minstep": p.get("minstep"), "facevalue": p.get("facevalue")}
    px, live = p["avg"], {}
    if not offline:
        try:
            live = moex.spec(sym, kind if kind in moex.KINDS else None)
            px = live.get("last") or px
        except C.NetError:
            pass
    spec.update({k: v for k, v in live.items() if k in ("stepprice", "minstep", "facevalue", "initial_margin") and v})
    return px * p["lots"] * costs.rub_per_point(spec), px, dict(spec, **{k: live.get(k) for k in ("next_coupon", "coupon", "initial_margin", "last_trade_date")})


def stress(rows, shock):
    """Убыток сценария без учёта стопов: на гэпе и при закрытии торгов стоп не защищает."""
    total = 0.0
    for r in rows:
        k = r["kind"]
        if k == "shares":
            total += r["value"] * shock["shares"] / 100
        elif k in ("bonds", "corp"):
            total += r["value"] * shock["ofz"] / 100
        elif k == "futures":
            total += r["value"] * shock["shares"] / 100 * (1 if r["side"] == "long" else -1)
    return total


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true"); ap.add_argument("--now")
    a = ap.parse_args()
    cfg = C.config()
    now = C.now_msk(a.now)
    st = riskbook.snapshot(cfg, now, a.offline)
    cap = st["equity"]
    rows, go = [], 0.0
    for s, p in st["positions"].items():
        v, px, sp = value_of(s, p, a.offline)
        m = (sp.get("initial_margin") or 0) * p["lots"] if p.get("kind") == "futures" else 0
        go += m
        rows.append({"sym": s, "kind": p.get("kind", "shares"), "side": p.get("side", "long"), "value": v, "go": m, "px": px, "spec": sp,
                     "risk": st["position_risk"].get(s), "sector": riskbook.sector(s) or GROUP.get(p.get("kind"), "прочее")})
    print("Портфель на %s МСК · капитал %s · просадка %s" % (C.fmt_dt(now), C.rub(cap, 0), C.pct(st["drawdown"] * 100, 1)))
    if not rows:
        print("Открытых позиций нет." + (" Деньги: %s" % C.rub(st["cash"], 0) if st.get("cash") is not None else ""))
        return
    print(C.table([[r["sym"], GROUP.get(r["kind"], r["kind"]), r["side"], C.rub(r["value"], 0), C.pct(r["value"] / cap * 100, 1),
                    C.rub(r["risk"], 0) if r["risk"] is not None else "НЕТ СТОПА"] for r in rows],
                  ["тикер", "рынок", "сторона", "стоимость", "% капитала", "риск до стопа"]))
    by, sec = {}, {}
    for r in rows:
        if r["kind"] != "futures":
            by[GROUP.get(r["kind"], r["kind"])] = by.get(GROUP.get(r["kind"], r["kind"]), 0) + r["value"]
        sec[r["sector"]] = sec.get(r["sector"], 0) + r["value"]
    print("\nДоли: " + " · ".join("%s %s" % (k, C.pct(v / cap * 100, 1)) for k, v in by.items()))
    if st.get("cash") is not None:
        print("Деньги: %s" % C.rub(st["cash"], 0))
    top = max(sec, key=sec.get)
    print("Крупнейшая группа: %s, %s капитала" % (top, C.pct(sec[top] / cap * 100, 1)))
    mo = C.money_limit(cfg.get("max_open_risk", "3%"), st["account"])
    print("Открытый риск: %s из %s" % (C.rub(st["open_risk"], 0), C.rub(mo, 0)))
    fut = sum(r["value"] for r in rows if r["kind"] == "futures")
    if fut:
        print("Фьючерсы: ГО %s (%s капитала), полная стоимость %s (%s капитала)" % (
            C.rub(go, 0), C.pct(go / cap * 100, 1), C.rub(fut, 0), C.num(fut / cap, 2)))
    if st["no_stop"]:
        print("⛔ Без стопа: " + ", ".join(st["no_stop"]))
    ev = []
    for r in rows:
        if r["spec"].get("next_coupon"):
            ev.append("%s купон %s" % (r["sym"], r["spec"]["next_coupon"]))
        if r["spec"].get("last_trade_date"):
            ev.append("%s экспирация %s" % (r["sym"], r["spec"]["last_trade_date"]))
        if r["kind"] == "shares" and not a.offline:
            try:
                d = moex.next_dividend(r["sym"], now)
                if d and d.get("date"):
                    ev.append("%s отсечка %s" % (r["sym"], d["date"]))
            except C.NetError:
                pass
    if ev:
        print("События: " + "; ".join(ev))
    print("\nСтресс без учёта стопов:")
    for name, shock in SCENARIOS:
        x = stress(rows, shock)
        print("  %s: %s (%s капитала)" % (name, C.rub(x, 0), C.pct(x / cap * 100, 1)))
    print("Сценарии показывают прошлые падения индексов. Отдельная бумага и будущий кризис могут упасть сильнее.")


if __name__ == "__main__":
    main()
