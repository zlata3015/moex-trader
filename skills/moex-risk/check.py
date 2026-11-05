#!/usr/bin/env python3
"""moex-risk: проверяет каждую заявку против ваших лимитов. PASS или BLOCK с причиной.

  check.py                 все заявки из trader/orders.json
  check.py --now "2026-10-30 09:26"
"""
import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "moex-setup", "lib"))
import common as C  # noqa: E402
import moex  # noqa: E402


def today_stats(now):
    log = C.load_json(C.tpath("state", "broker.json"), {}) or {}
    d = now.strftime("%Y-%m-%d")
    ev = [e for e in log.get("events", []) if (e.get("at") or "").startswith(d)]
    placed = [e for e in ev if e.get("type") == "placed" and not e.get("closing")]
    realized = sum(e.get("pnl", 0) for e in ev if e.get("type") == "fill" and e.get("pnl") is not None)
    pf = C.load_json(C.tpath("portfolio.json"), {}) or {}
    open_pos = [s for s, p in (pf.get("positions") or {}).items() if p.get("lots")]
    filled = {e.get("order_id") for e in ev if e.get("type") == "fill"}
    open_risk = sum(e.get("max_loss", 0) for e in placed
                    if e["symbol"] in open_pos or (e.get("order_id") not in filled and e.get("type") != "cancelled"))
    return {"trades": len(placed), "realized": realized, "open_risk": open_risk, "open_positions": open_pos,
            "cash": pf.get("cash")}


