"""Генератор офлайн-фикстур в формате ответов ISS Мосбиржи (синтетические данные, фиксированный seed).

Нужен для тестов без сети: формат JSON совпадает с iss.moex.com (iss.meta=off).
Запуск: python3 tests/make_fixtures.py
"""
import gzip
import json
import math
import os
import random
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "fixtures")
ISS = "iss.moex.com_iss_"
END = date(2026, 10, 30)
CANDLE_COLS = ["open", "close", "high", "low", "value", "volume", "begin", "end"]


def weekdays_back(n, end=END):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def save(name, obj, gz=False):
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, name + (".json.gz" if gz else ".json"))
    if gz:
        with gzip.open(p, "wt", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
    else:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)


def rnd_step(p, step):
    return round(round(p / step) * step, 6)


def intraday(rng, start_price, step, days, vol_per_min, breakout_last=False, base_volume=20000):
    rows, p = [], start_price
    for di, d in enumerate(days):
        gap = rng.gauss(0, vol_per_min * 6)
        p *= (1 + gap)
        drift = rng.gauss(0, vol_per_min * 0.08)
        for m in range(9 * 60, 18 * 60 + 50):
            t = datetime(d.year, d.month, d.day, m // 60, m % 60)
            mins = m - 9 * 60
            u = 1.6 if mins < 30 else (1.3 if mins > 560 else 1.0)  # U-образная волатильность
            mu = drift
            if breakout_last and di == len(days) - 1:
                if mins < 15:
                    mu = 0
                elif 15 <= mins < 40:
                    mu = vol_per_min * 0.9
            r = rng.gauss(mu, vol_per_min * u)
            o = p
            c = p * (1 + r)
            hi = max(o, c) * (1 + abs(rng.gauss(0, vol_per_min * 0.5)))
            lo = min(o, c) * (1 - abs(rng.gauss(0, vol_per_min * 0.5)))
            o, c, hi, lo = (rnd_step(x, step) for x in (o, c, hi, lo))
            hi, lo = max(hi, o, c), min(lo, o, c)
            vol = int(base_volume * u * (0.5 + rng.random()) * (3 if breakout_last and di == len(days) - 1 and 15 <= mins < 40 else 1))
            rows.append([o, c, hi, lo, round(vol * c, 2), vol, t.strftime("%Y-%m-%d %H:%M:%S"),
                         (t + timedelta(seconds=59)).strftime("%Y-%m-%d %H:%M:%S")])
            p = c
    return rows


def daily(rng, start_price, step, days, vol, trend=0.0004, base_volume=30000000):
    rows, p = [], start_price
    for i, d in enumerate(days):
        r = rng.gauss(trend + 0.0015 * math.sin(i / 25), vol)
        o = p * (1 + rng.gauss(0, vol * 0.3))
        c = o * (1 + r)
        hi = max(o, c) * (1 + abs(rng.gauss(0, vol * 0.5)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, vol * 0.5)))
        o, c, hi, lo = (rnd_step(x, step) for x in (o, c, hi, lo))
        hi, lo = max(hi, o, c), min(lo, o, c)
        v = int(base_volume * (0.5 + rng.random()))
        rows.append([o, c, hi, lo, round(v * c, 2), v, d.strftime("%Y-%m-%d 00:00:00"), d.strftime("%Y-%m-%d 23:59:59")])
        p = c
    return rows


