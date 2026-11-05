#!/usr/bin/env python3
"""moex-news: новости по бумагам из списка наблюдения — RSS российских деловых СМИ, новости биржи, дивиденды.

  news.py --tickers SBER,GAZP --hours 12
  news.py --market --hours 6                 рынок в целом: ЦБ, санкции, нефть, рубль
  news.py --tickers SBER --json
"""
import argparse
import email.utils
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "moex-setup", "lib"))
import common as C  # noqa: E402
import moex  # noqa: E402

FEEDS = [
    ("Интерфакс", "https://www.interfax.ru/rss.asp"),
    ("РБК", "https://rssexport.rbc.ru/rbcnews/news/30/full.rss"),
    ("Коммерсантъ", "https://www.kommersant.ru/RSS/news.xml"),
    ("Smart-Lab", "https://smart-lab.ru/news/rss/"),
    ("ТАСС Экономика", "https://tass.ru/rss/v2.xml"),
]

ALIASES = {
    "SBER": ["сбер", "сбербанк", "греф"], "SBERP": ["сбер", "сбербанк"], "GAZP": ["газпром"], "LKOH": ["лукойл"],
    "ROSN": ["роснефть", "сечин"], "NVTK": ["новатэк", "михельсон"], "GMKN": ["норникель", "норильский никель", "потанин"],
    "YDEX": ["яндекс"], "TCSG": ["т-банк", "тинькофф", "т-технологии"], "T": ["т-банк", "т-технологии", "тинькофф"],
    "VTBR": ["втб", "костин"], "MGNT": ["магнит"], "X5": ["x5", "пятёрочка", "пятерочка"], "FIVE": ["x5"],
    "PLZL": ["полюс"], "CHMF": ["северсталь"], "NLMK": ["нлмк"], "MAGN": ["ммк", "магнитогорск"],
    "TATN": ["татнефть"], "SNGS": ["сургутнефтегаз"], "SNGSP": ["сургутнефтегаз"], "ALRS": ["алроса"],
    "MOEX": ["мосбиржа", "московская биржа"], "AFLT": ["аэрофлот"], "MTSS": ["мтс"], "RTKM": ["ростелеком"],
    "PHOR": ["фосагро"], "IRAO": ["интер рао"], "HYDR": ["русгидро"], "OZON": ["озон", "ozon"], "PIKK": ["пик"],
    "SMLT": ["самолет", "самолёт"], "AFKS": ["афк система", "систем"], "SELG": ["селигдар"], "RUAL": ["русал"],
    "TRNFP": ["транснефть"], "BSPB": ["банк санкт-петербург"], "CBOM": ["мкб"], "HEAD": ["хэдхантер", "hh.ru"],
    "POSI": ["позитив", "positive technologies"], "ASTR": ["астра"], "MTLR": ["мечел"], "FLOT": ["совкомфлот"],
    "SVCB": ["совкомбанк"], "UPRO": ["юнипро"], "FEES": ["россети", "фск"], "ENPG": ["эн+"], "LENT": ["лента"],
    "Si": ["доллар", "рубл", "курс"], "CR": ["юан"], "BR": ["нефть", "brent", "опек"], "RI": ["индекс ртс"],
    "MX": ["индекс мосбирж", "imoex"], "GD": ["золот"], "NG": ["газ"],
}

TAGS = [
    ("дивиденды", r"дивиденд|отсечк|реестр акционер"),
    ("отчётность", r"мсфо|рсбу|отчёт|отчет|выручк|чистая прибыль|ebitda|квартал"),
    ("ставка ЦБ", r"ключев\w* ставк|банк росси|набиуллин|цб рф|центробанк"),
    ("санкции", r"санкци|ограничени|ofac|евросоюз"),
    ("сделка", r"слиян|поглощен|покупк\w* дол|продаж\w* дол|сделк|приобрет"),
    ("байбэк", r"buyback|байбэк|выкуп акций|обратн\w* выкуп"),
    ("размещение", r"spo|ipo|допэмисси|размещени"),
    ("суд/регулятор", r"суд|иск|фас|прокуратур|штраф|национализ"),
    ("нефть", r"нефт|brent|urals|опек"),
    ("рубль", r"курс рубл|доллар|юан|валют"),
    ("прогноз", r"прогноз|guidance|ориентир|рекомендац|целев\w* цен"),
]


def parse_feed(raw, source):
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", it.findtext("description") or "")
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate")
        dt = None
        if pub:
            try:
                d = email.utils.parsedate_to_datetime(pub)
                dt = (d - d.utcoffset() + timedelta(hours=3)).replace(tzinfo=None) if d.utcoffset() is not None else d
            except (TypeError, ValueError):
                dt = None
        out.append({"source": source, "title": title, "text": desc.strip()[:500], "link": link, "time": dt})
    return out


