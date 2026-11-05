#!/usr/bin/env python3
"""moex-macro: фон для российского рынка — ключевая ставка, курсы ЦБ, индексы, нефть, режим рынка.

  macro.py              сводка
  macro.py --json
"""
import argparse
import os
import re
import sys
from datetime import timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "moex-setup", "lib"))
import common as C  # noqa: E402
import moex  # noqa: E402
import ta  # noqa: E402

CBR_DAILY = "https://www.cbr.ru/scripts/XML_daily.asp"
CBR_SOAP = "https://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx"


def cbr_rates():
    raw = C.http(CBR_DAILY, retries=1)
    text = raw.decode("windows-1251", "replace")
    date = re.search(r'Date="([^"]+)"', text)
    out = {"date": date.group(1) if date else None}
    for code in ("USD", "EUR", "CNY"):
        m = re.search(r"<CharCode>%s</CharCode>.*?<Nominal>(\d+)</Nominal>.*?<Value>([\d,]+)</Value>" % code, text, re.S)
        if m:
            out[code] = float(m.group(2).replace(",", ".")) / int(m.group(1))
    return out


def key_rate(now):
    body = ('<?xml version="1.0" encoding="utf-8"?><soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
            '<KeyRate xmlns="http://web.cbr.ru/"><fromDate>%s</fromDate><ToDate>%s</ToDate></KeyRate></soap:Body></soap:Envelope>'
            % ((now - timedelta(days=400)).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"))).encode("utf-8")
    raw = C.http(CBR_SOAP, data=body, headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": "http://web.cbr.ru/KeyRate"},
                 retries=1)
    text = raw.decode("utf-8", "replace")
    pts = re.findall(r"<DT>([^<]+)</DT>\s*<Rate>([\d.,]+)</Rate>", text)
    pts = sorted((d[:10], float(r.replace(",", "."))) for d, r in pts)
    if not pts:
        return None
    cur = pts[-1]
    changes, prev = [], None
    for d, r in pts:
        if prev is not None and r != prev:
            changes.append((d, prev, r))
        prev = r
    return {"rate": cur[1], "since": next((d for d, p, r in reversed(changes)), None), "last_change": changes[-1] if changes else None}


def regime(sym, kind, now):
    d = moex.candles(sym, "1d", days=400, end=now, kind=kind, source="iss")
    if not d:
        return None
    c = [b["c"] for b in d]
    e50, e200 = ta.ema(c, 50)[-1], ta.ema(c, 200)[-1]
    an = ta.analyze(d)
    ch = lambda n: (c[-1] / c[-1 - n] - 1) * 100 if len(c) > n else None
    return {"last": c[-1], "date": d[-1]["t"][:10], "ema50": e50, "ema200": e200, "d1": ch(1), "w1": ch(5), "m1": ch(21),
            "score": an.get("score"), "verdict": an.get("verdict"),
            "regime": ("бычий" if e200 and c[-1] > e50 > e200 else "медвежий" if e200 and c[-1] < e50 < e200 else "смешанный")}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true"); ap.add_argument("--now")
    a = ap.parse_args()
    cfg = C.config()
    now = C.now_msk(a.now)
    out = {"at": C.fmt_dt(now), "errors": []}
    try:
        out["key_rate"] = key_rate(now)
    except C.NetError as e:
        out["errors"].append("ставка ЦБ: %s" % str(e)[:100])
        out["key_rate"] = {"rate": cfg.get("key_rate"), "manual": True} if cfg.get("key_rate") else None
    try:
        out["cbr"] = cbr_rates()
    except C.NetError as e:
        out["errors"].append("курсы ЦБ: %s" % str(e)[:100])
    out["markets"] = {}
    for name, sym, kind in (("IMOEX — индекс Мосбиржи", "IMOEX", "index"), ("RGBI — индекс гособлигаций", "RGBI", "index"),
                            ("Si — фьючерс доллар/рубль", "Si", "futures"), ("BR — фьючерс Brent", "BR", "futures"),
                            ("CNYRUB_TOM — юань", "CNYRUB_TOM", "currency")):
        try:
            out["markets"][name] = regime(sym, kind, now)
        except C.NetError as e:
            out["errors"].append("%s: %s" % (sym, str(e)[:80]))
    if a.json:
        C.print_json(out)
        return
    kr = out.get("key_rate")
    if kr:
        line = "Ключевая ставка ЦБ: %s%%" % C.num(kr["rate"], 2)
        if kr.get("last_change"):
            d, p, r = kr["last_change"]
            line += " (с %s, было %s%%)" % (d, C.num(p, 2))
        if kr.get("manual"):
            line += " — из config.json, проверьте на cbr.ru"
        print(line)
    else:
        print("Ключевая ставка ЦБ: нет данных — Claude найдёт последнее решение и дату следующего заседания поиском")
    if out.get("cbr"):
        print("Курсы ЦБ на %s: " % out["cbr"].get("date") + "  ".join("%s %s" % (k, C.num(out["cbr"][k], 4))
                                                                   for k in ("USD", "EUR", "CNY") if k in out["cbr"]))
    rows = []
    for name, r in out["markets"].items():
        if r:
            rows.append([name, C.px(r["last"]), C.pct(r["d1"]), C.pct(r["w1"]), C.pct(r["m1"]), r["regime"], "%+d" % (r["score"] or 0)])
    if rows:
        print()
        print(C.table(rows, ["инструмент", "значение", "день", "неделя", "месяц", "режим (EMA50/200)", "ТА"]))
    im = out["markets"].get("IMOEX — индекс Мосбиржи")
    if im:
        print("\nРежим рынка акций: %s. %s" % (im["regime"], "Лонги по тренду — норма." if im["regime"] == "бычий" else
                                               "Лонги против падающего рынка — повышенный риск; уменьшайте размер или ждите." if im["regime"] == "медвежий"
                                               else "Рынок без явного тренда — строже отбирайте сетапы."))
    if out["errors"]:
        print("\nНе удалось получить: " + "; ".join(out["errors"]))
    C.save_json(C.tpath("macro.json"), out)


if __name__ == "__main__":
    main()
