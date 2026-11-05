#!/usr/bin/env python3
"""moex-broker: отправка одобренных заявок в Т-Инвестиции. Песочница по умолчанию, никогда без вашего «да».

  broker.py show                              заявки и их готовность
  broker.py approve --index 1 --reply "да"    записать ваше подтверждение (дословно)
  broker.py place --index 1                   отправить заявку (нужны PASS от moex-risk + «да»)
  broker.py protect --index 1                 выставить стоп-лосс и тейк-профит после исполнения
  broker.py status --index 1 | cancel --index 1
  broker.py flatten --symbol SBER             заявка на закрытие позиции (тоже через «да»)
  broker.py fill --symbol SBER --side buy --lots 6 --price 440.95    ручная запись исполнения (любой брокер)
  broker.py accounts | sandbox-open --amount 100000 | portfolio | log
"""
import argparse
import os
import re
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "moex-setup", "lib"))
import common as C  # noqa: E402
import costs  # noqa: E402

YES = {"да", "yes", "подтверждаю", "отправляй", "согласен", "согласна", "да отправляй", "да подтверждаю"}


def state():
    return C.load_json(C.tpath("state", "broker.json"), {"events": [], "approvals": {}}) or {"events": [], "approvals": {}}


def save_state(s):
    C.save_json(C.tpath("state", "broker.json"), s)


def log_event(s, ev, now):
    ev["at"] = C.fmt_dt(now)
    s["events"].append(ev)
    save_state(s)


def orders():
    return C.load_json(C.tpath("orders.json"), []) or []


def pick(a):
    os_ = orders()
    if not os_ or a.index < 1 or a.index > len(os_):
        C.die("Нет заявки №%s в trader/orders.json" % a.index)
    o = os_[a.index - 1]
    o["fingerprint"] = C.fingerprint(o)
    return o


def readiness(o, now, s):
    risk = (C.load_json(C.tpath("state", "risk.json"), {}) or {}).get("verdicts", {})
    v = risk.get(o["fingerprint"])
    d = now.strftime("%Y-%m-%d")
    risk_ok = o.get("closing") or (v and v["verdict"] == "PASS" and v["date"] == d)
    ap = s["approvals"].get(o["fingerprint"])
    yes_ok = ap and ap["at"].startswith(d)
    return risk_ok, yes_ok, v


def ticket(o, i, now, s):
    risk_ok, yes_ok, v = readiness(o, now, s)
    k = o["kind"]
    unit = "контракт(ов)" if k == "futures" else "облигац." if k == "bonds" else "лот(ов)"
    print("Заявка %d · %s" % (i, "ГОТОВА К ОТПРАВКЕ" if risk_ok and yes_ok else "НЕ ГОТОВА"))
    print("  %s %s %d %s%s, %s%s" % ("КУПИТЬ" if o["side"] == "buy" else "ПРОДАТЬ", o["symbol"], o["lots"], unit,
                                    (" (%d шт.)" % o["units"]) if k in ("shares", "etf") else "",
                                    "по рынку" if o["type"] == "market" else "лимит " + C.px(o["entry"]),
                                    "  [ЗАКРЫТИЕ ПОЗИЦИИ]" if o.get("closing") else ""))
    if not o.get("closing"):
        print("  стоп-лосс %s · %s · убыток на стопе ≈ %s · %s" % (
            C.px(o["stop"]), ("тейк-профит " + C.px(o["target"])) if o.get("target") is not None else
            "без цели, трейлинг %s ATR" % C.num(o.get("trail") or 3, 1), C.rub(o["max_loss"], 0),
            ("закрыть до %s" % o["flat_by"]) if o.get("flat_by") else ("держать до %s дн." % o.get("max_hold_days"))))
    cfg = C.config()
    print("  счёт: %s" % ("ПЕСОЧНИЦА (учебные деньги)" if cfg["paper"] else "РЕАЛЬНЫЙ СЧЁТ Т-Инвестиций"))
    print("  проверка рисков: %s · ваше «да»: %s" % (
        "закрытие, не требуется" if o.get("closing") else ("PASS" if risk_ok else ("BLOCK" if v else "не проводилась")),
        "получено" if yes_ok else "нет"))


def cmd_show(a):
    now = C.now_msk(a.now)
    s = state()
    for i, o in enumerate(orders(), 1):
        o["fingerprint"] = C.fingerprint(o)
        ticket(o, i, now, s)