def fetch_all(extra):
    items, errors = [], []
    for name, url in FEEDS + [(u, u) for u in extra]:
        try:
            if os.environ.get("MOEX_TRADER_FIXTURES"):
                p = os.path.join(os.environ["MOEX_TRADER_FIXTURES"], C.fixture_key(url, {}) + ".xml")
                raw = open(p, "rb").read() if os.path.exists(p) else b"<rss/>"
            else:
                raw = C.http(url, headers={"Accept": "application/rss+xml, application/xml, text/xml"}, retries=1, timeout=15)
            items += parse_feed(raw, name)
        except C.NetError as e:
            errors.append("%s: %s" % (name, str(e)[:120]))
    try:
        for n in moex.sitenews(30):
            items.append({"source": "Мосбиржа", "title": n.get("title", ""), "text": "", "link": "https://www.moex.com/n%s" % n.get("id"),
                          "time": C.parse_dt(n["published_at"]) if n.get("published_at") else None})
    except (C.NetError, KeyError, ValueError):
        pass
    return items, errors


def tags_of(text):
    t = text.lower()
    return [name for name, rx in TAGS if re.search(rx, t)]


def aliases(sym):
    al = list(ALIASES.get(sym, []))
    if not al:
        try:
            nm = (moex.spec(sym).get("name") or "").lower()
            nm = re.sub(r"\b(ао|ап|пао|ао|ап|-|ап\.|ао\.)\b", " ", nm).strip()
            if nm:
                al.append(nm.split()[0][:7])
        except C.NetError:
            pass
    return al + [sym.lower()] if len(sym) > 2 else al


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickers", default=""); ap.add_argument("--hours", type=float, default=12)
    ap.add_argument("--market", action="store_true"); ap.add_argument("--json", action="store_true")
    ap.add_argument("--now")
    a = ap.parse_args()
    cfg = C.config()
    now = C.now_msk(a.now)
    since = now - timedelta(hours=a.hours)
    items, errors = fetch_all(cfg.get("sec_news_feeds") or [])
    items = [i for i in items if i["time"] is None or i["time"] >= since]
    for i in items:
        i["tags"] = tags_of(i["title"] + " " + i["text"])
    report = {"at": C.fmt_dt(now), "hours": a.hours, "tickers": {}, "errors": errors}
    tickers = [t.strip() for t in a.tickers.split(",") if t.strip()]
    for sym in tickers:
        al = aliases(sym)
        hits = [i for i in items if any(re.search(r"\b" + re.escape(x), (i["title"] + " " + i["text"]).lower()) for x in al)]
        hits.sort(key=lambda i: i["time"] or now, reverse=True)
        extra = {}
        try:
            sp = moex.spec(sym)
            extra["move"] = sp.get("change_pct")
            extra["last"] = sp.get("last")
            if sp["kind"] == "shares":
                extra["dividend"] = moex.next_dividend(sym, now)
        except C.NetError:
            pass
        report["tickers"][sym] = {"news": hits, **extra}
    if a.market or not tickers:
        keys = {"ставка ЦБ", "санкции", "нефть", "рубль"}
        report["market"] = [i for i in items if keys & set(i["tags"])][:25]
    if a.json:
        C.print_json(report)
        return
    for sym, r in report["tickers"].items():
        head = "%s" % sym
        if r.get("last"):
            head += " · %s (%s за день)" % (C.px(r["last"]), C.pct(r.get("move")))
        print(head)
        d = r.get("dividend")
        if d:
            print("  💰 ближайшая отсечка по дивидендам: %s, %s на акцию" % (d["date"], C.rub(d["value"])))
        if not r["news"]:
            print("  новостей за %s ч не найдено" % C.num(a.hours, 0))
        for i in r["news"][:12]:
            print("  %s  [%s] %s%s" % (i["time"].strftime("%d.%m %H:%M") if i["time"] else "     ", i["source"], i["title"],
                                       ("  #" + " #".join(i["tags"])) if i["tags"] else ""))
        print()
    if report.get("market"):
        print("Рынок в целом:")
        for i in report["market"]:
            print("  %s  [%s] %s  #%s" % (i["time"].strftime("%d.%m %H:%M") if i["time"] else "     ", i["source"], i["title"], " #".join(i["tags"])))
    if errors:
        print("\nНе удалось прочитать: " + "; ".join(errors))
    C.save_json(C.tpath("news.json"), report)


if __name__ == "__main__":
    main()
