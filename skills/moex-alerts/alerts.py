#!/usr/bin/env python3
"""moex-alerts: оповещения по цене, времени и индикаторам. Каждое срабатывает один раз.

  alerts.py add --from-plan                       стоп, цель и «закрыть до» из plan.json
  alerts.py add --symbol SBER --above 330
  alerts.py add --symbol SiZ6 --below 82000
  alerts.py add --symbol SBER --rsi-below 30 --tf 15m
  alerts.py add --at 18:30 --note "закрыть всё внутридневное"
  alerts.py check                                 проверить и прозвенеть то, что сработало
  alerts.py watch --every 60                      проверять в цикле
  alerts.py list | clear
"""
import argparse
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "moex-setup", "lib"))
import common as C  # noqa: E402
import moex  # noqa: E402
import ta  # noqa: E402


def load():
    return C.load_json(C.tpath("alerts.json"), []) or []


def save(x):
    C.save_json(C.tpath("alerts.json"), x)


def new(**kw):
    kw.setdefault("id", uuid.uuid4().hex[:6])
    kw.setdefault("fired", None)
    return kw


def cmd_add(a):
    al = load()
    now = C.now_msk(a.now)
    added = []
    if a.from_plan:
        p = C.load_json(C.tpath("plan.json"))
        if not p:
            C.die("Нет plan.json")
        long_ = p["direction"] == "long"
        grp = uuid.uuid4().hex[:6]
        added.append(new(symbol=p["symbol"], kind=p["kind"], op="below" if long_ else "above", price=p["stop"], note="СТОП", group=grp))
        if p.get("target") is not None:
            added.append(new(symbol=p["symbol"], kind=p["kind"], op="above" if long_ else "below", price=p["target"], note="ЦЕЛЬ", group=grp))
        if p.get("flat_by"):
            added.append(new(op="time", at=p["date"] + " " + p["flat_by"], note="закрыть позицию %s до конца дня" % p["symbol"]))
        if p.get("max_hold_days"):
            added.append(new(op="note", symbol=p["symbol"], note="максимальный срок удержания %d дн. с %s" % (p["max_hold_days"], p["date"])))
    if a.above is not None or a.below is not None:
        added.append(new(symbol=a.symbol, kind=a.kind, op="above" if a.above is not None else "below",
                         price=a.above if a.above is not None else a.below, note=a.note or ""))
    if a.rsi_below is not None or a.rsi_above is not None:
        added.append(new(symbol=a.symbol, kind=a.kind, op="rsi_below" if a.rsi_below is not None else "rsi_above",
                         value=a.rsi_below if a.rsi_below is not None else a.rsi_above, tf=a.tf, note=a.note or ""))
    if a.at:
        added.append(new(op="time", at=(now.strftime("%Y-%m-%d ") + a.at) if len(a.at) <= 5 else a.at, note=a.note or "время"))
    if not added:
        C.die("Нечего добавлять: укажите --from-plan, --above/--below, --rsi-below/--rsi-above или --at")
    al += added
    save(al)
    for x in added:
        print("+ " + describe(x))


def describe(x):
    if x["op"] in ("above", "below"):
        return "%s цена %s %s %s" % (x["symbol"], "≥" if x["op"] == "above" else "≤", C.px(x["price"]), x.get("note", ""))
    if x["op"].startswith("rsi"):
        return "%s RSI(%s) %s %s %s" % (x["symbol"], x["tf"], "<" if x["op"] == "rsi_below" else ">", x["value"], x.get("note", ""))
    if x["op"] == "time":
        return "в %s МСК — %s" % (x["at"], x["note"])
    return x.get("note", "")


def cmd_check(a, quiet=False):
    al = load()
    now = C.now_msk(a.now)
    prices, rang = {}, 0
    for x in al:
        if x["fired"] or x["op"] == "note":
            continue
        hit, info = False, ""
        try:
            if x["op"] == "time":
                hit = now >= C.parse_dt(x["at"])
            elif x["op"] in ("above", "below"):
                if x["symbol"] not in prices:
                    prices[x["symbol"]] = moex.last_price(x["symbol"], x.get("kind"))
                p, src = prices[x["symbol"]]
                hit = p is not None and (p >= x["price"] if x["op"] == "above" else p <= x["price"])
                info = "цена %s (%s)" % (C.px(p), "реальное время" if src == "tinvest" else "ISS, задержка ~15 мин")
            else:
                bars = moex.candles(x["symbol"], x["tf"], end=now, kind=x.get("kind"))
                r = ta.rsi([b["c"] for b in bars])[-1]
                hit = r is not None and (r < x["value"] if x["op"] == "rsi_below" else r > x["value"])
                info = "RSI %s" % C.num(r, 1)
        except C.NetError as e:
            print("! %s: %s" % (describe(x), e))
            continue
        if hit:
            x["fired"] = C.fmt_dt(now)
            rang += 1
            print("🔔 %s%s" % (describe(x), (" — " + info) if info else ""))
            if x.get("group"):  # стоп и цель — пара «одно отменяет другое»
                for y in al:
                    if y is not x and y.get("group") == x["group"] and not y["fired"]:
                        y["fired"] = "отменено: сработал " + x["note"]
    save(al)
    if not rang and not quiet:
        print("Ничего не сработало (%s МСК)" % now.strftime("%H:%M"))
    return rang


def cmd_watch(a):
    end = time.time() + a.max_minutes * 60
    while time.time() < end:
        cmd_check(a, quiet=True)
        if all(x["fired"] or x["op"] == "note" for x in load()):
            print("Все оповещения сработали")
            return
        time.sleep(a.every)


def cmd_list(a):
    for x in load():
        print("%s %s %s" % (x["id"], "✓ " + x["fired"] if x["fired"] else "·", describe(x)))


def cmd_clear(a):
    al = [x for x in load() if not x["fired"]] if a.fired else []
    save(al)
    print("Оповещения очищены" if not a.fired else "Удалены сработавшие")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--now", help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add")
    p.add_argument("--from-plan", action="store_true"); p.add_argument("--symbol"); p.add_argument("--kind")
    p.add_argument("--above", type=float); p.add_argument("--below", type=float)
    p.add_argument("--rsi-below", type=float); p.add_argument("--rsi-above", type=float); p.add_argument("--tf", default="15m")
    p.add_argument("--at"); p.add_argument("--note")
    p.set_defaults(fn=cmd_add)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    p = sub.add_parser("watch"); p.add_argument("--every", type=int, default=60); p.add_argument("--max-minutes", type=int, default=600)
    p.set_defaults(fn=cmd_watch)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    p = sub.add_parser("clear"); p.add_argument("--fired", action="store_true"); p.set_defaults(fn=cmd_clear)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
