#!/usr/bin/env python3
"""moex-trend: кредитный рейтинг и финансовое состояние компании. Сведения для решения, сделку сами не запрещают.

Рейтинг берётся по облигациям эмитента из таблицы smart-lab: это ориентир без названия агентства и прогноза.
Сайты АКРА и Эксперт РА из скрипта недоступны, изменение надо сверять по их релизам.
Показатели: годовая МСФО smart-lab, последний год с данными.
"""
import collections
import html
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "moex-setup", "lib"))
import common as C  # noqa: E402
import moex  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0"}
SCALE = "AAA AA+ AA AA- A+ A A- BBB+ BBB BBB- BB+ BB BB- B+ B B- CCC CC C D".split()
ALIAS = {"SNGSP": "SNGS", "SBERP": "SBER", "TATNP": "TATN", "RTKMP": "RTKM", "BSPBP": "BSPB", "MTLRP": "MTLR", "TRNFP": "TRNF"}


def num(x):
    try:
        return float(x.replace("%", "").replace(" ", ""))
    except ValueError:
        return None


def bond_rows(t):
    """Строки таблицы облигаций smart-lab."""
    out = []
    for tr in re.findall(r"<tr.*?</tr>", t, re.S):
        m = re.search(r'href="/q/bonds/([A-Z0-9]{12})/"', tr)
        td = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", x))).strip() for x in re.findall(r"<td.*?</td>", tr, re.S)]
        if not m or len(td) < 19:
            continue
        out.append(dict(isin=m[1], name=td[1], years=num(td[3]), ytm=num(td[4]), rating=td[7] if td[7] in SCALE else None, spread=num(td[8]),
                        volume=num(td[9]), duration=num(td[13]), price=num(td[14]), maturity=td[17], offer=td[18]))
    return out


def bond_table(pages=16):
    out = []
    for p in range(1, pages + 1):
        url = "https://smart-lab.ru/q/bonds/order_by_val_to_day/desc/" + ("page%d/" % p if p > 1 else "")
        try:
            out += bond_rows(C.http(url, headers=UA, timeout=15, retries=1).decode("utf-8", "replace"))
        except C.NetError:
            continue
    return out


def rating_map(bonds):
    return {b["isin"]: b["rating"] for b in bonds if b["rating"]}


def bond_ok(b, today):
    """Отбор облигаций. Пороги — правила инструкции и здравый смысл, сигналом покупки не являются."""
    if not b["rating"] or SCALE.index(b["rating"]) > SCALE.index("A-"):
        return False
    if b["duration"] is None or not 1 <= b["duration"] <= 5 or b["years"] is None or b["years"] > 7:
        return False
    if b["volume"] is None or b["volume"] < 5 or b["spread"] is None or not 100 <= b["spread"] <= 1000:
        return False
    if b["ytm"] is None or b["price"] is None:
        return False
    if b["offer"] and b["offer"] != "-":
        try:
            d, m, y = b["offer"].split(".")
            if date(2000 + int(y), int(m), int(d)) < today + timedelta(180):
                return False
        except ValueError:
            return False
    return True


def bond_pick(bonds, today, top=5):
    """Лучшие по премии к ОФЗ, не больше одной бумаги на название эмитента."""
    seen, out = set(), []
    for b in sorted((x for x in bonds if bond_ok(x, today)), key=lambda x: -x["spread"]):
        key = re.sub(r"[\d].*$", "", b["name"])
        if key not in seen:
            seen.add(key)
            out.append(b)
        if len(out) == top:
            break
    return out


def issuer_rating(sym, rmap):
    """Самый частый рейтинг облигаций того же эмитента по ISS."""
    who = moex.issuer(ALIAS.get(sym, sym))
    if not who or not who.get("issuer"):
        return None
    rows = moex.table(moex._get("/emitters/%s/securities.json" % who["issuer"], {"iss.only": "securities"}), "securities")
    got = [rmap[r["ISIN"]] for r in rows if r.get("ISIN") in rmap]
    return collections.Counter(got).most_common(1)[0][0] if got else None


def parse_table(t):
    m = re.search(r"<table[^>]*financials.*?</table>", t, re.S)
    rows = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", m[0] if m else "", re.S):
        td = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", x))).strip() for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        if len(td) > 2:
            rows[td[0] or "период"] = td[1:]
    return rows


def last(rows, key):
    for v in reversed([x for x in rows.get(key, [])]):
        x = v.replace(" ", "").replace(",", ".").replace("%", "")
        try:
            return float(x)
        except ValueError:
            continue
    return None