def cmd_approve(a):
    now = C.now_msk(a.now)
    o = pick(a)
    reply = re.sub(r"[^\wа-яё ]", "", a.reply.lower(), flags=re.I).strip()
    s = state()
    if reply not in YES:
        log_event(s, {"type": "rejected", "fingerprint": o["fingerprint"], "symbol": o["symbol"], "reply": a.reply}, now)
        print("Ответ «%s» не является подтверждением. Заявка не одобрена." % a.reply)
        return
    s["approvals"][o["fingerprint"]] = {"reply": a.reply, "at": C.fmt_dt(now), "symbol": o["symbol"]}
    log_event(s, {"type": "approved", "fingerprint": o["fingerprint"], "symbol": o["symbol"], "reply": a.reply}, now)
    print("Подтверждение записано дословно: «%s» для заявки %d (%s %s %d)." % (a.reply, a.index, o["side"], o["symbol"], o["lots"]))


def account_id(a, sandbox):
    acc = a.account or C.config().get("tinvest_account_id")
    if acc:
        return acc
    import tinvest
    accs = tinvest.accounts(sandbox)
    if len(accs) == 1:
        return accs[0]["id"]
    C.die("Укажите счёт: --account ID или tinvest_account_id в config.json. Доступные: " +
          ", ".join("%s (%s)" % (x["id"], x.get("name", "")) for x in accs))


def cmd_place(a):
    import tinvest
    now = C.now_msk(a.now)
    cfg = C.config()
    o = pick(a)
    s = state()
    risk_ok, yes_ok, _ = readiness(o, now, s)
    if not risk_ok:
        C.die("Заявка не прошла moex-risk сегодня (или проверки не было). Отправка запрещена.")
    if not yes_ok:
        C.die("Нет вашего «да» для этой заявки. Отправка запрещена.")
    if any(e.get("type") == "placed" and e.get("fingerprint") == o["fingerprint"] for e in s["events"]):
        C.die("Эта заявка уже отправлялась. Повторно не отправляю.")
    sandbox = bool(cfg["paper"])
    acc = account_id(a, sandbox)
    ins = tinvest.instrument(o["symbol"], o["kind"])
    price = C.round_step(o["entry"], ins.get("minstep") or (o.get("spec") or {}).get("minstep") or 0.01)
    oid = str(uuid.uuid4())
    try:
        r = tinvest.post_order(acc, o["symbol"], o["side"], o["lots"], o["type"], price, o["kind"], sandbox, oid)
    except C.NetError as e:
        log_event(s, {"type": "failed", "fingerprint": o["fingerprint"], "symbol": o["symbol"], "error": str(e)[:300]}, now)
        C.die("Брокер отклонил заявку: %s" % e)
    log_event(s, {"type": "placed", "fingerprint": o["fingerprint"], "symbol": o["symbol"], "kind": o["kind"], "side": o["side"],
                  "lots": o["lots"], "price": price, "order_id": r.get("orderId", oid), "sandbox": sandbox,
                  "max_loss": o.get("max_loss", 0), "risk_rub": o.get("max_loss_at_stop"), "stop": o.get("stop"),
                  "target": o.get("target"), "setup": o.get("setup"), "closing": o.get("closing", False),
                  "status": r.get("executionReportStatus"), "account": acc}, now)
    print("Заявка отправлена (%s): %s, статус %s, исполнено лотов %s из %s" % (
        "песочница" if sandbox else "РЕАЛЬНЫЙ СЧЁТ", r.get("orderId", oid), r.get("executionReportStatus"),
        r.get("lotsExecuted", "?"), r.get("lotsRequested", o["lots"])))
    if not o.get("closing"):
        print("После исполнения выставьте защиту: broker.py protect --index %d" % a.index)


def last_placed(s, fp):
    ev = [e for e in s["events"] if e.get("type") == "placed" and e.get("fingerprint") == fp]
    if not ev:
        C.die("Эта заявка ещё не отправлялась")
    return ev[-1]


