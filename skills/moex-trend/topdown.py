#!/usr/bin/env python3
"""moex-trend: торговля по тренду сверху вниз — месяц, неделя, день задают направление, вход по сигналу в тренде,
выход по подтягиваемому стопу.

  topdown.py analyze SBER                    тренды М/Н/Д, сигнал, предложение сделки
  topdown.py analyze SBER --plan             + записать план (trader/plan.json) для moex-budget → moex-risk → moex-broker
  topdown.py scan                            все ликвидные акции: где тренды согласны и есть сигнал, с рейтингом
  topdown.py news-mark SBER -1 --title "..." --url URL --published 2026-11-05   новость о компании: 1 хорошая, 0, -1 плохая
  topdown.py analyze SBER --files-dir DIR    по файлам SBER_D.csv / SBER_M.csv (выгрузки ISS)
"""
import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "moex-setup", "lib"))
import common as C  # noqa: E402
import moex  # noqa: E402
import rank  # noqa: E402
import ta  # noqa: E402
import trend  # noqa: E402

ARROW = {"up": "▲ вверх", "down": "▼ вниз", "flat": "◆ неясно"}


def load(sym, a):
    if a.files_dir:
        daily = moex.load_file(os.path.join(a.files_dir, "%s_D.csv" % sym))
        mp = os.path.join(a.files_dir, "%s_M.csv" % sym)
        monthly = moex.load_file(mp) if os.path.exists(mp) else None
        px = sorted({x for b in daily[-60:] for x in (b["o"], b["h"], b["l"], b["c"])})
        step = min([round(y - x, 6) for x, y in zip(px, px[1:]) if y - x > 1e-9] or [0.01])
        spec = {"symbol": sym, "kind": "shares", "minstep": step, "name": sym}
    else:
        end = C.now_msk(a.now)
        daily = moex.candles(sym, "1d", days=500, end=end, source="iss")
        try:
            monthly = moex.candles(sym, "1M", days=3650, end=end, source="iss")
        except C.NetError:
            monthly = None
        spec = moex.spec(sym)
    if a.now:
        daily = [b for b in daily if b["t"][:10] <= a.now[:10]]
    return daily, monthly, spec


def analyze(sym, a):
    cfg = C.config()
    daily, monthly, spec = load(sym, a)
    td = trend.top_down(daily, monthly, require=trend.STOCK_REQUIRE if spec.get("kind", "shares") == "shares" else ("1w", "1d"))
    v = td["verdict"]
    d = v["direction"]
    allowed = d == "long" or (d == "short" and (cfg["allow_short"] or a.short))
    sig = trend.entry_signal(daily, d) if d else None
    atr_d = ta.atr([b["h"] for b in daily], [b["l"] for b in daily], [b["c"] for b in daily])[-1]
    last = daily[-1]
    res = {"symbol": sym, "date": last["t"][:10], "price": last["c"], "atr": atr_d, "states": {k: td[k] for k in ("1M", "1w", "1d")},
           "direction": d, "why": v["why"], "allowed": allowed, "signal": sig, "spec": spec}
    if d and allowed:
        long_ = d == "long"
        stop = last["c"] - a.stop_atr * atr_d if long_ else last["c"] + a.stop_atr * atr_d
        res["proposal"] = {"entry": last["c"], "stop": C.round_step(stop, spec.get("minstep") or 0.01, "down" if long_ else "up"),
                           "trail": a.trail_atr, "risk_pct": a.stop_atr * atr_d / last["c"] * 100}
    if sig:
        res["rank"] = rank.assess(daily, d, sym, C.now_msk(a.now))
    bt = (C.load_json(C.tpath("backtest", "summary.json"), {}) or {}).get("%s|trend" % sym)
    if bt:
        res["backtest"] = bt
    if not a.files_dir and getattr(a, "cmd", "") == "analyze" and d and spec.get("kind", "shares") == "shares":
        res["finance"] = finance_check(sym, a)
    return res


