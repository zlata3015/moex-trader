#!/usr/bin/env python3
"""moex-journal: дневник сделок и статистика — что приносит деньги, а что пора выключить.

  journal.py stats                      итоги: доля прибыльных, среднее в R, профит-фактор, просадка
  journal.py stats --by setup           разрез по сетапам (symbol, weekday, hour, side)
  journal.py list --last 20
  journal.py add --symbol SBER --side long --entry 300 --exit 306 --lots 5 --risk 500 --setup orb   ручная запись
  journal.py note --index 3 --text "вошёл раньше сигнала"
"""
import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "moex-setup", "lib"))
import common as C  # noqa: E402
import sim  # noqa: E402

DAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def load():
    return C.load_json(C.tpath("journal.json"), []) or []


def key_of(t, by):
    if by == "setup":
        return t.get("setup") or "—"
    if by == "symbol":
        return t["symbol"]
    if by == "strategy":
        return C.STRATEGY_RU.get(t.get("strategy", "moderate"), "—")
    if by == "rank":
        rk = t.get("rank")
        return "%s, за %s, против %s, новость %+d" % (rk["tier"], rk["k"], rk.get("a", 0), rk["news"]) if rk else "без рейтинга"
    if by == "side":
        return t["side"]
    dt = C.parse_dt(t["opened"] or t["closed"])
    return DAYS[dt.weekday()] if by == "weekday" else "%02d:00" % dt.hour


def line(ts):
    rs = [t["r"] for t in ts if t.get("r") is not None]
    st = sim.stats(rs) if rs else {"trades": 0}
    pnl = sum(t["pnl"] for t in ts)
    return st, pnl


def cmd_stats(a):
    j = load()
    if a.since:
        j = [t for t in j if (t.get("closed") or "") >= a.since]
    if not j:
        print("Журнал пуст. Сделки попадают сюда автоматически, когда позиция закрыта (moex-broker fill/status).")
        return
    st, pnl = line(j)
    print("Сделок %d · результат %s" % (len(j), C.rub(pnl, 0)))
    if st.get("trades"):
        print("Прибыльных %s · среднее %s · профит-фактор %s · средняя прибыль %s / средний убыток %s · макс. просадка %s" % (
            C.pct(st["winrate"], 0), r(st["avg_r"]), C.num(st["profit_factor"], 2) if st["profit_factor"] else "∞",
            r(st["avg_win"]), r(st["avg_loss"]), r(st["max_dd_r"])))
    groups = {}
    for t in j:
        groups.setdefault(key_of(t, a.by), []).append(t)
    rows, advice = [], []
    for k, ts in sorted(groups.items(), key=lambda kv: -sum(t["pnl"] for t in kv[1])):
        s, p = line(ts)
        rows.append([k, len(ts), C.pct(s.get("winrate"), 0) if s.get("trades") else "—", r(s["avg_r"]) if s.get("trades") else "—",
                     C.rub(p, 0)])
        if s.get("trades", 0) >= 10 and s["avg_r"] < -0.1:
            advice.append("«%s»: %s за сделку на %d сделках — кандидат на отключение" % (k, r(s["avg_r"]), s["trades"]))
        if s.get("trades", 0) >= 10 and s["avg_r"] > 0.2:
            advice.append("«%s»: %s за сделку — здесь ваше преимущество" % (k, r(s["avg_r"])))
    print("\nРазрез по: %s" % {"setup": "сетапам", "symbol": "бумагам", "weekday": "дням недели", "hour": "часу входа", "side": "направлению", "rank": "рейтингу", "strategy": "стратегиям"}[a.by])
    print(C.table(rows, [a.by, "сделок", "прибыльных", "среднее", "результат"]))
    if advice:
        print("\n" + "\n".join("• " + x for x in advice))
    elif len(j) < 30:
        print("\nДо 30 сделок выводы делать рано — продолжайте вести журнал.")


def r(x):
    return ("+" if x >= 0 else "") + C.num(x, 2) + "R"


def cmd_list(a):
    j = load()
    rows = [[i, t["closed"], t["symbol"], t["side"], t.get("setup") or "", C.px(t["entry"]), C.px(t["exit"]),
             C.rub(t["pnl"], 0), r(t["r"]) if t.get("r") is not None else "—", (t.get("note") or "")[:30]]
            for i, t in list(enumerate(j, 1))[-a.last:]]
    print(C.table(rows, ["№", "закрыта", "тикер", "напр.", "сетап", "вход", "выход", "₽", "R", "заметка"]))


def cmd_add(a):
    j = load()
    now = C.fmt_dt(C.now_msk())
    mult = a.lot_size * (1 if a.side == "long" else -1)
    pnl = (a.exit - a.entry) * a.lots * mult - (a.fee or 0)
    j.append({"symbol": a.symbol, "kind": a.kind, "side": a.side, "setup": a.setup, "opened": a.opened or now,
              "closed": a.closed or now, "entry": a.entry, "exit": a.exit, "lots": a.lots, "pnl": round(pnl, 2),
              "risk_rub": a.risk, "r": round(pnl / a.risk, 3) if a.risk else None, "note": a.note})
    C.save_json(C.tpath("journal.json"), j)
    print("Записано: %s %s %s, результат %s" % (a.symbol, a.side, a.setup or "", C.rub(pnl)))


def cmd_note(a):
    j = load()
    j[a.index - 1]["note"] = a.text
    C.save_json(C.tpath("journal.json"), j)
    print("Заметка добавлена")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("stats"); p.add_argument("--by", default="setup", choices=["setup", "symbol", "weekday", "hour", "side", "rank", "strategy"])
    p.add_argument("--since"); p.set_defaults(fn=cmd_stats)
    p = sub.add_parser("list"); p.add_argument("--last", type=int, default=20); p.set_defaults(fn=cmd_list)
    p = sub.add_parser("add")
    for n in ("symbol", "setup", "note", "opened", "closed"):
        p.add_argument("--" + n)
    p.add_argument("--side", choices=["long", "short"], default="long"); p.add_argument("--kind", default="shares")
    p.add_argument("--entry", type=float, required=True); p.add_argument("--exit", type=float, required=True)
    p.add_argument("--lots", type=int, required=True); p.add_argument("--lot-size", type=float, default=1, help="шт. в лоте или ₽ за пункт для фьючерса")
    p.add_argument("--risk", type=float, help="₽ риска на сделку (до стопа)"); p.add_argument("--fee", type=float)
    p.set_defaults(fn=cmd_add)
    p = sub.add_parser("note"); p.add_argument("--index", type=int, required=True); p.add_argument("--text", required=True)
    p.set_defaults(fn=cmd_note)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