def cmd_status(a):
    import tinvest
    now = C.now_msk(a.now)
    o = pick(a)
    s = state()
    p = last_placed(s, o["fingerprint"])
    r = tinvest.order_state(p["account"], p["order_id"], p["sandbox"])
    done = int(r.get("lotsExecuted") or 0)
    avg = tinvest.q2f(r.get("averagePositionPrice")) or tinvest.q2f(r.get("executedOrderPrice"))
    print("%s: статус %s, исполнено %d из %s лотов, средняя цена %s" % (
        o["symbol"], r.get("executionReportStatus"), done, r.get("lotsRequested"), C.px(avg) if avg else "—"))
    prev = sum(e.get("lots", 0) for e in s["events"] if e.get("type") == "fill" and e.get("order_id") == p["order_id"])
    if done > prev and avg:
        record_fill(s, o["symbol"], o["kind"], o["side"], done - prev, avg, now, order=o, order_id=p["order_id"])


def cmd_protect(a):
    import tinvest
    now = C.now_msk(a.now)
    o = pick(a)
    s = state()
    p = last_placed(s, o["fingerprint"])
    r = tinvest.order_state(p["account"], p["order_id"], p["sandbox"])
    done = int(r.get("lotsExecuted") or 0)
    if done <= 0:
        C.die("Заявка ещё не исполнена — защищать нечего. Проверьте позже: broker.py status --index %d" % a.index)
    exit_side = "sell" if o["side"] == "buy" else "buy"
    stop = o["stop"]
    plan = C.load_json(C.tpath("plan.json"), {}) or {}
    if plan.get("symbol") == o["symbol"] and plan.get("stop"):
        better = plan["stop"] > stop if o["side"] == "buy" else plan["stop"] < stop
        if better:
            stop = plan["stop"]   # подтянутый трейлингом стоп
    if a.replace:
        for e in s["events"]:
            if e.get("type") == "stop_placed" and e.get("fingerprint") == o["fingerprint"] and not e.get("cancelled") \
                    and e.get("stop_type") == "stop_loss":
                try:
                    tinvest.cancel_stop(p["account"], e["stop_order_id"], p["sandbox"])
                    e["cancelled"] = C.fmt_dt(now)
                    print("Снят старый стоп %s" % C.px(e["price"]))
                except C.NetError as err:
                    print("Не удалось снять старый стоп %s: %s" % (e.get("stop_order_id"), err))
        save_state(s)
    todo = [("stop_loss", stop)]
    if o.get("target") is not None and not a.replace:
        todo.append(("take_profit", o["target"]))
    ok = []
    for typ, price in todo:
        try:
            rr = tinvest.post_stop(p["account"], o["symbol"], exit_side, done, price, typ, kind=o["kind"], sandbox=p["sandbox"])
            ok.append(typ)
            log_event(s, {"type": "stop_placed", "fingerprint": o["fingerprint"], "symbol": o["symbol"], "stop_type": typ,
                          "price": price, "lots": done, "stop_order_id": rr.get("stopOrderId")}, now)
            print("Выставлен %s по %s на %d лот(ов)" % ("стоп-лосс" if typ == "stop_loss" else "тейк-профит", C.px(price), done))
        except C.NetError as e:
            print("Не удалось выставить %s: %s" % (typ, e))
    if len(ok) < len(todo):
        print("⚠ Защита выставлена не полностью. Песочница может не поддерживать стоп-заявки — "
              "поставьте ценовые оповещения: moex-alerts alerts.py add --from-plan, и закрывайте вручную.")


def cmd_cancel(a):
    import tinvest
    now = C.now_msk(a.now)
    o = pick(a)
    s = state()
    p = last_placed(s, o["fingerprint"])
    tinvest.cancel_order(p["account"], p["order_id"], p["sandbox"])
    log_event(s, {"type": "cancelled", "fingerprint": o["fingerprint"], "symbol": o["symbol"], "order_id": p["order_id"]}, now)
    print("Заявка %s отменена" % p["order_id"])


def cmd_flatten(a):
    now = C.now_msk(a.now)
    pf = C.load_json(C.tpath("portfolio.json"), {}) or {}
    pos = (pf.get("positions") or {}).get(a.symbol)
    lots = a.lots or (pos and pos.get("lots"))
    if not lots:
        C.die("Не знаю размер позиции %s — укажите --lots" % a.symbol)
    side = "sell" if (pos or {}).get("side", "long") == "long" else "buy"
    o = {"symbol": a.symbol, "kind": (pos or {}).get("kind", a.kind or "shares"), "side": side, "direction": "flat",
         "lots": int(lots), "units": int(lots) * (pos or {}).get("lot_size", 1), "lot_size": (pos or {}).get("lot_size", 1),
         "type": "market", "entry": None, "stop": None, "target": None, "closing": True, "mode": "close",
         "date": now.strftime("%Y-%m-%d"), "time": C.fmt_dt(now)}
    o["fingerprint"] = C.fingerprint(o)
    C.save_json(C.tpath("orders.json"), [o])
    print("Заявка на закрытие: %s %s %d лот(ов) по рынку. Не забудьте снять стоп-заявки в приложении." % (
        "ПРОДАТЬ" if side == "sell" else "КУПИТЬ", a.symbol, int(lots)))
    print("Дальше: покажите тикет (show), получите «да» (approve) и отправьте (place --index 1).")


