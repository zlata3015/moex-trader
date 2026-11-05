"""Данные Московской биржи через ISS (iss.moex.com) — бесплатно, без ключа.

Важно: без подписки ISS отдаёт рыночные данные с задержкой ~15 минут.
Для торговли внутри дня в реальном времени используйте свечи Т-Инвестиций
(data_source: auto/tinvest + токен), см. tinvest.py.
"""
import csv
import io
import json
import os
import time
from datetime import datetime, timedelta

import common as C

ISS = "https://iss.moex.com/iss"

# вид инструмента -> (engine, market, board по умолчанию)
KINDS = {
    "shares":   ("stock", "shares", "TQBR"),
    "etf":      ("stock", "shares", "TQTF"),
    "bonds":    ("stock", "bonds", "TQOB"),      # ОФЗ; корпоративные — TQCB
    "corp":     ("stock", "bonds", "TQCB"),
    "futures":  ("futures", "forts", "RFUD"),
    "currency": ("currency", "selt", "CETS"),
    "index":    ("stock", "index", "SNDX"),
}

# Таймфреймы: имя -> (интервал ISS, минут в баре для пересборки или None)
TF = {
    "1m": (1, 1), "5m": (1, 5), "10m": (10, 10), "15m": (1, 15), "30m": (10, 30),
    "1h": (60, 60), "4h": (60, 240), "1d": (24, None), "1w": (7, None), "1M": (31, None),
}
INTRADAY = {"1m", "5m", "10m", "15m", "30m", "1h", "4h"}

_session_cache = {}


def _get(path, params=None):
    p = {"iss.meta": "off"}
    p.update(params or {})
    return C.http_json(ISS + path, params=p)


def table(js, block):
    """Блок ISS {columns, data} -> список словарей."""
    b = js.get(block) or {}
    cols = b.get("columns") or []
    return [dict(zip(cols, row)) for row in (b.get("data") or [])]


# ---------------------------------------------------------------- инструменты

def _cache_path():
    return C.tpath("cache", "resolve.json")


def resolve(symbol, kind=None):
    """Тикер -> {secid, engine, market, board, kind}. Для фьючерсов понимает код актива: 'Si' -> ближайший SiZ6."""
    symbol = symbol.strip()
    if kind in KINDS and kind != "futures":
        e, m, b = KINDS[kind]
        return {"secid": symbol.upper() if kind != "currency" else symbol, "engine": e, "market": m,
                "board": b, "kind": kind}
    cache = C.load_json(_cache_path(), {}) or {}
    today = C.now_msk().strftime("%Y-%m-%d")
    hit = cache.get(symbol)
    if hit and hit.get("date") == today:
        return hit
    res = None
    if kind != "futures":
        try:
            js = _get("/securities/%s.json" % symbol, {"iss.only": "boards"})
            boards = table(js, "boards")
            prim = [b for b in boards if b.get("is_primary") == 1] or [b for b in boards if b.get("is_traded") == 1]
            if prim:
                b = prim[0]
                res = {"secid": b["secid"], "engine": b["engine"], "market": b["market"],
                       "board": b["boardid"], "kind": _kind_of(b["engine"], b["market"], b["boardid"])}
        except C.NetError as e:
            if getattr(e, "status", None) not in (404,):
                raise
    if res is None:
        fut = nearest_future(symbol)
        if fut:
            res = {"secid": fut["SECID"], "engine": "futures", "market": "forts", "board": "RFUD",
                   "kind": "futures", "asset": fut.get("ASSETCODE")}
    if res is None:
        raise C.NetError("Инструмент %r не найден на Мосбирже. Попробуйте: scan.py find %s" % (symbol, symbol))
    res["date"] = today
    cache[symbol] = res
    C.save_json(_cache_path(), cache)
    return res


def _kind_of(engine, market, board):
    if engine == "futures":
        return "futures"
    if engine == "currency":
        return "currency"
    if market == "index":
        return "index"
    if market == "bonds":
        return "bonds"
    if board in ("TQTF", "TQIF"):
        return "etf"
    return "shares"


def search(query, limit=15):
    js = _get("/securities.json", {"q": query, "limit": limit, "is_trading": 1})
    return table(js, "securities")


def board_snapshot(kind="shares"):
    """Все инструменты режима торгов: справочник + рыночные данные, одним запросом."""
    e, m, b = KINDS[kind]
    js = _get("/engines/%s/markets/%s/boards/%s/securities.json" % (e, m, b),
              {"iss.only": "securities,marketdata"})
    sec = {r["SECID"]: r for r in table(js, "securities")}
    for r in table(js, "marketdata"):
        if r["SECID"] in sec:
            md = {("MD_" + k if k in sec[r["SECID"]] and k != "SECID" else k): v for k, v in r.items()}
            sec[r["SECID"]].update(md)
    return list(sec.values())


