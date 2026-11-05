#!/usr/bin/env python3
"""moex-timemachine: проигрывает сделку из plan.json 10 000 раз до того, как вы рискнёте рублём.

  timemachine.py run --write              бутстрэп по барам самой бумаги + все прошлые дни, когда срабатывал тот же сетап
  timemachine.py run --sims 20000 --seed 7
"""
import argparse
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "moex-setup", "lib"))
import common as C  # noqa: E402
import costs  # noqa: E402
import moex  # noqa: E402
import sim  # noqa: E402


def run(a):
    cfg = C.config()
    p = C.load_json(C.tpath("plan.json"))
    if not p:
        C.die("Нет trader/plan.json — сначала moex-exit plan --write")
    long_ = p["direction"] == "long"
    risk = p["risk_per_unit"]
    spec = {"kind": p["kind"], "minstep": a.minstep}
    if not a.offline:
        try:
            spec = moex.spec(p["symbol"], p["kind"])
        except C.NetError:
            pass
    cost_r = costs.in_r(spec, p["entry"], risk, cfg)
    end = C.parse_dt(p["date"] + " 23:59")
    out = {"symbol": p["symbol"], "mode": p["mode"], "sims": a.sims, "seed": a.seed, "cost_r": cost_r}
    if p["mode"] == "intraday":
        bars = moex.candles(p["symbol"], "5m", days=a.days, end=end, kind=p["kind"], source=a.source, file=a.file)
        days = moex.sessions_split(bars)
        hist = [days[d] for d in sorted(days) if d < p["date"]][-a.sessions:]
        em = C.minutes_of(C.parse_dt(p["entry_time"]))
        fm = C.hm(p["flat_by"])
        res, rs = sim.bootstrap_intraday(hist, em, fm, p["entry"], p["stop"], p["target"], long_, a.sims, a.seed)
        out.update(history_sessions=len(hist))
        # та же сделка на прошлых днях
        hb = [b for d in hist for b in d]
        if p.get("setup") == "orb" and p.get("range_high") is not None:
            sr = p["stop_rule"] if p["stop_rule"] in ("or-mid", "or-low") else "atr:1.5"
            tr = sim.backtest_orb(hb, spec, cfg, 5, p.get("orb_start") or cfg["orb_start"], p.get("orb_range_min") or 15,
                                  sr, p["rr"], p["flat_by"], cfg["no_entry_after"], allow_short=not long_)
            rule = "тот же пробой диапазона открытия (стоп %s, цель %sR)" % (sr, C.num(p["rr"], 1))
        else:
            tr = []
            sp, tp = risk / p["entry"], p["reward_per_unit"] / p["entry"]
            for d in hist:
                after = [b for b in d if C.minutes_of(C.parse_dt(b["t"])) > em]
                at = [b for b in d if C.minutes_of(C.parse_dt(b["t"])) <= em]
                if not after or not at:
                    continue
                e = at[-1]["c"]
                st, tg = (e * (1 - sp), e * (1 + tp)) if long_ else (e * (1 + sp), e * (1 - tp))
                o, px, _ = sim.run_trade(after, e, st, tg, long_, flat_by=fm)
                tr.append({"date": after[0]["t"][:10], "outcome": o,
                           "r": sim.r_multiple(e, px, abs(e - st), long_, cost_r)})
            rule = "вход в то же время дня с теми же расстояниями до стопа и цели"
    else:
        daily = moex.candles(p["symbol"], "1d", days=900, end=end, kind=p["kind"], source="iss", file=a.file)
        daily = [b for b in daily if b["t"][:10] <= p["date"]]
        res, rs = sim.bootstrap_daily(daily, p["max_hold_days"], p["entry"], p["stop"], p["target"], long_, a.sims, a.seed)
        out.update(history_days=len(daily))
        if p.get("setup") in sim.S.DETECTORS:
            tr = sim.backtest_swing(daily, spec, cfg, p["setup"], (risk / p["atr"]) if p.get("atr") else 2.0,
                                    p["rr"], p["max_hold_days"])
            rule = "тот же сетап «%s» на дневках" % sim.S.SETUPS[p["setup"]]
        else:
            tr, rule = [], None
    n = float(a.sims)
    exp_r = sum(rs) / len(rs) - cost_r
    out.update({"target_first": res["цель"] / n * 100, "stop_first": res["стоп"] / n * 100, "open_at_end": res["время"] / n * 100,
                "expected_r": exp_r, "history_rule": rule, "history": sim.stats([t["r"] for t in tr]),
                "history_counts": {k: sum(1 for t in tr if t["outcome"] == k) for k in ("цель", "стоп", "время")}})
    h = out["history"]
    hn, ha = h.get("trades", 0), h.get("avg_r", 0)
    if hn >= 10 and (exp_r > 0.05) != (ha > 0.05) and abs(exp_r - ha) > 0.1:
        out["verdict"] = ("сигналы расходятся: случайная модель даёт %s, реальная история сетапа %s. "
                          "При 30+ исторических сделках больше доверяйте истории" % (_r(exp_r), _r(ha)))
    elif exp_r > 0.05 and (hn < 10 or ha > 0.05):
        out["verdict"] = "перевес на вашей стороне — но это не обещание"
    elif exp_r < -0.05 and (hn < 10 or ha < 0):
        out["verdict"] = "против вас: в среднем сделка теряет после издержек"
    else:
        out["verdict"] = "близко к монетке: преимущества не видно"
    return p, out