def record_fill(s, symbol, kind, side, lots, price, now, order=None, order_id=None, lot_size=None, fee=None):
    """Учёт исполнения: портфель, реализованный P&L, журнал сделок."""
    cfg = C.config()
    pf = C.load_json(C.tpath("portfolio.json"), {"cash": None, "positions": {}}) or {"positions": {}}
    pos = pf["positions"].get(symbol) or {"lots": 0, "avg": 0.0, "kind": kind,
                                          "lot_size": lot_size or (order or {}).get("lot_size", 1), "side": "long"}
    spec = dict((order or {}).get("spec") or {}, kind=kind, lot=pos["lot_size"])
    rpp = costs.rub_per_point(spec)
    fee = fee if fee is not None else (cfg["futures_fee_rub"] * lots if kind == "futures" else price * rpp * lots * cfg["fee_pct"] / 100)
    signed = lots if side == "buy" else -lots
    cur = pos["lots"] if pos.get("side", "long") == "long" else -pos["lots"]
    pnl = None
    if cur == 0 or (cur > 0) == (signed > 0):   # открытие / доливка
        new = cur + signed
        pos["avg"] = (abs(cur) * pos["avg"] + lots * price) / abs(new)
        if cur == 0:
            pos.update(opened=C.fmt_dt(now), setup=(order or {}).get("setup"), risk_rub=(order or {}).get("max_loss_at_stop"),
                       fees=0.0, stop=(order or {}).get("stop"), target=(order or {}).get("target"))
        pos["fees"] = pos.get("fees", 0) + fee
    else:                                        # закрытие (частичное)
        q = min(lots, abs(cur))
        direction = 1 if cur > 0 else -1
        pnl = (price - pos["avg"]) * direction * q * rpp - fee
        new = cur + signed
        pos["realized"] = pos.get("realized", 0) + pnl
        if new == 0:
            total = pos["realized"] - pos.get("fees", 0)
            j = C.load_json(C.tpath("journal.json"), []) or []
            risk = pos.get("risk_rub")
            j.append({"symbol": symbol, "kind": kind, "side": "long" if direction > 0 else "short", "setup": pos.get("setup"),
                      "opened": pos.get("opened"), "closed": C.fmt_dt(now), "entry": pos["avg"], "exit": price,
                      "lots": q, "pnl": round(total, 2), "risk_rub": risk,
                      "r": round(total / risk, 3) if risk else None})
            C.save_json(C.tpath("journal.json"), j)
            pos = {"lots": 0, "avg": 0.0, "kind": kind, "lot_size": pos["lot_size"], "side": "long"}
    pos["lots"] = abs(new)
    pos["side"] = "long" if new >= 0 else "short"
    pf["positions"][symbol] = pos
    if pf.get("cash") is not None:
        notional = price * rpp * lots
        pf["cash"] += (-notional if side == "buy" else notional) - fee if kind != "futures" else (pnl or 0) - (fee if pnl is None else 0)
    C.save_json(C.tpath("portfolio.json"), pf)
    log_event(s, {"type": "fill", "symbol": symbol, "side": side, "lots": lots, "price": price, "pnl": round(pnl, 2) if pnl is not None else None, "fee": round(fee, 2),
                  "order_id": order_id}, now)
    print("Исполнение записано: %s %s %d по %s%s" % (side, symbol, lots, C.px(price),
                                                    (", результат %s" % C.rub(pnl)) if pnl is not None else ""))


def cmd_fill(a):
    now = C.now_msk(a.now)
    s = state()
    os_ = [o for o in orders() if o["symbol"] == a.symbol]
    o = os_[0] if os_ else None
    record_fill(s, a.symbol, a.kind or (o or {}).get("kind", "shares"), a.side, a.lots, a.price, now, order=o,
                lot_size=a.lot_size or (o or {}).get("lot_size"), fee=a.fee)


