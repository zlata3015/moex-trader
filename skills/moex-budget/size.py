#!/usr/bin/env python3
"""moex-budget: размер позиции в лотах под ваш риск — акции, фонды, валюта, облигации, фьючерсы (по ГО).

  size.py --write                        по plan.json и config.json -> trader/orders.json
  size.py --account 200000 --max-loss 1% --write
  size.py --entry-type market
"""
import argparse
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "moex-setup", "lib"))
import common as C  # noqa: E402
import costs  # noqa: E402
import moex  # noqa: E402


def size(plan, spec, cfg, account, max_loss):
    long_ = plan["direction"] == "long"
    entry, stop, target = plan["entry"], plan["stop"], plan["target"]
    risk_pts = abs(entry - stop)
    rpp = costs.rub_per_point(spec)                     # ₽ за 1 пункт на 1 лот/контракт/облигацию
    cost_pts = costs.per_unit(spec, entry, cfg)         # издержки туда-обратно, в пунктах
    unit = spec.get("lot", 1) if spec["kind"] not in ("futures", "bonds") else 1
    risk_lot = (risk_pts + cost_pts) * rpp              # ₽ риска на 1 лот с издержками
    if spec["kind"] == "futures":
        capital_lot = spec.get("initial_margin") or entry * rpp * 0.15
    elif spec["kind"] == "bonds":
        capital_lot = entry / 100 * (spec.get("facevalue") or 1000) + (spec.get("accrued") or 0)
    else:
        capital_lot = entry * unit
    by_risk = math.floor(max_loss / risk_lot) if risk_lot > 0 else 0
    by_size = math.floor(account * cfg["max_position_pct"] / 100 / capital_lot) if capital_lot > 0 else 0
    lots = max(0, min(by_risk, by_size))
    limiter = "риск на сделку" if by_risk <= by_size else "лимит %d%% счёта на позицию" % cfg["max_position_pct"]
    order = {
        "symbol": plan["symbol"], "kind": spec["kind"], "name": spec.get("name"),
        "side": "buy" if long_ else "sell", "direction": plan["direction"], "lots": lots,
        "units": lots * unit, "lot_size": unit, "type": "limit",
        "entry": entry, "stop": stop, "target": target,
        "max_loss": round(lots * risk_lot, 2), "max_loss_at_stop": round(lots * risk_pts * rpp, 2),
        "costs_rub": round(lots * cost_pts * rpp, 2),
        "profit_at_target": round(lots * (abs(target - entry) - cost_pts) * rpp, 2) if target is not None else None,
        "capital": round(lots * capital_lot, 2), "rr": plan.get("rr"),
        "target_cost_multiple": (round(abs(target - entry) / cost_pts, 1) if cost_pts and target is not None else None),
        "cost_share_of_risk": round(cost_pts / risk_pts, 3) if risk_pts else None, "trail": plan.get("trail"),
        "mode": plan["mode"], "flat_by": plan.get("flat_by"), "max_hold_days": plan.get("max_hold_days"),
        "setup": plan.get("setup"), "strategy": cfg["strategy"], "rank": plan.get("rank"), "date": plan["date"], "limiter": limiter,
        "spec": {k: spec.get(k) for k in ("minstep", "stepprice", "initial_margin", "high_limit", "low_limit",
                                           "last_trade_date", "turnover", "facevalue", "lot", "last", "issuer", "issuer_name")},
    }
    order["fingerprint"] = C.fingerprint(order)
    return order, {"by_risk": by_risk, "by_size": by_size, "risk_lot": risk_lot, "capital_lot": capital_lot}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--account", type=float); ap.add_argument("--max-loss")
    ap.add_argument("--entry-type", choices=["limit", "market"], default="limit")
    ap.add_argument("--kind", choices=list(moex.KINDS))
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    cfg = C.config()
    plan = C.load_json(C.tpath("plan.json"))
    if not plan:
        C.die("Нет trader/plan.json — сначала moex-exit plan --write")
    account = a.account or cfg["account_size"]
    max_loss = C.money_limit(a.max_loss or cfg["max_loss_per_trade"], account)
    import riskbook
    snap = riskbook.snapshot(cfg, C.now_msk())
    dd_note = ""
    if snap["dd_mult"] < 1:
        max_loss *= snap["dd_mult"]
        dd_note = " (урезан: просадка %s от максимума капитала)" % C.pct(snap["drawdown"] * 100, 1)
    room = C.money_limit(cfg.get("max_open_risk", "3%"), account) - snap["open_risk"]
    lost_today = -min(0, snap["realized_today"])
    room = min(room, C.money_limit(cfg["daily_loss_limit"], account) - lost_today)
    if room < max_loss:
        max_loss = max(0.0, room)
        dd_note += " (урезан до остатка лимитов: открытый риск %s, потеряно сегодня %s)" % (
            C.rub(snap["open_risk"], 0), C.rub(lost_today, 0))
    try:
        spec = moex.spec(plan["symbol"], a.kind or plan["kind"])
        if spec["kind"] in ("bonds", "corp"):
            spec.update(moex.issuer(plan["symbol"]) or {})
    except C.NetError as e:
        C.die(str(e))
    order, info = size(plan, spec, cfg, account, max_loss)
    order["type"] = a.entry_type
    order["fingerprint"] = C.fingerprint(order)
    k = spec["kind"]
    unit_word = {"futures": "контракт", "bonds": "облигация"}.get(k, "лот")
    print("Размер позиции · %s (%s) · счёт %s, риск на сделку %s%s" % (order["symbol"], order.get("name") or k, C.rub(account, 0),
                                                                      C.rub(max_loss, 0), dd_note))
    if snap["dd_mult"] == 0:
        print("  ⛔ просадка %s — пауза в торговле до разбора (dd_stop_pct). Новые позиции не открываем." % C.pct(snap["drawdown"] * 100, 1))
    if k == "futures":
        print("  шаг цены %s = %s, ГО %s на контракт" % (("%g" % spec["minstep"]).replace(".", ","), C.rub(spec.get("stepprice")), C.rub(spec.get("initial_margin"), 0)))
    elif k != "bonds":
        print("  лот = %d шт., шаг цены %s" % (order["lot_size"], ("%g" % spec["minstep"]).replace(".", ",")))
    print("  риск на 1 %s с издержками: %s → по риску можно %d, по размеру позиции %d" % (
        unit_word, C.rub(info["risk_lot"]), info["by_risk"], info["by_size"]))
    if order["lots"] == 0:
        print("  ⛔ даже 1 %s превышает ваш риск или лимит позиции — сделку пропускаем (или расширьте лимиты осознанно)" % unit_word)
    else:
        what = "%d %s" % (order["lots"], unit_word + ("ов" if k != "bonds" and order["lots"] > 4 else "а" if k != "bonds" and order["lots"] > 1 else ""))
        if k not in ("futures", "bonds"):
            what += " (%d шт.)" % order["units"]
        print("  ИТОГ: %s %s, ограничение — %s" % ("купить" if order["side"] == "buy" else "продать", what, order["limiter"]))
        print("  %s: %s · убыток на стопе ≈ %s (+ издержки %s) · %s" % (
            "ГО" if k == "futures" else "сумма", C.rub(order["capital"], 0), C.rub(order["max_loss_at_stop"], 0),
            C.rub(order["costs_rub"], 0), ("прибыль на цели ≈ " + C.rub(order["profit_at_target"], 0)) if order["target"] is not None
            else "цели нет — выход по подтягиваемому стопу"))
        print("  заявка: %s %s %d, стоп-лосс %s, %s" % (
            "ЛИМИТ" if order["type"] == "limit" else "РЫНОК", order["symbol"], order["lots"], C.px(order["stop"]),
            ("тейк-профит " + C.px(order["target"])) if order["target"] is not None else "трейлинг %s ATR" % C.num(order.get("trail") or 3, 1)))
        if order["target_cost_multiple"] is not None and order["target_cost_multiple"] < cfg["min_target_cost_multiple"]:
            print("  ⚠ до цели всего %s издержки сделки — комиссии съедят прибыль; moex-risk заблокирует" % C.num(order["target_cost_multiple"], 1))
        print("  Стоп — это план, а не гарантия: на гэпе он исполнится хуже.")
    if a.write:
        C.save_json(C.tpath("orders.json"), [order])
        print("\nЗаписано: %s" % C.tpath("orders.json"))


if __name__ == "__main__":
    main()
