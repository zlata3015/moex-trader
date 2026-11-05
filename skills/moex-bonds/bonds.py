#!/usr/bin/env python3
"""moex-bonds: подбор облигаций Мосбиржи.

  bonds.py screen --kind ofz --max-years 3      список выпусков: доходность, дюрация, премия к ОФЗ
  bonds.py screen --kind corp --min-spread 200  корпоративные с премией от 2 п.п.
  bonds.py show SU26248RMFS3                    карточка: поток, налог, чувствительность к ставке, сравнение с фондом
  bonds.py ladder --sum 30000 --rungs 3         лестница погашений из ОФЗ
"""
import argparse
import importlib.util
import os
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "moex-setup", "lib"))
import bondcalc as B  # noqa: E402
import common as C  # noqa: E402
import moex  # noqa: E402

BOARDS = {"ofz": ["TQOB"], "corp": ["TQCB"], "all": ["TQOB", "TQCB"]}
# Эти выпуски нельзя сравнивать по доходности к погашению или они несут особый риск.
SKIP_TYPES = ("Структурная", "Конвертируемая", "Валютная", "Линкер")


def board(b):
    js = moex._get("/engines/stock/markets/bonds/boards/%s/securities.json" % b,
                   {"iss.only": "securities,marketdata,marketdata_yields"})
    md = {r["SECID"]: r for r in moex.table(js, "marketdata")}
    yl = {r["SECID"]: r for r in moex.table(js, "marketdata_yields")}
    out = []
    for s in moex.table(js, "securities"):
        out.append(dict(s, md=md.get(s["SECID"], {}), yl=yl.get(s["SECID"], {})))
    return out


def row_of(r, now):
    y, m = r["yl"], r["md"]
    dur = (y.get("DURATION") or 0) / 365.0
    mat = r.get("MATDATE")
    years = (datetime.strptime(mat, "%Y-%m-%d") - now).days / 365.0 if mat and mat != "0000-00-00" else None
    return {"secid": r["SECID"], "name": r.get("SHORTNAME"), "price": y.get("PRICE") or m.get("LAST") or r.get("PREVWAPRICE"),
            "yield": y.get("EFFECTIVEYIELD"), "dur": dur, "years": years, "spread": y.get("GSPREADBP"),
            "turnover": m.get("VALTODAY_RUR") or m.get("VALTODAY") or 0, "offer": r.get("OFFERDATE") or r.get("PUTOPTIONDATE"),
            "yield_type": y.get("YIELDDATETYPE"), "level": r.get("LISTLEVEL"), "coupon": r.get("COUPON_DETAILS"),
            "type": r.get("BONDTYPE") or "", "subtype": r.get("BONDSUBTYPE") or "", "face": r.get("FACEVALUE"),
            "accrued": r.get("ACCRUEDINT") or 0, "unit": r.get("FACEUNIT"), "lotvalue": r.get("LOTVALUE")}


def fits(x, a):
    if x["unit"] != "SUR" or not x["yield"] or x["yield"] <= 0 or x["dur"] <= 0 or not x["price"]:
        return False
    if any(t in x["type"] for t in SKIP_TYPES) or x["subtype"] == "Бессрочные":
        return False
    if "плавающ" in x["type"] or (x["coupon"] or "") not in ("Фиксированный с известными купонами", "Фиксированный с неизвестными купонами"):
        if not getattr(a, "floaters", False):
            return False
    if x["turnover"] < a.min_turnover:
        return False
    if getattr(a, "max_years", None) and (x["dur"] > a.max_years):
        return False
    if getattr(a, "min_yield", None) and x["yield"] < a.min_yield:
        return False
    if getattr(a, "min_spread", None) and (x["spread"] or 0) < a.min_spread:
        return False
    return True