def cmd_accounts(a):
    import tinvest
    cfg = C.config()
    sb = cfg["paper"] if a.sandbox is None else a.sandbox
    for x in tinvest.accounts(sb):
        print("%s  %s  %s  %s" % (x["id"], x.get("name", ""), x.get("type", ""), x.get("status", "")))
    print("(%s)" % ("песочница" if sb else "реальные счета"))


def cmd_sandbox_open(a):
    import tinvest
    acc = tinvest.open_sandbox()
    bal = tinvest.sandbox_payin(acc, a.amount)
    print("Открыт счёт песочницы %s, баланс %s" % (acc, C.rub(bal)))
    print("Пропишите его в trader/config.json: \"tinvest_account_id\": \"%s\"" % acc)


def cmd_portfolio(a):
    cfg = C.config()
    try:
        import tinvest
        if tinvest.has_token(cfg["paper"]):
            acc = account_id(a, cfg["paper"])
            p = tinvest.portfolio(acc, cfg["paper"])
            print("Портфель (%s): всего %s" % ("песочница" if cfg["paper"] else "реальный", C.rub(tinvest.q2f(p.get("totalAmountPortfolio")))))
            for x in p.get("positions", []):
                print("  %s %s × %s, средняя %s, сейчас %s" % (x.get("ticker") or x.get("figi"), x.get("instrumentType"),
                                                             C.num(tinvest.q2f(x.get("quantity")), 0),
                                                             C.px(tinvest.q2f(x.get("averagePositionPrice"))),
                                                             C.px(tinvest.q2f(x.get("currentPrice")))))
            return
    except C.NetError as e:
        print("Брокер недоступен (%s), показываю локальный учёт" % e)
    pf = C.load_json(C.tpath("portfolio.json"), {}) or {}
    print("Локальный учёт: деньги %s" % C.rub(pf.get("cash")))
    for sym, p in (pf.get("positions") or {}).items():
        if p.get("lots"):
            print("  %s %s %d лот(ов), средняя %s" % (sym, p.get("side"), p["lots"], C.px(p["avg"])))


def cmd_log(a):
    now = C.now_msk(a.now)
    d = now.strftime("%Y-%m-%d")
    for e in state()["events"]:
        if e["at"].startswith(d) or a.all:
            print("%s  %-11s %s %s" % (e["at"], e["type"], e.get("symbol", ""),
                                       ", ".join("%s=%s" % (k, v) for k, v in e.items() if k not in ("at", "type", "symbol", "fingerprint"))))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--now", help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show").set_defaults(fn=cmd_show)
    p = sub.add_parser("approve"); p.add_argument("--index", type=int, default=1); p.add_argument("--reply", required=True)
    p.set_defaults(fn=cmd_approve)
    for name, fn in (("place", cmd_place), ("status", cmd_status), ("protect", cmd_protect), ("cancel", cmd_cancel)):
        p = sub.add_parser(name); p.add_argument("--index", type=int, default=1); p.add_argument("--account")
        if name == "protect":
            p.add_argument("--replace", action="store_true", help="снять старый стоп и поставить подтянутый")
        p.set_defaults(fn=fn)
    p = sub.add_parser("flatten"); p.add_argument("--symbol", required=True); p.add_argument("--lots", type=int)
    p.add_argument("--kind"); p.set_defaults(fn=cmd_flatten)
    p = sub.add_parser("fill"); p.add_argument("--symbol", required=True); p.add_argument("--side", choices=["buy", "sell"], required=True)
    p.add_argument("--lots", type=int, required=True); p.add_argument("--price", type=float, required=True)
    p.add_argument("--kind"); p.add_argument("--lot-size", type=int); p.add_argument("--fee", type=float)
    p.set_defaults(fn=cmd_fill)
    p = sub.add_parser("accounts"); p.add_argument("--sandbox", type=lambda x: x.lower() in ("1", "true", "да"))
    p.set_defaults(fn=cmd_accounts)
    p = sub.add_parser("sandbox-open"); p.add_argument("--amount", type=float, default=100000); p.set_defaults(fn=cmd_sandbox_open)
    p = sub.add_parser("portfolio"); p.add_argument("--account"); p.set_defaults(fn=cmd_portfolio)
    p = sub.add_parser("log"); p.add_argument("--all", action="store_true"); p.set_defaults(fn=cmd_log)
    a = ap.parse_args()
    try:
        a.fn(a)
    except C.NetError as e:
        C.die(str(e))


if __name__ == "__main__":
    main()