def finance_check(sym, a):
    """Финансы и дивиденды: только предупреждение, на вердикт не влияет."""
    try:
        f = moex.finance_warnings(moex.smartlab_finance(sym))
    except C.NetError:
        f = {"year": None, "warnings": ["отчётность получить не удалось, проверьте в moex-analyst"]}
    try:
        f["next_dividend"] = moex.next_dividend(sym, C.now_msk(a.now))
    except C.NetError:
        f["next_dividend"] = None
    return f


def show(r):
    print("%s · %s · цена %s · ATR(день) %s (%s)" % (r["symbol"], r["date"], C.px(r["price"]), C.px(r["atr"]),
                                                    C.pct(r["atr"] / r["price"] * 100)))
    for k, name in (("1M", "Месяц "), ("1w", "Неделя"), ("1d", "День  ")):
        st = r["states"][k]
        print("  %s %-9s %s" % (name, ARROW[st["dir"]], "; ".join(st["why"])))
    d = r["direction"]
    if not d:
        print("Итог: СДЕЛКИ НЕТ — %s. Ждём, пока неделя и день совпадут." % r["why"])
    elif not r["allowed"]:
        print("Итог: тренд ВНИЗ (%s). Шорты выключены — в покупку не входим, ждём разворота." % r["why"])
    else:
        print("Итог: работаем только в %s (%s)." % ("ПОКУПКУ" if d == "long" else "ПРОДАЖУ (шорт)", r["why"]))
        s = r["signal"]
        p = r["proposal"]
        if s:
            print("Сигнал сегодня: %s — %s %s" % ({"trend-pullback": "откат к EMA10 и разворот", "trend-breakout": "пробой 20-дневного экстремума"}[s["setup"]],
                                               "уровень", C.px(s["level"])))
            print("Подтверждение EMA20: %s (сигнал этим не отменяется)" % ("да, цена за EMA20" if s["confirm"] else "нет, цена по другую сторону EMA20"))
            rk = r.get("rank")
            if rk:
                print("Рейтинг на %s: %s · за %d из 3 (%s) · против %d · новость в сторону сделки %+d%s" % (
                    "покупку" if d == "long" else "продажу", rk["tier"], rk["k"],
                    ", ".join(k for k, v in rk["confirm"].items() if v) or "нет", rk["a"], rk["news"],
                    " — %s" % rk["news_title"] if rk["news_title"] else ""))
                if rk["tier"] != "высокий":
                    print("Сделку не открываем: нужен высокий рейтинг.")
            print("Предложение: вход около %s (завтра на открытии или сейчас), стоп %s (%s ATR, −%s), без цели — "
                  "подтягиваемый стоп %s ATR, держим, пока тренд жив" % (C.px(p["entry"]), C.px(p["stop"]), C.num(2, 0),
                                                                        C.pct(p["risk_pct"]), C.num(p["trail"], 1)))
        else:
            print("Сигнала сегодня нет: тренд есть, но точки входа нет (ждём отката к EMA10 или пробоя). Не догоняем цену.")
    f = r.get("finance")
    if f:
        print("Финансы и события (предупреждение, не запрет; решает пользователь)%s:" % (" за %s год" % f["year"] if f["year"] else ""))
        for w in f["warnings"] or ["явных тревожных признаков в годовой отчётности нет"]:
            print("  · " + w)
        nd = f.get("next_dividend")
        if nd:
            print("  · ближайшая дивидендная отсечка %s, %s на акцию" % (nd["date"], C.rub(nd["value"])))
        print("  · дату ближайшей отчётности проверьте в moex-news")
    bt = r.get("backtest")
    if bt:
        st = bt.get("oos") if bt.get("oos") and bt["oos"].get("trades", 0) >= 5 else bt["stats"]
        if st.get("trades"):
            print("Бэктест этой стратегии по %s: %d сделок, среднее %s%sR, прибыльных %s" % (
                r["symbol"], st["trades"], "+" if st["avg_r"] >= 0 else "", C.num(st["avg_r"], 2), C.pct(st["winrate"], 0)))