def cmd_screen(a):
    now = C.now_msk(a.now)
    rows = []
    for b in BOARDS[a.kind]:
        rows += [x for x in (row_of(r, now) for r in board(b)) if fits(x, a)]
    distressed = [x for x in rows if (x["spread"] or 0) > a.max_spread]
    if not a.risky:
        rows = [x for x in rows if x not in distressed]
    rows.sort(key=lambda x: -x["yield"])
    rows = rows[:a.top]
    if not rows:
        print("Подходящих выпусков нет. Смягчите --min-turnover или срок.")
        return
    print(C.table([[x["secid"], (x["name"] or "")[:20], C.px(x["price"]), C.num(x["yield"], 2) + " %", C.num(x["dur"], 2),
                    "%+d" % x["spread"] if x["spread"] is not None else "—", C.rub(x["turnover"], 0),
                    "оферта" if x["yield_type"] == "OFFER" else "погашение", x["level"]] for x in rows],
                  ["выпуск", "название", "цена", "доходн.", "дюрация, лет", "G-спред, б.п.", "оборот", "до", "листинг"]))
    if distressed and not a.risky:
        print("Скрыто выпусков с премией выше %d б.п.: %d. Такая доходность обычно означает, что рынок закладывает дефолт (--risky покажет их)." % (a.max_spread, len(distressed)))
    print("\nДоходность ISS до налога. G-спред — премия к кривой ОФЗ. Рейтинга в ISS нет: проверьте его до покупки.")


def key_rate(now, cfg):
    p = os.path.join(HERE, "..", "moex-macro", "macro.py")
    sp = importlib.util.spec_from_file_location("macro_mod", p)
    m = importlib.util.module_from_spec(sp)
    try:
        sp.loader.exec_module(m)
        r = m.key_rate(now)
        return r["rate"] if r else cfg.get("key_rate")
    except (C.NetError, KeyError):
        return cfg.get("key_rate")


def card(sec):
    """Данные выпуска: котировка, доходность, описание, эмитент, поток."""
    r0 = moex.resolve(sec, None)
    bd = r0["board"]
    row = next((r for r in board(bd) if r["SECID"] == r0["secid"]), None)
    if row is None:
        C.die("Выпуск %s не найден в режиме %s" % (sec, bd))
    desc = {x["name"]: x["value"] for x in moex.table(moex._get("/securities/%s.json" % r0["secid"], {"iss.only": "description"}), "description")}
    bz = moex._get("/securities/%s/bondization.json" % r0["secid"], {"limit": "unlimited", "iss.only": "coupons,amortizations,offers"})
    return row, desc, bz


def cmd_show(a):
    cfg = C.config()
    now = C.now_msk(a.now)
    row, desc, bz = card(a.secid)
    x = row_of(row, now)
    settle = datetime.strptime(row.get("SETTLEDATE") or now.strftime("%Y-%m-%d"), "%Y-%m-%d")
    face = float(row.get("FACEVALUE") or 1000)
    info = B.build_flows(moex.table(bz, "coupons"), moex.table(bz, "amortizations"), moex.table(bz, "offers"), settle, face)
    clean = (row["md"].get("OFFER") or x["price"]) / 100 * face
    acc = float(row.get("ACCRUEDINT") or 0)
    tax = cfg.get("tax_pct", 13) / 100.0
    gross = B.irr(info["flows"], clean + acc, settle)
    print("%s · %s" % (x["secid"], desc.get("NAME") or x["name"]))
    print("  тип: %s, %s" % (desc.get("TYPENAME"), x["type"] or "—"))
    print("  цена покупки %s %% (%s ₽) + НКД %s = %s · номинал %s" % (C.px(clean / face * 100), C.num(clean, 2), C.num(acc, 2), C.rub(clean + acc), C.rub(face, 0)))
    print("  горизонт: %s %s · купонов впереди %d" % (info["horizon_type"], info["horizon"], len(info["coupons"])))
    print("  оборот сегодня %s · листинг %s" % (C.rub(x["turnover"], 0), x["level"]))
    flags = []
    if desc.get("ISQUALIFIEDINVESTORS") == "1":
        flags.append("только для квалифицированных инвесторов")
    if desc.get("HASDEFAULT") == "1":
        flags.append("ДЕФОЛТ")
    if desc.get("HASTECHNICALDEFAULT") == "1":
        flags.append("ТЕХНИЧЕСКИЙ ДЕФОЛТ")
    if desc.get("HIGHRISK") == "1":
        flags.append("высокий риск по данным биржи")
    if desc.get("AMORTBOND") == "1":
        flags.append("амортизация")
    if info["unknown"]:
        flags.append("%d купонов неизвестны: доходность считается только по объявленным и занижена" % info["unknown"])
    if flags:
        print("  ⚠ " + "; ".join(flags))
    if gross is None:
        print("  доходность рассчитать не удалось")
        return
    net = B.net_yield(info, clean, acc, settle, tax, cfg.get("fee_pct", 0.05) / 100)
    mac, mod = B.duration(info["flows"], settle, gross)
    print("  доходность ISS %s · расчёт по купонам %s · после налога и комиссии %s" % (
        C.pct(x["yield"]), C.pct(gross * 100), C.pct(net * 100) if net is not None else "—"))
    print("  дюрация %s лет · модифицированная %s · премия к ОФЗ %s" % (
        C.num(mac, 2), C.num(mod, 2), ("%+d б.п." % x["spread"]) if x["spread"] is not None else "нет данных"))
    print("  при изменении доходности: " + " · ".join("%+d п.п. → %s" % (d, C.pct(B.price_change(mod, d))) for d in (-3, -1, 1, 3)))
    kr = key_rate(now, cfg)
    if kr and net is not None:
        fund = (kr - cfg.get("fund_spread_pct", 1)) * (1 - tax)
        print("  фонд денежного рынка: ставка %s − %s п.п., после налога ≈ %s при неизменной ставке. Разница %s%s п.п." % (
            C.num(kr, 2), C.num(cfg.get("fund_spread_pct", 1), 1), C.pct(fund), "+" if net * 100 >= fund else "", C.num(net * 100 - fund, 2)))
    print("  купоны: " + ", ".join("%s %s" % (d, C.num(v, 2)) for d, v in info["coupons"][:4]))
    print("  рейтинг: в ISS нет, проверьте у АКРА, Эксперт РА, НКР или НРА. Лимит на корпоративного эмитента %s%% счёта." % cfg.get("issuer_limit_pct", 5))


