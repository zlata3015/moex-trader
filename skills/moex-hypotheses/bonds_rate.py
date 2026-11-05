"""Первая запись реестра: облигации и ключевая ставка. Данные: ISS и cbr.ru, нужен доступ к этим сайтам."""
import concurrent.futures as cf
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "moex-setup", "lib"))
import common as C  # noqa: E402
import hypo as H  # noqa: E402

ISS = "https://iss.moex.com/iss"
CBR = "https://www.cbr.ru"
FROM, TO = "01.01.2013", None


def cached(name, fn):
    p = C.tpath("research", name + ".json")
    d = C.load_json(p)
    if d is None:
        d = fn()
        C.save_json(p, d)
    return d


def index(sec):
    def get():
        rows, st = [], 0
        while True:
            js = C.http_json("%s/history/engines/stock/markets/index/securities/%s.json" % (ISS, sec),
                             {"iss.meta": "off", "start": st, "history.columns": "TRADEDATE,CLOSE"})
            b = js["history"]["data"]
            if not b:
                return [r for r in rows if r[1] is not None]
            rows += b
            st += len(b)
    return [tuple(x) for x in cached("idx_" + re.sub(r"\W", "_", sec), get)]


def key_rate(today):
    def get():
        q = {"UniDbQuery.Posted": "True", "UniDbQuery.From": FROM, "UniDbQuery.To": today}
        t = C.http(CBR + "/hd_base/KeyRate/", q).decode("utf-8")
        rows = re.findall(r"<td[^>]*>\s*(\d\d)\.(\d\d)\.(\d{4})\s*</td>\s*<td[^>]*>\s*([\d,]+)\s*</td>", t)
        return sorted(("%s-%s-%s" % (y, m, d), float(v.replace(",", "."))) for d, m, y, v in rows)
    return [tuple(x) for x in cached("key", get)]


def cpi(today):
    def get():
        q = {"UniDbQuery.Posted": "True", "UniDbQuery.From": FROM, "UniDbQuery.To": today}
        t = C.http(CBR + "/hd_base/infl/", q).decode("utf-8")
        rows = re.findall(r"<td[^>]*>\s*(\d{1,2})\.(\d{4})\s*</td>\s*<td[^>]*>[^<]*</td>\s*<td[^>]*>\s*([\d,]+)\s*</td>", t)
        return {"%s-%02d" % (y, int(m)): float(v.replace(",", ".")) for m, y, v in rows}
    return cached("cpi", get)


def usd(today):
    def get():
        a = "01/01/2013"
        b = "/".join(today.split("."))
        t = C.http(CBR + "/scripts/XML_dynamic.asp", {"date_req1": a, "date_req2": b, "VAL_NM_RQ": "R01235"})
        out = []
        for x in ET.fromstring(t):
            d, m, y = x.attrib["Date"].split(".")
            out.append(("%s-%s-%s" % (y, m, d), float(x.find("Value").text.replace(",", ".")) / float(x.find("Nominal").text)))
        return sorted(out)
    return [tuple(x) for x in cached("usd", get)]


def curve(month_ends):
    def get():
        def one(d):
            js = C.http_json(ISS + "/engines/stock/zcyc.json", {"iss.meta": "off", "iss.only": "yearyields", "date": d})
            return d, {str(float(r[2])): r[3] for r in js["yearyields"]["data"]}
        with cf.ThreadPoolExecutor(6) as e:
            return dict(e.map(one, month_ends))
    return cached("zcyc", get)


def month_ends(cal):
    return [d for i, d in enumerate(cal) if i + 1 == len(cal) or cal[i + 1][:7] != d[:7]]