def write_plan(r, a):
    p = r["proposal"]
    now = C.now_msk(a.now)
    long_ = r["direction"] == "long"
    plan = {"symbol": r["symbol"], "kind": r["spec"].get("kind", "shares"), "direction": r["direction"], "mode": "position",
            "tf": "1d", "setup": (r["signal"] or {}).get("setup", "trend-manual"), "entry": p["entry"], "stop": p["stop"],
            "target": None, "risk_per_unit": abs(p["entry"] - p["stop"]), "reward_per_unit": None, "rr": None,
            "stop_rule": "atr:%s" % a.stop_atr, "target_rule": "none", "trail": p["trail"], "initial_stop": p["stop"],
            "minstep": r["spec"].get("minstep"), "atr": r["atr"], "confirm_ema20": (r["signal"] or {}).get("confirm"), "rank": r.get("rank"), "date": now.strftime("%Y-%m-%d"), "created": C.fmt_dt(now),
            "entry_time": C.fmt_dt(now), "flat_by": None, "max_hold_days": C.config().get("max_hold_days_position", 60),
            "nearest_resistance": None, "nearest_support": None,
            "trend": {k: r["states"][k]["dir"] for k in r["states"]}}
    C.save_json(C.tpath("plan.json"), plan)
    print("\nПлан записан: %s. Дальше: moex-budget → moex-risk → moex-broker." % C.tpath("plan.json"))


def cmd_analyze(a):
    r = analyze(a.symbol, a)
    show(r)
    if a.plan:
        if r.get("proposal") and (r["signal"] or a.force):
            write_plan(r, a)
        else:
            print("\nПлан не записан: %s" % ("нет сигнала входа (--force, чтобы всё равно)" if r.get("proposal") else "нет сделки по тренду"))