def nearest_future(asset, on_date=None):
    on = (on_date or C.now_msk()).strftime("%Y-%m-%d")
    rows = [r for r in board_snapshot("futures")
            if (r.get("ASSETCODE") or "").upper() == asset.upper() and (r.get("LASTTRADEDATE") or "") >= on]
    if not rows:
        return None
    rows.sort(key=lambda r: r["LASTTRADEDATE"])
    # если до экспирации ближайшего < 3 дней — берём следующий
    if len(rows) > 1:
        d0 = datetime.strptime(rows[0]["LASTTRADEDATE"], "%Y-%m-%d")
        if (d0 - datetime.strptime(on, "%Y-%m-%d")).days < 3:
            return rows[1]
    return rows[0]


def spec(symbol, kind=None):
    """Параметры инструмента для расчёта: лот, шаг цены, стоимость шага, ГО, номинал, НКД, последняя цена."""
    r = resolve(symbol, kind)
    js = _get("/engines/%s/markets/%s/boards/%s/securities/%s.json" % (r["engine"], r["market"], r["board"], r["secid"]),
              {"iss.only": "securities,marketdata"})
    s = (table(js, "securities") or [{}])[0]
    md = (table(js, "marketdata") or [{}])[0]
    last = None
    for k in ("LAST", "MARKETPRICE", "LCURRENTPRICE", "CURRENTVALUE", "SETTLEPRICE"):
        if md.get(k):
            last = md[k]
            break
    last = last or s.get("PREVPRICE") or s.get("PREVSETTLEPRICE") or s.get("PREVWAPRICE") or s.get("PREVLEGALCLOSEPRICE")
    out = {
        "symbol": r["secid"], "kind": r["kind"], "board": r["board"],
        "name": s.get("SHORTNAME") or s.get("SECNAME"),
        "lot": 1 if r["kind"] == "futures" else int(s.get("LOTSIZE") or 1),
        "minstep": float(s.get("MINSTEP") or 0.01),
        "decimals": s.get("DECIMALS"),
        "last": float(last) if last else None,
        "prev": s.get("PREVPRICE") or s.get("PREVSETTLEPRICE"),
        "turnover": md.get("VALTODAY") or md.get("VALTODAY_RUR") or md.get("VALUE"),
        "change_pct": md.get("LASTTOPREVPRICE") or md.get("LASTCHANGEPRCNT"),
        "updated": md.get("UPDATETIME") or md.get("SYSTIME"),
    }
    if r["kind"] == "futures":
        out.update({
            "stepprice": float(s.get("STEPPRICE") or 0) or None,
            "initial_margin": float(s.get("INITIALMARGIN") or 0) or None,
            "high_limit": s.get("HIGHLIMIT"), "low_limit": s.get("LOWLIMIT"),
            "last_trade_date": s.get("LASTTRADEDATE"), "asset": s.get("ASSETCODE"),
            "open_interest": md.get("OPENPOSITION"), "lot_volume": s.get("LOTVOLUME"),
        })
    if r["kind"] == "bonds":
        out.update({
            "facevalue": float(s.get("FACEVALUE") or 1000), "accrued": float(s.get("ACCRUEDINT") or 0),
            "matdate": s.get("MATDATE"), "coupon": s.get("COUPONVALUE"), "next_coupon": s.get("NEXTCOUPON"),
            "yield": md.get("YIELD"),
        })
    return out


# ---------------------------------------------------------------- свечи

def _iss_candles(r, interval, start, end):
    path = "/engines/%s/markets/%s/boards/%s/securities/%s/candles.json" % (
        r["engine"], r["market"], r["board"], r["secid"])
    out, pos = [], 0
    while True:
        js = _get(path, {"from": start.strftime("%Y-%m-%d"), "till": end.strftime("%Y-%m-%d"),
                         "interval": interval, "start": pos})
        rows = table(js, "candles")
        if not rows:
            break
        for x in rows:
            out.append({"t": x["begin"][:16], "o": float(x["open"]), "h": float(x["high"]),
                        "l": float(x["low"]), "c": float(x["close"]), "v": float(x.get("volume") or 0),
                        "val": float(x.get("value") or 0)})
        pos += len(rows)
        if len(rows) < 100:
            break
        time.sleep(0.05)
    return out


