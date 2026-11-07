#!/usr/bin/env python3
"""moex-trend: утренний журнал приоритетных покупок и сигналы продажи по портфелю. Заявок не отправляет.

  morning.py --journal journal.json --portfolio portfolio.json [--limit 100] [--json]

journal.json: записи {date, symbol, close, rs, signal, stop}. Новые дописываются, старше 30 дней удаляются.
state.json: прошлый рейтинг и показатели, чтобы показать изменения.
portfolio.json: {"strategy": "moderate", "cash": 100000, "positions": [{"symbol", "qty", "entry", "stop", "date"}]}.
"""
import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "moex-setup", "lib"))
import common as C  # noqa: E402
import credit  # noqa: E402
import moex  # noqa: E402
import priority as P  # noqa: E402
import ta  # noqa: E402
import trend  # noqa: E402

KEEP_DAYS = 30
MAX_HOLD = 60


def load(path, default):
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def update_journal(old, rows, day, today=None):
    """Дописать бумаги дня один раз и удалить записи старше KEEP_DAYS. Возвращает (журнал, удалено)."""
    today = today or date.fromisoformat(day)
    cut = (today - timedelta(KEEP_DAYS)).isoformat()
    kept = [r for r in old if r["date"] >= cut]
    have = {(r["date"], r["symbol"]) for r in kept}
    for r in rows:
        if (day, r["symbol"]) not in have:
            kept.append({"date": day, "symbol": r["symbol"], "close": r["close"], "rs": round(r["rs"], 4), "signal": r["signal"], "stop": round(r["stop"], 4)})
    return sorted(kept, key=lambda r: (r["date"], r["symbol"])), len(old) - len([r for r in old if r["date"] >= cut])


def update_bonds(journal, picks, day):
    have = {(r["date"], r["symbol"]) for r in journal}
    for b in picks:
        if (day, b["name"]) not in have:
            journal.append({"date": day, "kind": "bond", "symbol": b["name"], "isin": b["isin"], "ytm": b["ytm"], "rating": b["rating"], "spread": b["spread"],
                            "duration": b["duration"], "price": b["price"]})
    return sorted(journal, key=lambda r: (r["date"], r["symbol"]))


def bond_changes(old, bonds, journal):
    """Изменение рейтинга облигаций журнала. old: {isin: рейтинг}."""
    now = {b["isin"]: b for b in bonds}
    out = []
    for isin in {r["isin"] for r in journal if r.get("kind") == "bond"}:
        b = now.get(isin)
        if b and old.get(isin) and b["rating"] and old[isin] != b["rating"]:
            out.append("%s: рейтинг %s → %s" % (b["name"], old[isin], b["rating"]))
    return sorted(out)


def days_listed(journal, symbol):
    return len({r["date"] for r in journal if r["symbol"] == symbol})


def exit_view(pos, bars, today):
    """Вердикт по позиции: сигнал продажи по правилу и предупреждения. bars: дни от начала истории."""
    c = [b["c"] for b in bars]
    atr = ta.atr([b["h"] for b in bars], [b["l"] for b in bars], c)[-1]
    held = [b for b in bars if b["t"][:10] >= pos["date"]]
    top = max([b["h"] for b in held] or [pos["entry"]])
    stop = max(pos["stop"], top - 3 * atr)
    last = bars[-1]
    days = len(held)
    sell, warn = [], []
    if last["l"] <= stop:
        sell.append("задет стоп %s" % C.px(stop))
    if days >= MAX_HOLD:
        sell.append("прошло %d дней из %d" % (days, MAX_HOLD))
    wk = trend.trend_state(trend.resample_period(bars, "W"), "1w")["dir"]
    if wk == "down":
        warn.append("неделя вниз")
    e20 = ta.ema(c, 20)[-1]
    if e20 and c[-1] < e20:
        warn.append("ниже EMA20")
    return {"symbol": pos["symbol"], "close": c[-1], "stop": stop, "days": days, "pnl_pct": (c[-1] / pos["entry"] - 1) * 100,
            "action": "ПРОДАТЬ" if sell else "держать", "sell": sell, "warn": warn, "qty": pos.get("qty", 0), "entry": pos["entry"]}