def cmd_scan(a):
    if a.files_dir:
        syms = sorted(os.path.basename(f)[:-6] for f in glob.glob(os.path.join(a.files_dir, "*_D.csv"))
                      if not os.path.basename(f).startswith("IMOEX"))
    else:
        import importlib.util
        sp = importlib.util.spec_from_file_location("scan", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "moex-scanner", "scan.py"))
        scan = importlib.util.module_from_spec(sp)
        sp.loader.exec_module(scan)
        syms = [u["symbol"] for u in scan.universe("shares", C.config()["min_turnover_rub"], a.limit)]
    rows, ready, ranked = [], [], []
    for s in syms:
        try:
            r = analyze(s, a)
        except (C.NetError, FileNotFoundError) as e:
            sys.stderr.write("%s: %s\n" % (s, e))
            continue
        if not r["atr"]:
            continue   # истории меньше, чем нужно для ATR: новая бумага, тренд не определить
        if r["atr"] / r["price"] < 0.004:
            continue   # фонды денежного рынка и облигационные фонды: почти без колебаний, это не тренд, а начисление процентов
        st = r["states"]
        verdict = ("ПОКУПКА" if r["direction"] == "long" else "ШОРТ" if r["direction"] == "short" and r["allowed"]
                   else "вниз (шорты выкл.)" if r["direction"] == "short" else "—")
        sig = {"trend-pullback": "откат", "trend-breakout": "пробой"}.get((r["signal"] or {}).get("setup"), "")
        conf = "" if not r["signal"] else "да" if r["signal"]["confirm"] else "нет"
        rows.append([s, ARROW[st["1M"]["dir"]], ARROW[st["1w"]["dir"]], ARROW[st["1d"]["dir"]], verdict, sig or "нет", conf, C.px(r["price"])])
        if sig and r["allowed"]:
            ready.append(s)
        if sig:
            ranked.append(dict(r["rank"], symbol=s, setup=sig, price=r["price"], allowed=r["allowed"]))
            rank.log_signal(ranked[-1], sig, r["price"], r["atr"], r["date"], r["allowed"])
    print(C.table(rows, ["тикер", "месяц", "неделя", "день", "направление", "сигнал", "EMA20", "цена"]))
    print("\nГотовы к входу по тренду: %s" % (", ".join(ready) if ready else "нет — это нормально, ждём сигналов"))
    held = (C.load_json(C.tpath("portfolio.json"), {}) or {}).get("positions") or {}
    for side, title in (("long", "Рейтинг на покупку"), ("short", "Рейтинг на продажу")):
        lst = sorted((x for x in ranked if x["direction"] == side), key=rank.sort_key)
        print("\n%s (сделка только с высоким, лимиты проверяет moex-risk):" % title)
        if side == "short" and not C.config()["allow_short"]:
            print("Шорт выключен: это учёт для рискованной стратегии, заявки не будет.")
        if lst:
            print(C.table([[i, x["symbol"], x["tier"], "%d из 3" % x["k"], x["a"], "%+d" % x["news"], x["setup"], C.px(x["price"])]
                           for i, x in enumerate(lst, 1)], ["№", "тикер", "рейтинг", "за", "против", "новость", "сигнал", "цена"]))
        else:
            print("сигналов нет")
    for x in ranked:
        if x["direction"] == "short" and x["tier"] == "высокий" and (held.get(x["symbol"]) or {}).get("lots"):
            print("⚠ %s в портфеле, а рейтинг на продажу высокий: разберите позицию. Стоп и правила выхода это не меняет." % x["symbol"])
    try:
        idx = moex.candles("IMOEX", "1d", days=150, kind="index", source="iss") if not a.files_dir else \
            moex.load_file(os.path.join(a.files_dir, "IMOEX_D.csv"))
        e50 = ta.ema([b["c"] for b in idx], 50)[-1]
        up = idx[-1]["c"] > e50
        print("Индекс Мосбиржи %s %s 50-дневной средней (%s) — %s" % (C.px(idx[-1]["c"]), "выше" if up else "ниже", C.px(e50),
              "покупки разрешены" if up else "фильтр рынка: новые покупки по умолчанию блокируются (regime_filter)"))
    except (C.NetError, FileNotFoundError, IndexError):
        pass


def cmd_news_mark(a):
    try:
        e = rank.add_mark(a.symbol, a.mark, a.title, a.url, a.published, C.now_msk(a.now), a.url2)
    except ValueError as err:
        C.die(str(err))
    print("Отметка записана: %s %+d, %s (%s). Действует 3 торговых дня." % (a.symbol, e["mark"], e["title"], e["published"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--files-dir"); p.add_argument("--now", help="'YYYY-MM-DD' — разбор на дату в прошлом")
        p.add_argument("--short", action="store_true", help="разрешить шорт для этого разбора")
        p.add_argument("--stop-atr", type=float, default=2.0)
        p.add_argument("--trail-atr", type=float, default=C.config().get("trail_atr", 3.0))
    p = sub.add_parser("analyze"); p.add_argument("symbol"); common(p)
    p.add_argument("--plan", action="store_true"); p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_analyze)
    p = sub.add_parser("scan"); common(p); p.add_argument("--limit", type=int, default=40); p.set_defaults(fn=cmd_scan)
    p = sub.add_parser("news-mark", help="отметка новости: 1 в сторону сделки, 0 нейтрально, -1 против")
    p.add_argument("symbol"); p.add_argument("mark", type=int); p.add_argument("--title", required=True)
    p.add_argument("--url", required=True); p.add_argument("--url2", help="второе издание, если первичного источника нет")
    p.add_argument("--published", required=True); p.add_argument("--now")
    p.set_defaults(fn=cmd_news_mark)
    a = ap.parse_args()
    try:
        a.fn(a)
    except C.NetError as e:
        C.die(str(e))


if __name__ == "__main__":
    main()