def check(o, cfg, now, st, offline=False):
    res = []

    def add(ok, name, text, warn=False):
        res.append({"ok": ok, "warn": warn, "check": name, "text": text})

    if o.get("closing"):
        add(True, "закрытие", "заявка закрывает позицию — снижает риск, лимиты не применяются")
        return res
    long_ = o["direction"] == "long"
    account = cfg["account_size"]
    max_loss = C.money_limit(cfg["max_loss_per_trade"], account)
    k = o["kind"]
    add(o["lots"] >= 1, "размер", "%d лот(ов)" % o["lots"] if o["lots"] >= 1 else "0 лотов — нечего отправлять")
    tgt = o.get("target")
    if tgt is None:
        add(o.get("trail") is not None and ((long_ and o["stop"] < o["entry"]) or (not long_ and o["stop"] > o["entry"])),
            "стоп", "стоп %s, вход %s, цели нет — подтягиваемый стоп %s ATR" % (
                C.px(o["stop"]), C.px(o["entry"]), C.num(o.get("trail") or 0, 1)))
    else:
        add((long_ and o["stop"] < o["entry"] < tgt) or (not long_ and tgt < o["entry"] < o["stop"]),
            "стоп и цель", "стоп %s, вход %s, цель %s" % (C.px(o["stop"]), C.px(o["entry"]), C.px(tgt)))
    add(o["max_loss"] <= max_loss * 1.001, "убыток на сделку", "%s из лимита %s" % (C.rub(o["max_loss"], 0), C.rub(max_loss, 0)))
    dl = C.money_limit(cfg["daily_loss_limit"], account)
    worst = -min(0, st["realized"]) + st["open_risk"] + o["max_loss"]
    add(worst <= dl, "дневной лимит", "уже потеряно %s, открытый риск %s, эта сделка %s — итого %s из %s" % (
        C.rub(-min(0, st["realized"]), 0), C.rub(st["open_risk"], 0), C.rub(o["max_loss"], 0), C.rub(worst, 0), C.rub(dl, 0)))
    add(st["realized"] > -dl, "стоп на день", "дневной лимит убытка уже выбран — сегодня больше не торгуем"
        if st["realized"] <= -dl else "лимит не выбран")
    add(st["trades"] < cfg["max_trades_per_day"], "сделок за день", "%d из %d" % (st["trades"], cfg["max_trades_per_day"]))
    add(len(st["open_positions"]) < cfg["max_open_positions"] or o["symbol"] in st["open_positions"], "открытых позиций",
        "%d из %d" % (len(st["open_positions"]), cfg["max_open_positions"]))
    if tgt is not None:
        rr = o.get("rr") or 0
        add(rr >= cfg["min_reward_risk"], "прибыль/риск", "%s, минимум %s" % (C.num(rr, 2), C.num(cfg["min_reward_risk"], 1)))
    elif o.get("cost_share_of_risk") is not None:
        add(o["cost_share_of_risk"] <= 0.25, "комиссии", "издержки сделки — %s риска, максимум 25%%" % C.pct(o["cost_share_of_risk"] * 100, 0))
    tcm = o.get("target_cost_multiple")
    if tcm is not None:
        add(tcm >= cfg["min_target_cost_multiple"], "комиссии", "до цели %s издержек сделки, минимум %s" % (
            C.num(tcm, 1), cfg["min_target_cost_multiple"]))
    cap_lim = account * cfg["max_position_pct"] / 100
    add(o["capital"] <= cap_lim * 1.001, "размер позиции", "%s %s из %s (%d%% счёта)" % (
        "ГО" if k == "futures" else "сумма", C.rub(o["capital"], 0), C.rub(cap_lim, 0), cfg["max_position_pct"]))
    if st.get("cash") is not None:
        add(o["capital"] <= st["cash"], "деньги на счёте", "нужно %s, свободно %s" % (C.rub(o["capital"], 0), C.rub(st["cash"], 0)))
    if not long_:
        add(cfg["allow_short"], "шорт", "короткие продажи %s в конфиге" % ("разрешены" if cfg["allow_short"] else "выключены"))
    allow = {"futures": cfg["allow_futures"], "bonds": cfg["allow_bonds"], "corp": cfg["allow_bonds"], "currency": cfg["allow_currency"]}
    if k in allow:
        add(allow[k], "рынок", "%s %s" % (k, "разрешён" if allow[k] else "выключен в конфиге"))
    turn = (o.get("spec") or {}).get("turnover")
    if turn is not None and k in ("shares", "etf"):
        add(turn >= cfg["min_turnover_rub"], "ликвидность", "оборот %s, минимум %s" % (C.rub(turn, 0), C.rub(cfg["min_turnover_rub"], 0)))
    # время и сессия
    if o["mode"] in ("intraday", "position"):
        sess = C.session_of(k if k in cfg["sessions"] else "shares", now, cfg)
        ok_sess = sess == "main" or (sess == "evening" and cfg["allow_evening_session"]) or (sess == "morning" and cfg.get("allow_morning_session"))
        add(ok_sess, "сессия", "сейчас %s %s МСК — %s" % (now.strftime("%d.%m"), now.strftime("%H:%M"),
                                                         {"main": "основная сессия", "auction": "аукцион — не входим",
                                                          "clearing": "клиринг — торгов нет", "evening": "вечерняя сессия",
                                                          "morning": "утренняя сессия", "closed": "торги закрыты"}[sess]))
        if o["mode"] == "intraday":
            add(C.minutes_of(now) < C.hm(cfg["no_entry_after"]), "окно входа", "новые внутридневные входы до %s" % cfg["no_entry_after"])
        add(o["date"] == now.strftime("%Y-%m-%d"), "свежесть плана", "план от %s" % o["date"])
        add(now.weekday() < 5 or cfg.get("allow_weekend"), "день недели", "выходные торги %s" % (
            "разрешены" if cfg.get("allow_weekend") else "выключены (allow_weekend)"))
    # фьючерсы: лимиты цены и экспирация
    sp = o.get("spec") or {}
    if k == "futures":
        hl, ll = sp.get("high_limit"), sp.get("low_limit")
        if hl and ll:
            add(ll <= min(o["stop"], o["target"]) and max(o["stop"], o["target"]) <= hl, "ценовые лимиты",
                "коридор биржи %s – %s" % (C.px(ll), C.px(hl)))
        ltd = sp.get("last_trade_date")
        if ltd:
            days = (datetime.strptime(ltd, "%Y-%m-%d") - now).days
            add(days >= 2, "экспирация", "до экспирации %d дн." % days)
    # дивидендный гэп для свинга
    if o["mode"] == "swing" and k in ("shares",) and long_ and not offline:
        try:
            nd = moex.next_dividend(o["symbol"], now)
        except C.NetError:
            nd = None
        if nd and nd.get("date"):
            days = (datetime.strptime(nd["date"], "%Y-%m-%d") - now).days
            hold = (o.get("max_hold_days") or 10) * 1.5
            if days <= hold:
                gap = nd["value"] / o["entry"] * 100 if nd.get("value") else None
                add(not cfg["block_dividend_gap"], "дивидендный гэп",
                    "отсечка %s (%s, ≈%s): цена упадёт на размер дивиденда" % (nd["date"], C.rub(nd.get("value")), C.pct(gap)),
                    warn=not cfg["block_dividend_gap"])
    # тренд сверху вниз: месяц, неделя, день
    if cfg.get("require_trend_alignment") and k in ("shares", "etf", "futures", "currency") and not offline:
        try:
            import trend
            daily = moex.candles(o["symbol"], "1d", days=500, end=now, kind=k, source="iss")
            daily = [b for b in daily if b["t"][:10] < now.strftime("%Y-%m-%d")] if o["mode"] != "position" else daily
            try:
                monthly = moex.candles(o["symbol"], "1M", days=3650, end=now, kind=k, source="iss")
            except C.NetError:
                monthly = None
            td = trend.top_down(daily, monthly)
            want = o["direction"]
            got = td["verdict"]["direction"]
            add(got == want, "тренд", "месяц %s, неделя %s, день %s — %s" % (
                trend.DIR_RU[td["1M"]["dir"]], trend.DIR_RU[td["1w"]["dir"]], trend.DIR_RU[td["1d"]["dir"]],
                "сделка по тренду" if got == want else ("сделка ПРОТИВ тренда" if got else td["verdict"]["why"])))
        except (C.NetError, KeyError, IndexError):
            pass
    # бэктест
    bt = C.load_json(C.tpath("backtest", "summary.json"), {}) or {}
    key = "%s|%s" % (o["symbol"], "orb" if o.get("setup") == "orb" else
                     "trend" if str(o.get("setup", "")).startswith("trend") else "swing:%s" % o.get("setup"))
    if key in bt:
        s = bt[key]
        ref = s["oos"] if s.get("oos") and s["oos"].get("trades", 0) >= 10 else s["stats"]
        n = ref.get("trades", 0)
        good = ref.get("avg_r", 0) > 0 and n >= 20
        add(ref.get("avg_r", 0) > 0 or not cfg["require_backtest"], "бэктест", "сетап на истории: %s за сделку (%d сделок%s)" % (
            ("+" if ref.get("avg_r", 0) >= 0 else "") + C.num(ref.get("avg_r", 0), 2) + "R", n,
            ", мало для вывода" if n < 20 else ""), warn=not good)
    elif cfg["require_backtest"]:
        add(False, "бэктест", "нет бэктеста этого сетапа по %s (require_backtest включён)" % o["symbol"])
    # режим рынка
    if cfg["regime_filter"] and long_ and k in ("shares", "etf") and not offline:
        try:
            import ta
            idx = moex.candles("IMOEX", "1d", days=150, end=now, kind="index", source="iss")
            c = [b["c"] for b in idx if b["t"][:10] < now.strftime("%Y-%m-%d")]
            e50 = ta.ema(c, 50)[-1]
            up = c[-1] > e50
            add(up or o["mode"] == "intraday", "режим рынка", "IMOEX %s EMA50 дневной (%s vs %s)" % (
                "выше" if up else "ниже", C.px(c[-1]), C.px(e50)), warn=not up)
        except (C.NetError, IndexError, TypeError):
            pass
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--now"); ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    cfg = C.config()
    now = C.now_msk(a.now)
    orders = C.load_json(C.tpath("orders.json"), []) or []
    if not orders:
        C.die("Нет заявок в trader/orders.json — сначала moex-budget size.py --write")
    st = today_stats(now)
    state = C.load_json(C.tpath("state", "risk.json"), {}) or {}
    verdicts = state.get("verdicts", {})
    for i, o in enumerate(orders, 1):
        o["fingerprint"] = C.fingerprint(o)
        res = check(o, cfg, now, st, a.offline)
        fails = [r for r in res if not r["ok"]]
        verdict = "PASS" if not fails else "BLOCK"
        print("Заявка %d · %s %s %d · %s" % (i, "КУПИТЬ" if o["side"] == "buy" else "ПРОДАТЬ", o["symbol"], o["lots"],
                                           "✅ PASS" if verdict == "PASS" else "⛔ BLOCK"))
        for r in res:
            mark = "✓" if r["ok"] and not r["warn"] else ("⚠" if r["ok"] else "✗")
            print("  %s %-18s %s" % (mark, r["check"], r["text"]))
        if fails:
            print("  Причина: " + "; ".join(r["check"] for r in fails))
        verdicts[o["fingerprint"]] = {"verdict": verdict, "at": C.fmt_dt(now), "date": now.strftime("%Y-%m-%d"),
                                      "symbol": o["symbol"], "fails": [r["check"] for r in fails]}
    C.save_json(C.tpath("orders.json"), orders)
    C.save_json(C.tpath("state", "risk.json"), {"verdicts": verdicts})


if __name__ == "__main__":
    main()