def resample(bars, minutes):
    """Пересборка минуток в N-минутные бары (границы от полуночи МСК)."""
    if not bars or minutes in (None, 1):
        return bars
    out, cur, key = [], None, None
    for b in bars:
        d, t = b["t"][:10], b["t"][11:16]
        m = int(t[:2]) * 60 + int(t[3:5])
        k = (d, m // minutes)
        if k != key:
            if cur:
                out.append(cur)
            start = (m // minutes) * minutes
            cur = dict(b)
            cur["t"] = "%s %02d:%02d" % (d, start // 60, start % 60)
            key = k
        else:
            cur["h"] = max(cur["h"], b["h"])
            cur["l"] = min(cur["l"], b["l"])
            cur["c"] = b["c"]
            cur["v"] += b["v"]
            cur["val"] = cur.get("val", 0) + b.get("val", 0)
    if cur:
        out.append(cur)
    return out


def default_days(tf):
    return {"1m": 3, "5m": 10, "10m": 20, "15m": 25, "30m": 40, "1h": 90, "4h": 200,
            "1d": 500, "1w": 1500, "1M": 3650}[tf]


def candles(symbol, tf="5m", days=None, end=None, kind=None, source=None, file=None):
    """Свечи OHLCV. Возвращает список {t:'YYYY-MM-DD HH:MM', o,h,l,c,v} по возрастанию времени."""
    if tf not in TF:
        raise ValueError("Таймфрейм %s не поддерживается. Есть: %s" % (tf, ", ".join(TF)))
    if file:
        bars = load_file(file)
        mins = TF[tf][1]
        return resample(bars, mins) if mins and tf in INTRADAY else bars
    cfg = C.config()
    end = end or C.now_msk()
    days = days or default_days(tf)
    start = end - timedelta(days=days)
    src = source or cfg.get("data_source", "auto")
    if src in ("tinvest", "auto") and tf in INTRADAY:
        import tinvest
        if tinvest.has_token():
            try:
                return tinvest.candles(symbol, tf, start, end, kind=kind)
            except Exception as e:  # падаем на ISS, но честно говорим
                if src == "tinvest":
                    raise
                import sys
                sys.stderr.write("[данные] Т-Инвестиции недоступны (%s), беру ISS с задержкой ~15 мин\n" % e)
    r = resolve(symbol, kind)
    interval, mins = TF[tf]
    bars = _iss_candles(r, interval, start, end)
    if tf in INTRADAY and mins and mins != interval:
        bars = resample(bars, mins)
    return bars


def load_file(path):
    """Свечи из файла: JSON ответа ISS, наш JSON-список или CSV (экспорт Финама или date,time,open,high,low,close,volume)."""
    with open(path, "rb") as f:
        raw = f.read()
    if path.endswith(".gz"):
        import gzip
        raw = gzip.decompress(raw)
    text = raw.decode("utf-8-sig", "replace").strip()
    if text.startswith("{") or text.startswith("["):
        js = json.loads(text)
        if isinstance(js, list):
            return js
        rows = table(js, "candles")
        return [{"t": x["begin"][:16], "o": float(x["open"]), "h": float(x["high"]), "l": float(x["low"]),
                 "c": float(x["close"]), "v": float(x.get("volume") or 0)} for x in rows]
    sep = ";" if text.count(";") > text.count(",") else ","
    lines = text.splitlines()
    while lines and not any(k in lines[0].lower() for k in ("open", "<open>", "откр")):
        lines.pop(0)   # CSV из ISS начинается со строки "candles" и пустой строки
    text = "\n".join(lines)
    rd = csv.reader(io.StringIO(text), delimiter=sep)
    head = [h.strip("<> ").lower() for h in next(rd)]
    idx = {h: i for i, h in enumerate(head)}

    def g(row, *names):
        for n in names:
            if n in idx:
                return row[idx[n]]
        return None
    out = []
    for row in rd:
        if not row:
            continue
        d = g(row, "date", "дата", "begin", "datetime")
        t = g(row, "time", "время") or ""
        d = d.strip()
        if len(d) == 8 and d.isdigit():
            d = "%s-%s-%s" % (d[:4], d[4:6], d[6:])
        elif "." in d and len(d.split(" ")[0]) == 10:
            dd = d.split(" ")[0].split(".")
            d = "%s-%s-%s%s" % (dd[2], dd[1], dd[0], d[10:])
        t = t.strip()
        if t and len(t) >= 4 and t.isdigit():
            t = "%s:%s" % (t[:2], t[2:4]) if len(t) in (4, 6) else t
        ts = (d[:10] + " " + (t[:5] if t else (d[11:16] if len(d) > 11 else "00:00"))).strip()
        f = lambda v: float(str(v).replace(",", ".")) if v not in (None, "") else 0.0
        out.append({"t": ts, "o": f(g(row, "open", "откр")), "h": f(g(row, "high", "макс")),
                    "l": f(g(row, "low", "мин")), "c": f(g(row, "close", "закр")),
                    "v": f(g(row, "vol", "volume", "объем", "объём"))})
    out.sort(key=lambda b: b["t"])
    return out


def sessions_split(bars):
    """Группировка внутридневных баров по датам: {date: [bars]}."""
    out = {}
    for b in bars:
        out.setdefault(b["t"][:10], []).append(b)
    return out


def last_price(symbol, kind=None):
    """Последняя цена: Т-Инвестиции (реальное время), иначе ISS (задержка)."""
    import tinvest
    if tinvest.has_token():
        try:
            p = tinvest.last_price(symbol, kind=kind)
            if p:
                return p, "tinvest"
        except Exception:
            pass
    return spec(symbol, kind)["last"], "iss"


# ---------------------------------------------------------------- корпоративные события

def dividends(symbol):
    """История дивидендов: ISS (если отдаёт), иначе таблица smart-lab.ru.
    -> [{date: дата закрытия реестра, last_day: последний день покупки под дивиденд, value, currency}]."""
    try:
        js = _get("/securities/%s/dividends.json" % symbol)
        rows = table(js, "dividends")
        if rows:
            return [{"date": r.get("registryclosedate"), "value": r.get("value"), "currency": r.get("currencyid")}
                    for r in rows]
    except C.NetError:
        pass
    return smartlab_dividends(symbol)


def smartlab_dividends(symbol):
    import html as _html
    import re
    raw = C.http("https://smart-lab.ru/q/%s/dividend/" % symbol, headers={"User-Agent": "Mozilla/5.0"}, retries=1)
    text = raw.decode("utf-8", "replace")
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S):
        cells = [_html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
        if len(cells) >= 5 and cells[0].upper() == symbol.upper() and re.match(r"\d\d\.\d\d\.\d{4}", cells[1]):
            val = re.sub(r"[^\d,\.]", "", cells[4]).replace(",", ".")
            if not val:
                continue
            iso = lambda x: "%s-%s-%s" % (x[6:10], x[3:5], x[0:2]) if re.match(r"\d\d\.\d\d\.\d{4}", x) else None
            out.append({"last_day": iso(cells[1]), "date": iso(cells[2]) or iso(cells[1]), "value": float(val),
                        "currency": "RUB", "source": "smart-lab"})
    out.sort(key=lambda d: d["date"] or "")
    return out


def next_dividend(symbol, today=None):
    today = (today or C.now_msk()).strftime("%Y-%m-%d")
    try:
        fut = [d for d in dividends(symbol) if (d["date"] or "") >= today]
    except C.NetError:
        return None
    fut.sort(key=lambda d: d["date"])
    return fut[0] if fut else None


def description(symbol):
    js = _get("/securities/%s.json" % symbol, {"iss.only": "description"})
    return {r["name"]: r["value"] for r in table(js, "description")}


def indices_of(symbol):
    js = _get("/securities/%s/indices.json" % symbol)
    return [r.get("SECID") or r.get("secid") for r in table(js, "indices")
            if (r.get("till") or r.get("TILL") or "9999") >= C.now_msk().strftime("%Y-%m-%d")]


def sitenews(limit=50):
    js = _get("/sitenews.json", {"limit": limit})
    return table(js, "sitenews")


def adjust_splits(bars, divs=None, threshold=2.5):
    """Учёт дроблений и консолидаций: скачок открытия к прошлому закрытию больше чем в threshold раз
    (на Мосбирже за день такого не бывает) считается сменой номинала; вся история до него пересчитывается.
    divs (список {date, value}) пересчитываются тем же коэффициентом. -> (bars, divs, [(дата, коэффициент)])."""
    bars = [dict(b) for b in bars]
    divs = [dict(d) for d in (divs or [])]
    events = []
    for i in range(len(bars) - 1, 0, -1):
        prev, cur = bars[i - 1]["c"], bars[i]["o"]
        if prev <= 0 or cur <= 0:
            continue
        ratio = cur / prev
        if ratio > threshold or ratio < 1 / threshold:
            f = round(ratio) if ratio > 1 else 1.0 / round(1 / ratio)
            date = bars[i]["t"][:10]
            for b in bars[:i]:
                for k in ("o", "h", "l", "c"):
                    b[k] *= f
                b["v"] = b.get("v", 0) / f
            for d in divs:
                if d.get("value") and (d.get("date") or "") < date:
                    d["value"] *= f
            events.append((date, f))
    return bars, divs, events


def issuer(secid):
    """Эмитент бумаги из справочника ISS: {issuer: id, issuer_name, issuer_inn}."""
    try:
        rows = search(secid, limit=10)
    except C.NetError:
        return None
    for r in rows:
        if (r.get("secid") or "").upper() == secid.upper():
            return {"issuer": r.get("emitent_id"), "issuer_name": r.get("emitent_title"), "issuer_inn": r.get("emitent_inn")}
    return None