def prev_month(ym, n):
    y, m = int(ym[:4]), int(ym[5:7])
    t = y * 12 + m - 1 - n
    return "%04d-%02d" % (t // 12, t % 12 + 1)


def signals(cal, L, key, cpi_m, usd_s, zc):
    """Значения на концы месяцев, только известное на дату. Инфляция с запасом: за месяц t-2."""
    money = H.money_index(cal, key)
    k = H.step_values(cal, key)
    u = H.step_values(cal, usd_s)
    me = [d for d in month_ends(cal) if d >= "2014-01-06" and d in zc]
    pos = {d: i for i, d in enumerate(cal)}
    last_sign, sign_at = 0, {}
    for i, d in enumerate(cal):
        if i and k[i] != k[i - 1] and k[i - 1] is not None:
            last_sign = 1 if k[i] > k[i - 1] else -1
        sign_at[d] = last_sign

    def back(series, i, n):
        return series[i] / series[i - n] - 1 if i >= n else None

    xs = [a / b for a, b in zip(L, money)]
    out = {n: [] for n in NAMES}
    for d in me:
        i = pos[d]
        y = zc[d]
        y10, y1, y3 = y.get("10.0"), y.get("1.0"), y.get("0.25")
        kk = k[i]
        c = cpi_m.get(prev_month(d[:7], 2))
        c3 = cpi_m.get(prev_month(d[:7], 5))
        ma = sum(L[i - 199:i + 1]) / 200 if i >= 199 else None
        peak = max(L[max(0, i - 251):i + 1])
        m3, m6, m12 = back(xs, i, 63), back(xs, i, 126), back(xs, i, 252)
        du = back(u, i, 63)
        cut = sign_at[d] < 0
        vals = {
            "B01": cut, "B02": kk > y10, "B03": y10 > kk, "B04": y10 > kk + 1, "B05": y10 > y1, "B06": y10 < y1,
            "B07": y3 < kk - 0.5, "B08": ma is not None and L[i] > ma, "B09": m3 is not None and m3 > 0,
            "B10": m6 is not None and m6 > 0, "B11": m12 is not None and m12 > 0,
            "B12": c is not None and kk - c > 5, "B13": c is not None and y10 - c > 5,
            "B14": c is not None and c3 is not None and c < c3, "B15": du is not None and du < 0.07,
            "B16": L[i] / peak - 1 < -0.10, "B17": cut and ma is not None and L[i] > ma,
            "B18": kk > y10 or (ma is not None and L[i] > ma),
            "B19": m3 is not None and m3 > 0 and du is not None and du < 0.07,
        }
        for n, v in vals.items():
            out[n].append((d, 1.0 if v else 0.0))
    return out


NAMES = ["B%02d" % i for i in range(1, 20)]
LONG = "RUGBITR5+"
# id, индекс, с какой даты, какой сигнал
ITEMS = [(n, LONG, "2015-01-01", n) for n in NAMES] + [
    ("B20", LONG, "2014-03-03", "B01"), ("B21", "RUGBITR5Y", "2014-03-03", "B01"), ("B22", "RUGBITR3Y", "2014-03-03", "B01"),
]


def main(reg_path, periods, dry=False):
    today = C.now_msk().strftime("%d.%m.%Y")
    reg = H.load(reg_path)
    series = {s: index(s) for s in (LONG, "RUGBITR5Y", "RUGBITR3Y")}
    base = series[LONG]
    key = key_rate(today)
    cal_all = [d for d, _ in base]
    cal = [d for d in cal_all if d >= "2013-09-17"]
    L = H.step_values(cal, base)
    sig = signals(cal, L, key, cpi(today), usd(today), curve([d for d in month_ends(cal) if d >= "2014-01-06"]))
    rows = []
    for hid, sec, since, sname in ITEMS:
        a = H.step_values(cal, series[sec])
        m = H.evaluate(cal, a, key, sig[sname], since, periods)
        if dry:
            st, why = H.verdict(m, H.family_size(reg, "bonds-rate"), "rare", reg.get("criteria"))
        else:
            h = H.record(reg, hid, m)
            st, why = h["status"], h["reasons"]
        rows.append([hid, st, m["annual"], m["premium"], m["max_dd"], m["p"], m["premium_late"], "%d/%d" % (m["legs_won"], m["legs"])])
    if not dry:
        H.save(reg_path, reg)
    print(C.table(rows, ["id", "статус", "годовых", "премия", "просадка", "p", "задержка", "заходы"]))


def cycle(reg_path, periods):
    """G02, G16, G17, G03, G18, G19 и варианты B23–B28. Уже записанные гипотезы не пересчитываются в реестре."""
    today = C.now_msk().strftime("%d.%m.%Y")
    reg = H.load(reg_path)
    long5, s1, m5 = index(LONG), index("RUGBITR1Y"), index("RUGBITR5Y")
    key = key_rate(today)
    cal = [d for d, _ in long5 if d >= "2013-09-17"]
    l, s, mid = H.step_values(cal, long5), H.step_values(cal, s1), H.step_values(cal, m5)
    k = H.step_values(cal, key)
    me = [d for d in month_ends(cal) if d >= "2014-01-06"]
    zc = curve(me)
    money = H.money_index(cal, key)
    pos_of = {d: i for i, d in enumerate(cal)}
    sign, changed, sign_at, chg_at = 0, None, {}, {}
    for i, d in enumerate(cal):
        if i and k[i - 1] is not None and k[i] != k[i - 1]:
            sign, changed = (1 if k[i] > k[i - 1] else -1), d
        sign_at[d], chg_at[d] = sign, changed
    xs = [a / b for a, b in zip(l, money)]

    def flag(fn):
        return [(d, 1.0 if fn(d, pos_of[d]) else 0.0) for d in me]

    def ahead(d, i):
        ke = ((1 + k[i] / 1200) ** 12 - 1) * 100
        y1 = zc.get(d, {}).get("1.0")
        return sign_at[d] > 0 and y1 is not None and y1 < ke - 1.0

    def paused(d, n):
        return chg_at[d] is not None and H.days_between(chg_at[d], d) / 30.4 >= n

    def mom6(i):
        return i >= 126 and xs[i] / xs[i - 126] > 1

    hike = flag(lambda d, i: sign_at[d] > 0)
    since = "2014-03-03"
    pos = H.positions(cal, hike)
    rot = [100.0]
    for i in range(1, len(cal)):
        a = s if pos[i] else l
        rot.append(rot[-1] * a[i] / a[i - 1] * (1 - 0.002 * abs(pos[i] - pos[i - 1])))
    items = [
        ("G02", s, hike, 0.002, l), ("G16", s, hike, 0.001, None), ("G17", rot, [(cal[0], 1.0)], 0.001, None),
        ("G03", l, flag(ahead), 0.001, None),
        ("G18", l, flag(lambda d, i: ahead(d, i) or sign_at[d] < 0), 0.001, None),
        ("G19", mid, flag(lambda d, i: ahead(d, i) or sign_at[d] < 0), 0.001, None),
    ]
    for hid, n in (("B23", 2), ("B24", 3), ("B25", 4), ("B26", 6)):
        items.append((hid, l, flag(lambda d, i, n=n: sign_at[d] < 0 or paused(d, n)), 0.001, None))
    items.append(("B27", l, flag(lambda d, i: sign_at[d] < 0 and mom6(i)), 0.001, None))
    items.append(("B28", l, flag(lambda d, i: sign_at[d] < 0 or mom6(i)), 0.001, None))
    rows = []
    for hid, asset, sig, cost, base in items:
        m = H.evaluate(cal, asset, key, sig, since, periods, cost=cost, money=base)
        h = H.find(reg, hid)
        if h["status"] == "planned":
            H.record(reg, hid, m)
        rows.append([hid, h["status"], m["annual"], m["premium"], m["max_dd"], m["p"], m["premium_late"],
                     "%d/%d" % (m["legs_won"], m["legs"]), int(sig[-1][1])])
    H.save(reg_path, reg)
    print(C.table(rows, ["id", "статус", "годовых", "премия", "просадка", "p", "задержка", "заходы", "сейчас"]))
    print("Размер семейства:", H.family_size(reg, "bonds-rate"))
    months = [d for d, v in dict(items[3][2]).items() if v]
    print("Месяцы сигнала G03:", " ".join(x[:7] for x in months))
