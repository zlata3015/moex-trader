"""Рейтинг сигналов, отдельно на покупку и на продажу. Сделку открывает только высокий рейтинг (require_high_rank).
Рейтинг не заменяет moex-risk: высокий рейтинг не отменяет ни одного лимита.

K — число технических подтверждений от 0 до 3: цена за EMA20, гистограмма MACD, RSI в зоне импульса.
На истории выбор по K не улучшил счёт с 2024 года (Q01 отклонён), разница K=3 и остальных — кандидат (Q02).
A — число тех же сигналов против сделки. Новость — отметка о компании: +1 хорошая, 0 нейтральная, −1 плохая,
записанная до заявки. Для продажи знак меняется. Истории новостей нет, польза проверяется только вперёд (Q07).
Высокий: новость в сторону сделки, K от 1, A=0. Низкий: новость против или A от 1. Остальное обычный.
"""
from datetime import datetime, timedelta
from urllib.parse import urlparse

import common as C
import ta

TIERS = ("высокий", "обычный", "низкий")
NEWS_DAYS = 3


def confirmations(bars, direction="long"):
    """Подтверждения на закрытии последнего бара."""
    c = [b["c"] for b in bars]
    e = ta.ema(c, 20)[-1]
    h = ta.macd(c)[2][-1]
    r = ta.rsi(c)[-1]
    if direction == "long":
        out = {"EMA20": e is not None and c[-1] > e, "MACD": h is not None and h > 0, "RSI": r is not None and 50 <= r <= 70}
    else:
        out = {"EMA20": e is not None and c[-1] < e, "MACD": h is not None and h < 0, "RSI": r is not None and 30 <= r <= 50}
    return out


PRIMARY = ("moex.com", "cbr.ru", "e-disclosure.ru")   # биржа, регулятор, официальное раскрытие компании
AGENCIES = ("interfax.ru", "finam.ru", "vedomosti.ru", "kommersant.ru", "1prime.ru", "tass.ru", "rbc.ru")


def source_of(url):
    host = (urlparse(url).hostname or "").lower()
    return next((d for d in PRIMARY + AGENCIES if host == d or host.endswith("." + d)), None)


def check_sources(mark, url, url2=None):
    """Плюс или минус: один первичный источник или два разных издания. Форум и блог отметку не дают."""
    a, b = source_of(url), source_of(url2) if url2 else None
    if mark == 0 or a in PRIMARY or b in PRIMARY:
        return
    if a and b and a != b:
        return
    raise ValueError("для отметки 1 или -1 нужен первичный источник (биржа, ЦБ, e-disclosure) или два разных издания из списка")


def marks():
    return C.load_json(C.tpath("news_marks.json"), {}) or {}


def add_mark(symbol, mark, title, url, published, now, url2=None):
    """Отметки только добавляются: задним числом рейтинг не меняется."""
    if mark not in (-1, 0, 1):
        raise ValueError("отметка новости: -1, 0 или 1")
    if not title or not url or not published:
        raise ValueError("нужны заголовок, ссылка и дата публикации")
    if published[:10] > now.strftime("%Y-%m-%d"):
        raise ValueError("дата публикации в будущем")
    check_sources(mark, url, url2)
    m = marks()
    e = {"mark": mark, "title": title, "url": url, "url2": url2, "published": published[:10], "written": C.fmt_dt(now)}
    m.setdefault(symbol, []).append(e)
    C.save_json(C.tpath("news_marks.json"), m)
    return e


def trading_days_since(day, now):
    d, n = datetime.strptime(day, "%Y-%m-%d"), 0
    while d.date() < now.date():
        d += timedelta(days=1)
        n += d.weekday() < 5
    return n


def news_of(symbol, now):
    """Последняя отметка, записанная не позже now и не старше 3 торговых дней. Нет такой: None."""
    stamp = C.fmt_dt(now)
    for e in reversed(marks().get(symbol, [])):
        if e["written"] <= stamp:
            return e if trading_days_since(e["published"], now) <= NEWS_DAYS else None
    return None


def assess(bars, direction, symbol, now):
    conf = confirmations(bars, direction)
    against = confirmations(bars, "short" if direction == "long" else "long")
    k, a = sum(conf.values()), sum(against.values())
    n = news_of(symbol, now)
    mark = n["mark"] if n else 0
    news = mark if direction == "long" else -mark
    t = "низкий" if news < 0 or a >= 1 else "высокий" if news > 0 and k >= 1 else "обычный"
    return {"tier": t, "direction": direction, "k": k, "a": a, "confirm": conf, "against": against, "news": news,
            "news_title": n["title"] if n else None, "news_url": n["url"] if n else None,
            "status": "Q07 проверяется в песочнице, на истории запрет не подтверждён"}


def sort_key(r):
    return (TIERS.index(r["tier"]), -r["k"], r["a"], -r["news"])


def log_signal(r, setup, price, atr, day, allowed):
    """Журнал всех сигналов, включая несделанные: без него высокий рейтинг не с чем сравнить (Q07)."""
    log = C.load_json(C.tpath("signals.json"), []) or []
    key = (day, r["symbol"], r["direction"])
    if any((x["date"], x["symbol"], x["direction"]) == key for x in log):
        return
    log.append({"date": day, "strategy": C.config()["strategy"], "symbol": r["symbol"], "direction": r["direction"], "setup": setup, "tier": r["tier"], "k": r["k"],
                "a": r["a"], "news": r["news"], "price": price, "atr": atr, "allowed": allowed})
    C.save_json(C.tpath("signals.json"), log)
