#!/usr/bin/env python3
"""moex-risk: проверяет каждую заявку против ваших лимитов. PASS или BLOCK с причиной.

  check.py                 все заявки из trader/orders.json
  check.py --now "2026-10-30 09:26"
  check.py --status        сводка рисков портфеля: капитал, просадка, открытый риск по позициям, лимиты
"""
import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "moex-setup", "lib"))
import common as C  # noqa: E402
import moex  # noqa: E402


def today_stats(now, cfg=None, offline=False):
    import riskbook
    return riskbook.snapshot(cfg or C.config(), now, offline)


def check(o, cfg, now, st, offline=False):
    res = []

    def add(ok, name, text, warn=False):
        res.append({"ok": ok, "warn": warn, "check": name, "text": text})

    if o.get("closing"):
        add(True, "закрытие", "заявка закрывает позицию — снижает риск, лимиты не применяются")
        return res
    add(o.get("strategy", "moderate") == cfg.get("strategy", "moderate"), "стратегия месяца", "заявка: %s, месяц: %s" % (
        C.STRATEGY_RU.get(o.get("strategy", "moderate")), C.STRATEGY_RU.get(cfg.get("strategy", "moderate"))))
    why = C.halted()
    add(why is None, "аварийная остановка", "новые заявки запрещены: %s" % why if why else "не включена")
    if not cfg["paper"]:
        if offline:
            add(False, "офлайн", "реальная заявка без проверки данных запрещена")
        for key, name in (("regime_filter", "режим рынка"), ("require_trend_alignment", "тренд"), ("require_backtest", "бэктест"),
                          ("require_high_rank", "рейтинг")):
            if not cfg.get(key):
                add(False, "настройки", "для реальной заявки нельзя выключать проверку: %s (%s)" % (name, key))
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
    import riskbook
    # просадка от максимума: −7% — объём вдвое, −10% — пауза
    mult = st.get("dd_mult", 1.0)
    dd = st.get("drawdown", 0.0)
    add(mult > 0, "просадка", "капитал %s, максимум %s, просадка %s — %s" % (
        C.rub(st.get("equity", account), 0), C.rub(st.get("peak", account), 0), C.pct(dd * 100, 1),
        "норма" if mult == 1 else "объём вдвое меньше" if mult == 0.5 else "ПАУЗА: торговля остановлена до разбора"),
        warn=(mult == 0.5))
    limit = max_loss * mult
    add(o["max_loss"] <= limit * 1.001, "убыток на сделку", "%s из лимита %s%s" % (
        C.rub(o["max_loss"], 0), C.rub(limit, 0), " (урезан из-за просадки)" if mult < 1 else ""))
    dl = C.money_limit(cfg["daily_loss_limit"], account)
    wl = C.money_limit(cfg.get("weekly_loss_limit", "4%"), account)
    lost_d, lost_w = -min(0, st["realized_today"]), -min(0, st["realized_week"])
    add(lost_d < dl, "стоп на день", "сегодня потеряно %s из %s%s" % (
        C.rub(lost_d, 0), C.rub(dl, 0), " — сегодня больше не торгуем" if lost_d >= dl else ""))
    add(lost_d + o["max_loss"] <= dl * 1.001, "дневной запас", "потеряно сегодня %s + риск сделки %s = %s из %s" % (
        C.rub(lost_d, 0), C.rub(o["max_loss"], 0), C.rub(lost_d + o["max_loss"], 0), C.rub(dl, 0)))
    add(lost_w < wl, "стоп на неделю", "за неделю потеряно %s из %s%s" % (
        C.rub(lost_w, 0), C.rub(wl, 0), " — до понедельника не торгуем" if lost_w >= wl else ""))
    mo = C.money_limit(cfg.get("max_open_risk", "3%"), account)
    add(st["open_risk"] + o["max_loss"] <= mo * 1.001, "открытый риск", "позиции и заявки %s + эта сделка %s = %s из %s" % (
        C.rub(st["open_risk"], 0), C.rub(o["max_loss"], 0), C.rub(st["open_risk"] + o["max_loss"], 0), C.rub(mo, 0)))
    if st.get("no_stop"):
        add(False, "позиции без стопа", "нет стопа у %s — сначала поставьте стоп (moex-broker protect)" % ", ".join(st["no_stop"]))
    add(st["trades_today"] < cfg["max_trades_per_day"], "сделок за день", "%d из %d" % (st["trades_today"], cfg["max_trades_per_day"]))
    pos = st.get("positions", {})
    # усреднение убытка запрещено
    if o["symbol"] in pos:
        p = pos[o["symbol"]]
        same = (p.get("side", "long") == "long") == long_
        losing = (o["entry"] < p["avg"]) if long_ else (o["entry"] > p["avg"])
        if same:
            add(not losing, "усреднение", "уже есть позиция по %s, средняя %s — %s" % (
                o["symbol"], C.px(p["avg"]), "докупка в убытке ЗАПРЕЩЕНА" if losing else "добавление в прибыли"))
    # коррелирующие позиции считаем как одну
    try:
        group = riskbook.correlated_group(o["symbol"], [s for s in pos if s != o["symbol"]], cfg, offline, now)
    except C.NetError as e:
        group = []
        add(False, "корреляция", "проверка не выполнена: %s" % str(e)[:80])
    if group:
        g_risk = sum(st["position_risk"].get(s) or 0 for s, _ in group)
        g_lim = limit * float(cfg.get("corr_group_risk_mult", 1.0))
        add(g_risk + o["max_loss"] <= g_lim * 1.001, "корреляция", "связана с %s; общий риск группы %s из %s" % (
            ", ".join("%s (%s)" % (s, why) for s, why in group), C.rub(g_risk + o["max_loss"], 0), C.rub(g_lim, 0)))
    linked = {s for s, _ in group}
    n_groups = len({riskbook.sector(s) or s for s in pos if s not in linked and s != o["symbol"]})
    add(n_groups < cfg["max_open_positions"] or o["symbol"] in pos or bool(group), "открытых позиций",
        "%d независимых из %d (связанные бумаги считаются одной)" % (n_groups, cfg["max_open_positions"]))
    if cfg.get("strategy", "moderate") == "risky":
        others = [s for s in pos if s != o["symbol"]]
        add(len(others) < cfg["max_open_positions"], "одна позиция", "открыты: %s" % (", ".join(others) or "нет"))
    # лимит на корпоративного эмитента (облигации)
    if k in ("bonds", "corp") and not str(o["symbol"]).startswith("SU"):
        issuer = (o.get("spec") or {}).get("issuer")
        if issuer:
            same_iss = sum(abs(p.get("avg", 0)) * p["lots"] * (p.get("facevalue") or 1000) / 100
                           for s, p in pos.items() if p.get("issuer") == issuer)
            lim_iss = account * float(cfg.get("issuer_limit_pct", 5)) / 100
            add(same_iss + o["capital"] <= lim_iss * 1.001, "эмитент", "%s: уже %s + эта покупка %s = %s из %s (%s%% счёта)" % (
                (o.get("spec") or {}).get("issuer_name") or issuer, C.rub(same_iss, 0), C.rub(o["capital"], 0),
                C.rub(same_iss + o["capital"], 0), C.rub(lim_iss, 0), cfg.get("issuer_limit_pct", 5)))
        else:
            add(True, "эмитент", "не удалось определить эмитента — проверьте лимит 5%% вручную", warn=True)
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
        add(cfg["paper"], "шорт и этап", "шорт только в песочнице до этапа 5" if not cfg["paper"] else "песочница")
    allow = {"futures": cfg["allow_futures"], "bonds": cfg["allow_bonds"], "corp": cfg["allow_bonds"], "currency": cfg["allow_currency"]}
    if k in allow:
        add(allow[k], "рынок", "%s %s" % (k, "разрешён" if allow[k] else "выключен в конфиге"))
    turn = (o.get("spec") or {}).get("turnover")
    if turn is not None and k in ("shares", "etf"):
        add(turn >= cfg["min_turnover_rub"], "ликвидность", "оборот %s, минимум %s" % (C.rub(turn, 0), C.rub(cfg["min_turnover_rub"], 0)))
    if cfg.get("max_notional_mult") and k == "futures":
        import costs
        full = o["entry"] * costs.rub_per_point(dict(o.get("spec") or {}, kind=k)) * o["lots"]
        add(full <= account * cfg["max_notional_mult"] * 1.001, "полная стоимость", "%s из %s" % (
            C.rub(full, 0), C.rub(account * cfg["max_notional_mult"], 0)))
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
        add(cfg["paper"], "фьючерсы", "только песочница до прохождения этапа 4" if not cfg["paper"] else "песочница")
        hl, ll = sp.get("high_limit"), sp.get("low_limit")
        if hl and ll:
            lv = [x for x in (o["stop"], o.get("target")) if x is not None]
            add(ll <= min(lv) and max(lv) <= hl, "ценовые лимиты", "коридор биржи %s – %s" % (C.px(ll), C.px(hl)))
        ltd = sp.get("last_trade_date")
        if ltd:
            days = (datetime.strptime(ltd, "%Y-%m-%d") - now).days
            add(days >= 2, "экспирация", "до экспирации %d дн." % days)
    # свежесть и качество свечей: на устаревших данных новая сделка не открывается
    if not offline and k != "bonds" and k != "corp":
        try:
            import dataqc
            bars = moex.candles(o["symbol"], "1d", days=120, end=now, kind=k, source="iss")
            q = dataqc.check_daily(bars, now, cfg.get("max_data_age_days", 5))
            add(not q["errors"], "данные", "; ".join(q["errors"]) if q["errors"] else "свечи свежие, дефектов нет")
            for w in q["warnings"]:
                add(True, "данные", w, warn=True)
        except C.NetError as e:
            add(False, "данные", "не удалось получить свечи: %s" % str(e)[:80])
    # дивидендный гэп для свинга
    if o["mode"] == "swing" and k in ("shares",) and long_ and not offline:
        try:
            nd = moex.next_dividend(o["symbol"], now)
        except C.NetError as e:
            nd = None
            add(not cfg["block_dividend_gap"], "дивидендный гэп", "проверка не выполнена: %s" % str(e)[:80], warn=not cfg["block_dividend_gap"])
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
                add(bool(cfg["paper"]), "месячные свечи", "не получены, месяц посчитан по дневным свечам", warn=bool(cfg["paper"]))
            td = trend.top_down(daily, monthly, require=trend.STOCK_REQUIRE if k == "shares" else ("1w", "1d"))
            want = o["direction"]
            got = td["verdict"]["direction"]
            add(got == want, "тренд", "месяц %s, неделя %s, день %s — %s" % (
                trend.DIR_RU[td["1M"]["dir"]], trend.DIR_RU[td["1w"]["dir"]], trend.DIR_RU[td["1d"]["dir"]],
                "сделка по тренду" if got == want else ("сделка ПРОТИВ тренда" if got else td["verdict"]["why"])))
        except (C.NetError, KeyError, IndexError) as e:
            add(False, "тренд", "проверка не выполнена: %s" % str(e)[:80])
    # рейтинг: новая сделка только с высоким рейтингом в её направлении
    if cfg.get("require_high_rank") and k in ("shares", "etf", "futures", "currency"):
        import rank
        rk = o.get("rank")
        if not offline:
            try:
                bars_r = moex.candles(o["symbol"], "1d", days=200, end=now, kind=k, source="iss")
                rk = rank.assess(bars_r, o["direction"], o["symbol"], now)
            except (C.NetError, IndexError, TypeError) as e:
                rk = None
                add(False, "рейтинг", "проверка не выполнена: %s" % str(e)[:80])
        if rk and rk.get("direction", o["direction"]) == o["direction"]:
            add(rk["tier"] == "высокий", "рейтинг", "%s: подтверждений %d, против %d, новость %+d" % (
                rk["tier"], rk["k"], rk.get("a", 0), rk["news"]))
        elif offline:
            add(False, "рейтинг", "у заявки нет рейтинга в её направлении")
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
    risky = cfg.get("strategy", "moderate") == "risky"   # рынок растёт: только покупка, падает: только продажа
    if cfg["regime_filter"] and not offline and (risky or (long_ and k in ("shares", "etf"))):
        try:
            import ta
            idx = moex.candles("IMOEX", "1d", days=150, end=now, kind="index", source="iss")
            c = [b["c"] for b in idx if b["t"][:10] < now.strftime("%Y-%m-%d")]
            e50 = ta.ema(c, 50)[-1]
            up = c[-1] > e50
            fits = up == long_
            add(fits or o["mode"] == "intraday", "режим рынка", "IMOEX %s EMA50 дневной (%s vs %s)" % (
                "выше" if up else "ниже", C.px(c[-1]), C.px(e50)), warn=not fits)
        except (C.NetError, IndexError, TypeError) as e:
            add(False, "режим рынка", "проверка не выполнена: %s" % str(e)[:80])
    # статус стратегии в реестре и этап проекта
    import hypo
    sid, stat = hypo.strategy_status(o.get("setup"))
    if cfg["paper"]:
        bad = stat in ("rejected", "planned")
        add(not bad, "реестр", "%s: статус %s" % (sid or o.get("setup"), stat or "нет в реестре, только для учебной сделки"), warn=stat is None)
    else:
        add(stat == "live", "этап", "реальная заявка только для стратегии со статусом live, сейчас %s" % (stat or "нет в реестре"))
    return res