def financials(sym):
    code = ALIAS.get(sym, sym)
    rows = {}
    for path in ("MSFO", "RSBU"):
        try:
            rows = parse_table(C.http("https://smart-lab.ru/q/%s/f/y/%s/" % (code, path), headers=UA, timeout=15, retries=1).decode("utf-8", "replace"))
        except C.NetError:
            rows = {}
        if rows:
            std = path
            break
    if not rows:
        return None
    head = next(iter(rows.values()))
    year = next((y for y in reversed(head) if re.fullmatch(r"20\d\d", y)), None)
    g = lambda k: last(rows, k)
    if g("Дост. общ капитала, %") is not None:
        return score_bank(dict(kind="банк", std=std, year=year, capital=g("Дост. общ капитала, %"), npl=g("Просроченные кредиты, NPL, %"),
                               cor=g("Стоимость риска (CoR), %"), ldr=g("Loan-to-deposit ratio, %"), profit=g("Чистая прибыль, млрд руб")))
    nd, e, debt, cash, assets, na, pr = (g(k) for k in ("Чистый долг, млрд руб", "EBITDA, млрд руб", "Долг, млрд руб", "Наличность, млрд руб",
                                                        "Активы, млрд руб", "Чистые активы, млрд руб", "Процентные расходы, млрд руб"))
    return score_firm(dict(kind="компания", std=std, year=year, nd_ebitda=nd / e if nd is not None and e and e > 0 else None, ebitda=e,
                           cover=e / pr if e is not None and pr and pr > 0 else None, cash_debt=cash / debt if cash is not None and debt and debt > 0 else None,
                           no_debt=debt == 0 or (nd is not None and nd <= 0), equity=na / assets if na is not None and assets else None,
                           fcf=g("FCF, млрд руб"), ocf=g("Опер.денежный поток, млрд руб"), profit=g("Чистая прибыль, млрд руб")))


def light(v, good, bad, higher=True):
    """🟢 🟡 🔴 или ⚪ при отсутствии данных."""
    if v is None:
        return "⚪"
    if higher:
        return "🟢" if v >= good else "🔴" if v < bad else "🟡"
    return "🟢" if v <= good else "🔴" if v > bad else "🟡"


def worst(*x):
    x = [i for i in x if i != "⚪"]
    return "⚪" if not x else "🔴" if "🔴" in x else "🟡" if "🟡" in x else "🟢"


def score_firm(f):
    """Пороги — правила здравого смысла. Преимущество на истории не проверено, качество V01 отклонено."""
    nd = "🟢" if f["no_debt"] else ("🔴" if f["ebitda"] is not None and f["ebitda"] <= 0 else light(f["nd_ebitda"], 2, 3, False))
    f["stability"] = worst(nd, light(f["equity"], .4, .2))
    f["liquidity"] = "🟢" if f["no_debt"] else worst(light(f["cash_debt"], .3, .1), light(f["ocf"], 0.0001, 0))
    f["default"] = worst(nd, "🟢" if f["no_debt"] else light(f["cover"], 3, 1.5), light(f["profit"], 0.0001, 0))
    return f


def score_bank(f):
    f["stability"] = light(f["capital"], 12, 10)
    f["liquidity"] = light(f["ldr"], 100, 130, False)
    f["default"] = worst(light(f["npl"], 5, 10, False), light(f["cor"], 1.5, 3, False), light(f["profit"], 0.0001, 0))
    return f


def snapshot(sym, rmap):
    try:
        rating = issuer_rating(sym, rmap)
    except (C.NetError, KeyError, IndexError, TypeError):
        rating = None
    return dict(rating=rating, fin=financials(sym))


def changes(old, new):
    """Что изменилось со вчера: рейтинг и светофоры."""
    out = []
    if not old:
        return out
    a, b = old.get("rating"), new.get("rating")
    if a and b and a != b:
        arrow = "⬆️" if SCALE.index(b) < SCALE.index(a) else "⬇️"
        out.append("%s рейтинг %s → %s" % (arrow, a, b))
    names = dict(stability="устойчивость", liquidity="ликвидность", default="риск дефолта")
    f0, f1 = old.get("fin") or {}, new.get("fin") or {}
    for k, n in names.items():
        if f0.get(k) and f1.get(k) and f0[k] != f1[k]:
            out.append("%s %s → %s" % (n, f0[k], f1[k]))
    return out


def fin_line(f):
    if not f:
        return "отчётность не найдена"
    if f["kind"] == "банк":
        return "капитал %s%%, NPL %s%%, CoR %s%%, кредиты/депозиты %s%%" % tuple("н/д" if f[k] is None else "%.1f" % f[k] for k in ("capital", "npl", "cor", "ldr"))
    nd = "долга нет" if f["no_debt"] else "чистый долг/EBITDA " + ("н/д" if f["nd_ebitda"] is None else "%.1f" % f["nd_ebitda"])
    cov = "" if f["cover"] is None else ", покрытие процентов %.1f" % f["cover"]
    cd = "" if f["cash_debt"] is None else ", деньги/долг %.2f" % f["cash_debt"]
    return nd + cov + cd
