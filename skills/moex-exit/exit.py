#!/usr/bin/env python3
"""moex-exit: план выхода до входа — стоп, цель, время.

  exit.py plan --stop or-mid --target 1R --write           по entry.json (сработавший сетап)
  exit.py plan --symbol SBER --entry 325 --stop atr:1.5 --target resistance --mode swing --write
  exit.py plan --symbol SBER --stop atr:2 --target none --trail 3 --mode position --write   по тренду, без цели
  exit.py update                                           подтянуть стоп открытой позиции по новым свечам
Стопы: or-mid, or-low, atr[:K], swing, level:X, pct:X.   Цели: NR (1R, 2R…), resistance, level:X, pct:X.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "moex-setup", "lib"))
import common as C  # noqa: E402
import moex  # noqa: E402
import ta  # noqa: E402


def build(a):
    cfg = C.config()
    e = C.load_json(C.tpath("entry.json"), {}) or {}
    sym = a.symbol or e.get("symbol")
    if not sym:
        C.die("Не знаю инструмент: укажите --symbol или сначала запустите moex-entry")
    use_entry = e.get("symbol") == sym
    direction = a.direction or (e.get("direction") if use_entry else None) or "long"
    sp = moex.spec(sym, a.kind) if not a.offline else {"symbol": sym, "kind": a.kind or "shares", "minstep": a.minstep or 0.01, "lot": 1}
    entry = a.entry or (e.get("trigger_price") if use_entry else None) or sp.get("last")
    if not entry:
        C.die("Нет цены входа: укажите --entry")
    long_ = direction == "long"
    tf = a.tf or ("5m" if a.mode == "intraday" else "1d")
    if a.target == "none" and not a.trail:
        a.trail = cfg.get("trail_atr", 3.0)
    bars = moex.candles(sym, tf, end=C.now_msk(a.now), kind=a.kind, source=a.source, file=a.file)
    st = ta.suggest_stop(bars, direction, 1.5)
    lv = ta.levels(bars[-300:])
    # --- стоп
    rule = a.stop
    if rule in ("or-mid", "or-low"):
        if not use_entry or e.get("range_high") is None:
            C.die("Стоп %s нужен сработавший пробой диапазона открытия (entry.json)" % rule)
        stop = e["range_mid"] if rule == "or-mid" else (e["range_low"] if long_ else e["range_high"])
    elif rule.startswith("atr"):
        k = float(rule.split(":")[1]) if ":" in rule else 1.5
        stop = entry - k * st["atr"] if long_ else entry + k * st["atr"]
    elif rule == "swing":
        if not st["swing_stop"]:
            C.die("Не нашёл свинг-точку для стопа — используйте atr")
        buf = st["atr"] * 0.1
        stop = st["swing_stop"] - buf if long_ else st["swing_stop"] + buf
    elif rule.startswith("level:"):
        stop = float(rule.split(":")[1])
    elif rule.startswith("pct:"):
        p = float(rule.split(":")[1]) / 100
        stop = entry * (1 - p) if long_ else entry * (1 + p)
    else:
        C.die("Не понимаю стоп %r" % rule)
    step = sp.get("minstep") or 0.01
    stop = C.round_step(stop, step, "down" if long_ else "up")
    risk = (entry - stop) if long_ else (stop - entry)
    if risk <= 0:
        C.die("Стоп %s по неправильную сторону от входа %s" % (stop, entry))
    # --- цель
    t = a.target
    sup, res = ta.nearest_levels(lv, entry)
    if t == "none":
        target = None
    elif t.upper().endswith("R"):
        target = entry + float(t[:-1].replace(",", ".")) * risk * (1 if long_ else -1)
    elif t == "resistance":
        lvl = res if long_ else sup
        if not lvl:
            C.die("Нет уровня %s выше цены — задайте цель в R" % ("сопротивления" if long_ else "поддержки"))
        target = lvl["price"] - (st["atr"] * 0.1 if long_ else -st["atr"] * 0.1)
    elif t.startswith("level:"):
        target = float(t.split(":")[1])
    elif t.startswith("pct:"):
        p = float(t.split(":")[1]) / 100
        target = entry * (1 + p) if long_ else entry * (1 - p)
    else:
        C.die("Не понимаю цель %r" % t)
    if target is not None:
        target = C.round_step(target, step, "up" if long_ else "down")  # не портим заданное соотношение прибыль/риск
    reward = None if target is None else ((target - entry) if long_ else (entry - target))
    now = C.now_msk(a.now)
    plan = {
        "symbol": sp["symbol"], "kind": sp["kind"], "direction": direction, "mode": a.mode, "tf": tf,
        "setup": e.get("setup") if use_entry else (a.setup or "manual"),
        "entry": entry, "stop": stop, "target": target, "risk_per_unit": risk, "reward_per_unit": reward,
        "rr": (reward / risk) if (risk and reward is not None) else None, "stop_rule": rule, "target_rule": t,
        "trail": a.trail, "initial_stop": stop, "minstep": step,
        "atr": st["atr"], "date": now.strftime("%Y-%m-%d"), "created": C.fmt_dt(now),
        "entry_time": (e.get("trigger_time") if use_entry else None) or C.fmt_dt(now),
        "range_high": e.get("range_high") if use_entry else None, "range_low": e.get("range_low") if use_entry else None,
        "orb_start": e.get("start") if use_entry else None, "orb_range_min": e.get("range_min") if use_entry else None,
        "flat_by": cfg["flat_by"] if a.mode == "intraday" else None,
        "max_hold_days": (a.max_hold_days if a.mode == "swing" else
                          cfg.get("max_hold_days_position", 60) if a.mode == "position" else None),
        "nearest_resistance": res["price"] if res else None, "nearest_support": sup["price"] if sup else None,
    }
    return plan


def show(p):
    long_ = p["direction"] == "long"
    print("План выхода · %s · %s · %s" % (p["symbol"], "лонг" if long_ else "шорт",
                                         {"intraday": "внутри дня", "swing": "свинг", "position": "по тренду"}[p["mode"]]))
    print("  вход    %s" % C.px(p["entry"]))
    print("  стоп    %s  (%s, риск %s на единицу, %s)" % (C.px(p["stop"]), p["stop_rule"], C.px(p["risk_per_unit"]),
                                                       C.pct(p["risk_per_unit"] / p["entry"] * 100)))
    if p["target"] is not None:
        print("  цель    %s  (%s, прибыль/риск %s)" % (C.px(p["target"]), p["target_rule"], C.num(p["rr"], 2)))
    else:
        print("  цель    без фиксированной цели — держим, пока тренд жив")
    if p.get("trail"):
        print("  трейлинг стоп подтягивается за ценой: максимум с момента входа минус %s ATR (сейчас ATR %s ≈ %s)" % (
            C.num(p["trail"], 1), C.px(p["atr"]), C.px(p["trail"] * p["atr"])))
    if p["flat_by"]:
        print("  время   закрыть позицию до %s МСК в любом случае" % p["flat_by"])
    else:
        print("  время   держать не дольше %d торговых дней" % p["max_hold_days"])
    print("  ATR     %s — стоп = %s ATR" % (C.px(p["atr"]), C.num(p["risk_per_unit"] / p["atr"], 1) if p["atr"] else "—"))
    nr = p["nearest_resistance"] if long_ else p["nearest_support"]
    if nr and p["target"] is not None and ((long_ and nr < p["target"]) or (not long_ and nr > p["target"])):
        print("  ⚠ на пути к цели уровень %s %s — цена может развернуться раньше" % (
            "сопротивления" if long_ else "поддержки", C.px(nr)))
    cfg = C.config()
    if p["rr"] is not None and p["rr"] < cfg["min_reward_risk"]:
        print("  ⚠ прибыль/риск %s ниже вашего минимума %s — moex-risk такую сделку заблокирует" % (C.num(p["rr"], 2), cfg["min_reward_risk"]))


def cmd_update(a):
    """Подтянуть стоп открытой позиции: экстремум с момента входа минус K ATR, только в сторону прибыли."""
    import trend
    p = C.load_json(C.tpath("plan.json"))
    if not p:
        C.die("Нет trader/plan.json")
    k = a.trail or p.get("trail") or C.config().get("trail_atr", 3.0)
    long_ = p["direction"] == "long"
    bars = moex.candles(p["symbol"], p["tf"], end=C.now_msk(a.now), kind=p.get("kind"), source=a.source, file=a.file)
    since = p.get("filled_at") or p.get("entry_time") or p["created"]
    cmp = 10 if p["tf"] in ("1d", "1w", "1M") else 16   # дневной план: свеча дня входа тоже считается
    idx = next((i for i, b in enumerate(bars) if b["t"][:cmp] >= since[:cmp]), None)
    if idx is None:
        C.die("Нет свечей после входа (%s)" % since)
    new = trend.chandelier(bars, idx, p["entry"], long_, k)
    new = C.round_step(new, p.get("minstep") or 0.01, "down" if long_ else "up")
    old = p["stop"]
    better = (new > old) if long_ else (new < old)
    last = bars[-1]["c"]
    print("%s · цена %s · стоп был %s · трейлинг %s ATR даёт %s" % (p["symbol"], C.px(last), C.px(old), C.num(k, 1), C.px(new)))
    if better:
        p["stop"] = new
        p.setdefault("stop_history", []).append([C.fmt_dt(C.now_msk(a.now)), new])
        C.save_json(C.tpath("plan.json"), p)
        pf = C.load_json(C.tpath("portfolio.json"), {}) or {}
        pos = (pf.get("positions") or {}).get(p["symbol"])
        if pos and pos.get("lots"):
            pos["stop"] = new          # открытый риск портфеля считается от актуального стопа
            C.save_json(C.tpath("portfolio.json"), pf)
        locked = (new - p["entry"]) * (1 if long_ else -1)
        print("Стоп подтянут до %s%s. Передвиньте стоп у брокера: broker.py protect --index 1 --replace" % (
            C.px(new), (" — прибыль %s на акцию уже защищена" % C.px(locked)) if locked > 0 else ""))
    else:
        print("Стоп остаётся %s (стоп двигается только в сторону прибыли)" % C.px(old))
    if (long_ and last <= p["stop"]) or (not long_ and last >= p["stop"]):
        print("⚠ Цена у стопа или за ним — позицию пора закрывать: broker.py flatten --symbol %s" % p["symbol"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--symbol"); p.add_argument("--entry", type=float)
    p.add_argument("--direction", choices=["long", "short"])
    p.add_argument("--stop", default="or-mid"); p.add_argument("--target", default="1.5R", help="NR, resistance, level:X, pct:X или none")
    p.add_argument("--mode", default="intraday", choices=["intraday", "swing", "position"])
    p.add_argument("--trail", type=float, help="подтягиваемый стоп: K ATR от максимума с момента входа")
    p.add_argument("--max-hold-days", type=int, default=10)
    p.add_argument("--setup"); p.add_argument("--tf", choices=list(moex.TF))
    p.add_argument("--kind", choices=list(moex.KINDS)); p.add_argument("--source", choices=["auto", "iss", "tinvest"])
    p.add_argument("--file"); p.add_argument("--now"); p.add_argument("--offline", action="store_true")
    p.add_argument("--minstep", type=float)
    p.add_argument("--write", action="store_true", help="сохранить в trader/plan.json")
    u = sub.add_parser("update")
    u.add_argument("--trail", type=float); u.add_argument("--now"); u.add_argument("--file")
    u.add_argument("--source", choices=["auto", "iss", "tinvest"])
    a = ap.parse_args()
    try:
        if a.cmd == "update":
            return cmd_update(a)
        plan = build(a)
    except C.NetError as e:
        C.die(str(e))
    show(plan)
    if a.write:
        C.save_json(C.tpath("plan.json"), plan)
        print("\nЗаписано: %s" % C.tpath("plan.json"))


if __name__ == "__main__":
    main()