def status(cfg, now, offline):
    import riskbook
    st = riskbook.snapshot(cfg, now, offline)
    acc = st["account"]
    lim = lambda key, d: C.money_limit(cfg.get(key, d), acc)
    print("Риски портфеля на %s МСК" % C.fmt_dt(now))
    print("  капитал %s · максимум %s · просадка %s (объём %s)" % (
        C.rub(st["equity"], 0), C.rub(st["peak"], 0), C.pct(st["drawdown"] * 100, 1),
        {1.0: "обычный", 0.5: "вдвое меньше", 0.0: "ПАУЗА"}[st["dd_mult"]]))
    print("  результат: сегодня %s (лимит −%s) · неделя %s (лимит −%s)" % (
        C.rub(st["realized_today"], 0), C.rub(lim("daily_loss_limit", "2%"), 0),
        C.rub(st["realized_week"], 0), C.rub(lim("weekly_loss_limit", "4%"), 0)))
    print("  открытый риск %s из %s" % (C.rub(st["open_risk"], 0), C.rub(lim("max_open_risk", "3%"), 0)))
    rows = []
    for s_, p in st["positions"].items():
        r = st["position_risk"][s_]
        rows.append([s_, riskbook.sector(s_) or "—", p.get("side", "long"), p["lots"], C.px(p["avg"]),
                     C.px(p.get("stop")) if p.get("stop") is not None else "НЕТ СТОПА",
                     C.rub(r, 0) if r is not None else "∞", C.pct(r / acc * 100, 2) if r is not None else "—"])
    for e in st["pending"]:
        rows.append([e["symbol"], riskbook.sector(e["symbol"]) or "—", "заявка", e.get("lots"), C.px(e.get("price")),
                     C.px(e.get("stop")), C.rub(e.get("max_loss", 0), 0), C.pct(e.get("max_loss", 0) / acc * 100, 2)])
    if rows:
        print(C.table(rows, ["тикер", "отрасль", "сторона", "лотов", "средняя", "стоп", "риск ₽", "% счёта"]))
    else:
        print("  открытых позиций нет")
    by_sec = {}
    for s_, p in st["positions"].items():
        by_sec.setdefault(riskbook.sector(s_) or s_, []).append(s_)
    for sec, lst in by_sec.items():
        if len(lst) > 1:
            print("  ⚠ связанные позиции (считаются одной): %s — %s" % (sec, ", ".join(lst)))
    if st["no_stop"]:
        print("  ⛔ без стопа: %s — новые сделки заблокированы, пока не поставлен стоп" % ", ".join(st["no_stop"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--now"); ap.add_argument("--offline", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    cfg = C.config()
    if a.now and not cfg["paper"] and not a.status:
        C.die("--now запрещён при paper false: реальную заявку проверяем только на текущее время.")
    now = C.now_msk(a.now)
    if a.status:
        return status(cfg, now, a.offline)
    orders = C.load_json(C.tpath("orders.json"), []) or []
    if not orders:
        C.die("Нет заявок в trader/orders.json — сначала moex-budget size.py --write")
    st = today_stats(now, cfg, a.offline)
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
                                      "paper": bool(cfg["paper"]), "symbol": o["symbol"], "fails": [r["check"] for r in fails]}
    C.save_json(C.tpath("orders.json"), orders)
    C.save_json(C.tpath("state", "risk.json"), {"verdicts": verdicts})


if __name__ == "__main__":
    main()