def cmd_ladder(a):
    now = C.now_msk(a.now)
    a.min_yield = None; a.min_spread = None; a.max_years = None
    rows = [x for x in (row_of(r, now) for r in board("TQOB")) if fits(x, a) and x["years"]]
    per = a.sum / a.rungs
    out, spent, inc = [], 0.0, 0.0
    for i in range(a.rungs):
        lo, hi = i * a.step + 0.25, (i + 1) * a.step + 0.25
        pool = [x for x in rows if lo <= x["years"] < hi]
        if not pool:
            print("Ступень %s–%s лет: подходящей ОФЗ нет" % (C.num(lo, 2), C.num(hi, 2)))
            continue
        x = max(pool, key=lambda r: r["turnover"])
        unit = x["price"] / 100 * (x["face"] or 1000) + x["accrued"]
        n = int(per // unit)
        if n < 1:
            print("Ступень %s: сумма %s меньше одной облигации %s" % (x["secid"], C.rub(per, 0), C.rub(unit, 0)))
            continue
        out.append([x["secid"], (x["name"] or "")[:14], C.num(x["years"], 1), C.num(x["yield"], 2) + " %", C.num(x["dur"], 2), n, C.rub(n * unit, 0)])
        spent += n * unit
        inc += n * unit * x["yield"]
    if out:
        print(C.table(out, ["выпуск", "название", "лет", "доходн.", "дюрация", "штук", "сумма"]))
        print("\nИтого %s, остаток %s. Средняя доходность до налога %s. На каждой ступени выбран самый оборотный выпуск." % (
            C.rub(spent, 0), C.rub(a.sum - spent, 0), C.pct(inc / spent)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--now")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("screen"); p.add_argument("--kind", default="ofz", choices=list(BOARDS)); p.add_argument("--max-years", type=float, help="дюрация до N лет")
    p.add_argument("--min-yield", type=float); p.add_argument("--min-spread", type=float); p.add_argument("--min-turnover", type=float, default=1000000)
    p.add_argument("--floaters", action="store_true"); p.add_argument("--top", type=int, default=20)
    p.add_argument("--max-spread", type=float, default=1000, help="выше этой премии к ОФЗ выпуск скрывается"); p.add_argument("--risky", action="store_true"); p.set_defaults(fn=cmd_screen)
    p = sub.add_parser("show"); p.add_argument("secid"); p.set_defaults(fn=cmd_show)
    p = sub.add_parser("ladder"); p.add_argument("--sum", type=float, required=True); p.add_argument("--rungs", type=int, default=3)
    p.add_argument("--step", type=float, default=1.0, help="лет между ступенями"); p.add_argument("--min-turnover", type=float, default=5000000); p.set_defaults(fn=cmd_ladder)
    a = ap.parse_args()
    try:
        a.fn(a)
    except C.NetError as e:
        C.die(str(e))


if __name__ == "__main__":
    main()