def _r(x):
    return ("+" if x >= 0 else "") + C.num(x, 2) + "R"


def show(p, o):
    print("Машина времени · %s · %s · вход %s, стоп %s, цель %s" % (p["symbol"], "лонг" if p["direction"] == "long" else "шорт",
                                                                  C.px(p["entry"]), C.px(p["stop"]), C.px(p["target"])))
    if p["mode"] == "intraday":
        print("Модель: %d сессий, каждый 5-минутный бар берётся из того же времени дня случайного прошлого дня (из %d), seed %d"
              % (o["sims"], o["history_sessions"], o["seed"]))
    else:
        print("Модель: %d сценариев на %d торговых дней из дневных баров (%d дней истории), seed %d"
              % (o["sims"], p["max_hold_days"], o["history_days"], o["seed"]))
    print("  цель раньше стопа   %s" % C.pct(o["target_first"], 1))
    print("  стоп раньше цели    %s" % C.pct(o["stop_first"], 1))
    print("  ни то ни другое     %s (закрытие по времени)" % C.pct(o["open_at_end"], 1))
    print("  в среднем на сделку  %s после издержек (издержки %s)" % (_r(o["expected_r"]), C.num(o["cost_r"], 2) + "R"))
    if o.get("history_rule"):
        h, c = o["history"], o["history_counts"]
        if h.get("trades"):
            print("История: %s — %d сделок: цель %d, стоп %d, по времени %d; доля прибыльных %s, среднее %s, профит-фактор %s"
                  % (o["history_rule"], h["trades"], c["цель"], c["стоп"], c["время"], C.pct(h["winrate"], 0), _r(h["avg_r"]),
                     C.num(h["profit_factor"], 2) if h["profit_factor"] else "—"))
        else:
            print("История: %s — ни одного срабатывания" % o["history_rule"])
    print("Вывод: " + o["verdict"])
    print("Это симуляция по прошлым барам, не прогноз: отчётность, новости, санкции и гэпы могут сделать то, чего в истории не было.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("--sims", type=int, default=10000); p.add_argument("--seed", type=int, default=19)
    p.add_argument("--sessions", type=int, default=59); p.add_argument("--days", type=int, default=95)
    p.add_argument("--source", choices=["auto", "iss", "tinvest"]); p.add_argument("--file")
    p.add_argument("--offline", action="store_true"); p.add_argument("--minstep", type=float, default=0.01)
    p.add_argument("--write", action="store_true")
    a = ap.parse_args()
    try:
        plan, o = run(a)
    except C.NetError as e:
        C.die(str(e))
    show(plan, o)
    if a.write:
        C.save_json(C.tpath("state", "timemachine.json"), dict(o, plan=plan))


if __name__ == "__main__":
    main()