def resample10(rows):
    out, cur, key = [], None, None
    for r in rows:
        t = r[6]
        k = (t[:10], (int(t[11:13]) * 60 + int(t[14:16])) // 10)
        if k != key:
            if cur:
                out.append(cur)
            m = k[1] * 10
            cur = list(r)
            cur[6] = "%s %02d:%02d:00" % (t[:10], m // 60, m % 60)
            key = k
        else:
            cur[1] = r[1]
            cur[2] = max(cur[2], r[2])
            cur[3] = min(cur[3], r[3])
            cur[4] += r[4]
            cur[5] += r[5]
            cur[7] = r[7]
    if cur:
        out.append(cur)
    return out


def resample60(rows):
    out, cur, key = [], None, None
    for r in rows:
        k = (r[6][:10], r[6][11:13])
        if k != key:
            if cur:
                out.append(cur)
            cur = list(r)
            cur[6] = r[6][:13] + ":00:00"
            key = k
        else:
            cur[1] = r[1]
            cur[2] = max(cur[2], r[2])
            cur[3] = min(cur[3], r[3])
            cur[4] += r[4]
            cur[5] += r[5]
    if cur:
        out.append(cur)
    return out


def candles_obj(rows):
    return {"candles": {"columns": CANDLE_COLS, "data": rows}}


def boards(secid, engine, market, board):
    return {"boards": {"columns": ["secid", "boardid", "title", "board_group_id", "market_id", "market", "engine_id",
                                   "engine", "is_traded", "decimals", "history_from", "history_till", "listed_from",
                                   "listed_till", "is_primary", "currencyid"],
                       "data": [[secid, board, board, 57, 1, market, 1, engine, 1, 2, "2010-01-01", "2026-10-30",
                                 "2010-01-01", "2026-10-30", 1, "RUB"]]}}


def main():
    rng = random.Random(20261030)
    d40 = weekdays_back(40)
    d420 = weekdays_back(420)
    inst = {
        # secid: (engine, market, board, price, step, lot, vol_min, daily_vol, kind)
        "SBER": ("stock", "shares", "TQBR", 300.0, 0.01, 10, 0.0009, 0.016),
        "GAZP": ("stock", "shares", "TQBR", 130.0, 0.01, 10, 0.0011, 0.019),
        "LKOH": ("stock", "shares", "TQBR", 6900.0, 0.5, 1, 0.0008, 0.014),
        "YDEX": ("stock", "shares", "TQBR", 4100.0, 0.5, 1, 0.0012, 0.021),
        "SiZ6": ("futures", "forts", "RFUD", 84000.0, 1, 1, 0.0005, 0.009),
        "IMOEX": ("stock", "index", "SNDX", 2700.0, 0.01, 1, 0.0006, 0.011),
    }
    last = {}
    for secid, (e, m, b, price, step, lot, vm, dv) in inst.items():
        base = "%sengines_%s_markets_%s_boards_%s_securities_%s_candles" % (ISS, e, m, b, secid)
        dl = daily(rng, price * 0.8, step, d420[:-40], dv)
        mins = intraday(rng, dl[-1][1], step, d40, vm, breakout_last=(secid in ("SBER", "SiZ6")),
                        base_volume=20000 if lot > 1 else 300)
        for d in d40:  # последние 40 дневок — из минуток, чтобы таймфреймы совпадали
            ds = d.strftime("%Y-%m-%d")
            day = [r for r in mins if r[6].startswith(ds)]
            dl.append([day[0][0], day[-1][1], max(r[2] for r in day), min(r[3] for r in day),
                       round(sum(r[4] for r in day), 2), sum(r[5] for r in day), ds + " 00:00:00", ds + " 23:59:59"])
        save(base + "__interval-24", candles_obj(dl))
        # недельки
        weeks, cur, wk = [], None, None
        for r in dl:
            dd = datetime.strptime(r[6][:10], "%Y-%m-%d").date()
            k = dd.isocalendar()[:2]
            if k != wk:
                if cur:
                    weeks.append(cur)
                cur, wk = list(r), k
            else:
                cur[1] = r[1]; cur[2] = max(cur[2], r[2]); cur[3] = min(cur[3], r[3]); cur[4] += r[4]; cur[5] += r[5]
        weeks.append(cur)
        save(base + "__interval-7", candles_obj(weeks))
        save(base + "__interval-1", candles_obj(mins), gz=True)
        save(base + "__interval-10", candles_obj(resample10(mins)), gz=True)
        save(base + "__interval-60", candles_obj(resample60(mins)))
        last[secid] = mins[-1][1]
        save("%ssecurities_%s" % (ISS, secid), boards(secid, e, m, b))

    # снимок режима TQBR
    sec_cols = ["SECID", "BOARDID", "SHORTNAME", "PREVPRICE", "LOTSIZE", "FACEVALUE", "STATUS", "BOARDNAME",
                "DECIMALS", "SECNAME", "MINSTEP", "PREVWAPRICE", "ISSUESIZE", "ISIN", "LATNAME", "PREVLEGALCLOSEPRICE",
                "CURRENCYID", "SECTYPE", "LISTLEVEL"]
    md_cols = ["SECID", "BOARDID", "BID", "OFFER", "OPEN", "LOW", "HIGH", "LAST", "VALTODAY", "VOLTODAY",
               "LASTTOPREVPRICE", "UPDATETIME", "ISSUECAPITALIZATION", "SYSTIME"]
    names = {"SBER": ("Сбербанк", "Сбербанк России ПАО ао", 21586948000),
             "GAZP": ("ГАЗПРОМ ао", "\"Газпром\" (ПАО) ао", 23673512900),
             "LKOH": ("ЛУКОЙЛ", "НК ЛУКОЙЛ (ПАО) - ао", 692865762),
             "YDEX": ("Яндекс", "МКПАО \"Яндекс\" ао", 395000000)}
    sd, mdd = [], []
    for s, (nm, full, issue) in names.items():
        e, m, b, price, step, lot, vm, dv = inst[s]
        lp = last[s]
        sd.append([s, "TQBR", nm, round(lp * 0.99, 2), lot, 1, "A", "Т+", 2, full, step, lp, issue, "RU000", s, lp,
                   "SUR", "1", 1])
        mdd.append([s, "TQBR", lp - step, lp + step, lp * 0.99, lp * 0.985, lp * 1.02, lp, 9.5e9 if s != "YDEX" else 4e9,
                    1000000, 1.0, "18:49:59", issue * lp, "2026-10-30 18:50:00"])
        spec = {"securities": {"columns": sec_cols, "data": [sd[-1]]}, "marketdata": {"columns": md_cols, "data": [mdd[-1]]}}
        save("%sengines_stock_markets_shares_boards_TQBR_securities_%s" % (ISS, s), spec)
    save("%sengines_stock_markets_shares_boards_TQBR_securities" % ISS,
         {"securities": {"columns": sec_cols, "data": sd}, "marketdata": {"columns": md_cols, "data": mdd}})

    # фьючерсы
    fcols = ["SECID", "BOARDID", "SHORTNAME", "SECNAME", "PREVSETTLEPRICE", "DECIMALS", "MINSTEP", "LASTTRADEDATE",
             "LASTDELDATE", "SECTYPE", "LATNAME", "ASSETCODE", "PREVOPENPOSITION", "LOTVOLUME", "INITIALMARGIN",
             "HIGHLIMIT", "LOWLIMIT", "STEPPRICE", "LASTSETTLEPRICE", "PREVPRICE"]
    fmd = ["SECID", "BOARDID", "LAST", "OPEN", "HIGH", "LOW", "VALTODAY", "VOLTODAY", "OPENPOSITION", "UPDATETIME",
           "LASTTOPREVPRICE", "SETTLEPRICE"]
    lp = last["SiZ6"]
    frows = [["SiZ6", "RFUD", "Si-12.26", "Фьючерсный контракт Si-12.26", lp, 0, 1, "2026-12-17", "2026-12-17", "Si",
              "Si-12.26", "Si", 1500000, 1000, 12500.0, lp * 1.07, lp * 0.93, 1.0, lp, lp],
             ["SiH7", "RFUD", "Si-3.27", "Фьючерсный контракт Si-3.27", lp * 1.03, 0, 1, "2027-03-18", "2027-03-18", "Si",
              "Si-3.27", "Si", 200000, 1000, 12900.0, lp * 1.1, lp * 0.96, 1.0, lp * 1.03, lp * 1.03]]
    fm = [["SiZ6", "RFUD", lp, lp, lp * 1.01, lp * 0.99, 8e10, 900000, 1500000, "18:49:59", 0.3, lp],
          ["SiH7", "RFUD", lp * 1.03, lp * 1.03, lp * 1.04, lp * 1.02, 4e9, 40000, 200000, "18:49:59", 0.3, lp * 1.03]]
    save("%sengines_futures_markets_forts_boards_RFUD_securities" % ISS,
         {"securities": {"columns": fcols, "data": frows}, "marketdata": {"columns": fmd, "data": fm}})
    save("%sengines_futures_markets_forts_boards_RFUD_securities_SiZ6" % ISS,
         {"securities": {"columns": fcols, "data": [frows[0]]}, "marketdata": {"columns": fmd, "data": [fm[0]]}})
    # «Si» без номера — нет такого secid: пустые boards -> поиск ближайшего фьючерса
    save("%ssecurities_Si" % ISS, {"boards": {"columns": ["secid"], "data": []}})

    # дивиденды
    save("%ssecurities_SBER_dividends" % ISS, {"dividends": {
        "columns": ["secid", "isin", "registryclosedate", "value", "currencyid"],
        "data": [["SBER", "RU0009029540", "2024-07-11", 33.3, "RUB"], ["SBER", "RU0009029540", "2025-07-18", 34.84, "RUB"],
                 ["SBER", "RU0009029540", "2026-07-17", 37.6, "RUB"], ["SBER", "RU0009029540", "2026-11-05", 5.0, "RUB"]]}})
    save("%ssecurities_GAZP_dividends" % ISS, {"dividends": {"columns": ["secid", "isin", "registryclosedate", "value", "currencyid"], "data": []}})
    for s in ("LKOH", "YDEX"):
        save("%ssecurities_%s_dividends" % (ISS, s), {"dividends": {"columns": ["secid", "isin", "registryclosedate", "value", "currencyid"],
                                                                   "data": [[s, "RU", "2026-06-01", 500.0 if s == "LKOH" else 80.0, "RUB"]]}})
    print("фикстуры записаны в", OUT)


if __name__ == "__main__":
    main()