def auto_close(pos, bars, fee=0.0005):
    """Учебное закрытие по дням: стоп, подтягиваемый стоп 3 ATR, 60 дней. Цена на гэпе — открытие. None, если позиция жива."""
    c = [b["c"] for b in bars]
    atr = ta.atr([b["h"] for b in bars], [b["l"] for b in bars], c)
    stop, top, n = pos["stop"], pos["entry"], 0
    for i, b in enumerate(bars):
        d = b["t"][:10]
        if d < pos["date"]:
            continue
        n += 1
        if b["l"] <= stop:
            px, why = min(b["o"], stop), "стоп"
        elif n >= MAX_HOLD:
            px, why = b["c"], "60 дней"
        else:
            top = max(top, b["h"])
            if atr[i]:
                stop = max(stop, top - 3 * atr[i])
            continue
        pnl = (px * (1 - fee) - pos["entry"] * (1 + fee)) * pos["qty"]
        return dict(pos, exit_date=d, exit=round(px, 4), why=why, pnl=round(pnl, 2), days=n)
    return None


def size(cash, row, risk_pct=0.5, cap_pct=20):
    """Лот не учитывается: штук по риску и потолку доли."""
    d = row["close"] - row["stop"]
    return int(min(cash * risk_pct / 100 / d, cash * cap_pct / 100 / row["close"])) if d > 0 else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--journal", required=True)
    ap.add_argument("--portfolio", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    now = C.now_msk()
    cut = P.closed_day(now)
    try:
        ix = P.ix_state(cut)
    except C.NetError as e:
        C.die("ISS недоступен: %s. Журнал не менялся." % e)
    pf = load(a.portfolio, {"strategy": "moderate", "cash": 100000, "positions": []})
    rows, bad = [], []

    def one(s):
        try:
            return s, P.stock_row(s, ix, cut)
        except (C.NetError, KeyError, IndexError, ValueError, TypeError):
            return s, False

    with ThreadPoolExecutor(5) as ex:
        for s, r in ex.map(one, P.universe(a.limit)):
            if r is False:
                bad.append(s)
            elif r and r["date"] == ix["date"]:
                rows.append(r)
    pri = sorted((r for r in rows if r["priority"]), key=lambda r: -r["rs"])
    journal, removed = update_journal(load(a.journal, []), pri, ix["date"], now.date())
    with open(a.journal, "w", encoding="utf-8") as f:
        json.dump(journal, f, ensure_ascii=False, indent=1)
    import riskbook
    try:
        rate = riskbook.key_rate(now)
    except C.NetError:
        rate = None
    limit = C.config().get("max_key_rate")
    regime = ix["closes"][-1] > ta.ema(ix["closes"], 50)[-1]
    moderate_ok = rate is not None and limit and rate <= limit and regime
    held = {p["symbol"] for p in pf["positions"]}
    today = [r for r in pri if r["signal"] and r["symbol"] not in held]
    for r in today:
        r["qty"] = size(pf["cash"], r)
    exits, live, done = [], [], []
    for p in pf["positions"]:
        if p.get("kind") == "bond":
            live.append(p)
            exits.append({"symbol": p["symbol"], "action": "облигация, стопа нет, контроль по рейтингу и лимиту на эмитента", "sell": [], "warn": []})
            continue
        try:
            b = [x for x in moex.candles(p["symbol"], "1d", days=500, source="iss") if x["t"][:10] <= cut]
            closed = auto_close(p, b)
            if closed:
                done.append(closed)
                pf["cash"] = pf.get("cash", 0) + closed["qty"] * closed["exit"] * 0.9995
                continue
            live.append(p)
            exits.append(exit_view(p, b, cut))
        except C.NetError:
            live.append(p)
            exits.append({"symbol": p["symbol"], "action": "данных нет, проверить вручную", "sell": [], "warn": []})
    pf["positions"] = live
    pf["closed"] = pf.get("closed", []) + done
    with open(a.portfolio, "w", encoding="utf-8") as f:
        json.dump(pf, f, ensure_ascii=False, indent=1)
    res = dict(day=ix["date"], strategy=pf["strategy"], rate=rate, market=ix["dir"], checked=len(rows), failed=bad, priority=pri, today=today,
               moderate_ok=bool(moderate_ok), exits=exits, closed=done, cash=pf.get("cash"), removed=removed, journal_size=len(journal))
    try:
        res["h10"] = P.h10(ix, cut)
    except (C.NetError, KeyError, IndexError, ValueError, TypeError) as e:
        res["h10_error"] = str(e)[:120]
    bonds = credit.bond_table()
    rmap = credit.rating_map(bonds)
    old = load(a.state, {})
    picks = credit.bond_pick(bonds, now.date())
    journal = update_bonds(journal, picks, ix["date"])
    with open(a.journal, "w", encoding="utf-8") as f:
        json.dump(journal, f, ensure_ascii=False, indent=1)
    res["bonds"] = picks
    res["bond_changes"] = bond_changes(old.get("_bonds", {}), bonds, journal)
    res["journal_size"] = len(journal)
    names = sorted({r["symbol"] for r in journal if r.get("kind") != "bond"} | {p["symbol"] for p in pf["positions"] if p.get("kind") != "bond"})
    with ThreadPoolExecutor(4) as ex:
        snaps = dict(zip(names, ex.map(lambda s: credit.snapshot(s, rmap), names)))
    res["credit"] = {s: dict(v, changes=credit.changes(old.get(s), v)) for s, v in snaps.items()}
    res["rating_coverage"] = len(rmap)
    with open(a.state, "w", encoding="utf-8") as f:
        rated = dict(old.get("_bonds", {}), **{r["isin"]: rmap[r["isin"]] for r in journal if r.get("kind") == "bond" and r["isin"] in rmap})
        json.dump(dict(old, _bonds=rated, **snaps), f, ensure_ascii=False, indent=1)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
        return
    print(render(res, journal))


def company_lines(c):
    f = c.get("fin") or {}
    x = ["   Рейтинг %s. Устойчивость %s, ликвидность %s, риск дефолта %s" % (
        c.get("rating") or "не найден", f.get("stability", "⚪"), f.get("liquidity", "⚪"), f.get("default", "⚪")),
        "   %s%s" % (credit.fin_line(c.get("fin")), ", отчёт %s" % f.get("year") if f.get("year") else "")]
    x += ["   Изменение: %s" % z for z in c.get("changes", [])]
    return x


def buy_status(res):
    """(можно покупать, почему нет)."""
    if res["strategy"] != "moderate":
        return True, ""
    if res["moderate_ok"]:
        return True, ""
    why = "ключевая ставка %s%% выше порога 12%%" % res["rate"] if res["rate"] and res["rate"] > 12 else "IMOEX ниже EMA50 или нет данных о ставке"
    return False, why


def render(res, journal):
    mk = {1: "растёт", -1: "падает", 0: "без направления"}[res["market"]]
    st = {"moderate": "умеренная", "risky": "рискованная"}.get(res["strategy"], res["strategy"])
    ok, why = buy_status(res)
    sig, wait = res["today"], [r for r in res["priority"] if not r["signal"]]
    ex = res["exits"]
    L = ["Утренний отчёт. Закрытие %s" % res["day"],
         "Стратегия %s, ставка %s%%, рынок %s" % (st, res["rate"] if res["rate"] is not None else "н/д", mk), "",
         "ГЛАВНОЕ",
         "Сигналы на покупку: %s" % (", ".join(r["symbol"] for r in sig) or "нет"),
         "Купить сегодня: %s" % (", ".join(r["symbol"] for r in sig) if sig and ok else "ничего" + (", " + why if sig and not ok else "")),
         "Портфель: %s" % ("пуст" if not ex else "%d поз., держим: %s" % (len(ex), ", ".join(e["symbol"] for e in ex))),
         "Закрыто автоматически: %s" % (", ".join(c["symbol"] for c in res.get("closed", [])) or "ничего"), "",
         "СИГНАЛЫ НА ПОКУПКУ"]
    if not sig:
        L.append("сегодня сделки нет")
    for r in sig:
        L.append("%s, %s. Цена %s, стоп %s, до %d шт. без учёта лота. %s" % (
            r["symbol"], r["signal"], C.px(r["close"]), C.px(r["stop"]), r["qty"], "Можно купить" if ok else "Купить нельзя, только в журнал"))
        L += company_lines(res["credit"].get(r["symbol"], {}))
    L += ["", "ЖУРНАЛ: ЖДЁМ СИГНАЛА"]
    sigs = {r["symbol"] for r in sig}
    names = [s for s in sorted({r["symbol"] for r in journal if r.get("kind") != "bond"}) if s not in sigs]
    if not names:
        L.append("нет бумаг")
    for s in names:
        e = [r for r in journal if r["symbol"] == s]
        L.append("%s, в журнале с %s, дней %d, цена при записи %s" % (s, e[0]["date"], len(e), C.px(e[-1]["close"])))
        L += company_lines(res["credit"].get(s, {}))
    L += ["", "ОБЛИГАЦИИ: НАБЛЮДЕНИЕ", "Купить: ничего. Лестница не обогнала фонд, покупки облигаций выключены."]
    fundy = (res["rate"] - 1) if res["rate"] is not None else None
    for b in res.get("bonds", []):
        L.append("%s, рейтинг %s, доходность %.1f%%, премия к ОФЗ %.0f б.п., дюрация %.1f г., погашение %s" % (
            b["name"], b["rating"], b["ytm"], b["spread"], b["duration"], b["maturity"]))
    if not res.get("bonds"):
        L.append("Подходящих бумаг нет.")
    if fundy is not None and res.get("bonds"):
        L.append("Для сравнения: фонд около %.0f%% годовых до налога, у корпоративных бумаг риск дефолта." % fundy)
    L += ["Изменение: %s" % z for z in res.get("bond_changes", [])]
    L += ["", "ПОРТФЕЛЬ, свободно %s ₽" % C.px(res.get("cash") or 0)]
    for c in res.get("closed", []):
        L.append("%s, %d шт.: закрыта %s, причина: %s" % (c["symbol"], c["qty"], c["exit_date"], c["why"]))
        L.append("   Куплено по %s, продано по %s, результат %+.0f ₽ с комиссией" % (C.px(c["entry"]), C.px(c["exit"]), c["pnl"]))
    if not ex and not res.get("closed"):
        L.append("Открытых позиций нет.")
    for e in ex:
        if "close" not in e:
            L.append("%s: данных нет, проверить вручную" % e["symbol"])
            continue
        L.append("%s, %d шт.: %s" % (e["symbol"], e["qty"], e["action"]))
        L.append("   Куплено по %s, сейчас %s, результат %+.1f%% (%+.0f ₽)" % (C.px(e["entry"]), C.px(e["close"]), e["pnl_pct"], (e["close"] - e["entry"]) * e["qty"]))
        L.append("   Стоп %s, дней в позиции %d" % (C.px(e["stop"]), e["days"]))
        if e["sell"] or e["warn"]:
            L.append("   Причина: %s" % "; ".join(e["sell"] + e["warn"]))
        L += company_lines(res["credit"].get(e["symbol"], {}))
    L += ["", "ПЕСОЧНИЦА H10, РИСКОВАННАЯ"]
    act = [r for r in res.get("h10", []) if r["active"]]
    if "h10_error" in res:
        L.append("данные не получены: %s" % res["h10_error"])
    elif not act:
        L.append("сигналов нет")
    for r in act:
        L.append("%s, %s, контракт %s, приоритет %s" % (r["symbol"], "покупка" if r["side"] > 0 else "продажа", r["contract"], "да" if r["priority"] else "нет"))
    L += ["", "🟢 норма, 🟡 внимание, 🔴 слабо, ⚪ нет данных. Рейтинг по облигациям эмитента, без агентства.",
          "Закрытие учебное, по ценам ISS. Заявки не отправляются. Цифры не обещают доходность."]
    return "\n".join(L)


if __name__ == "__main__":
    main()
