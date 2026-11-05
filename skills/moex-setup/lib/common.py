"""Общие вещи пакета moex-trader: папки, конфиг, время МСК, HTTP, форматирование.

Только стандартная библиотека Python 3.8+.
"""
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))  # Москва живёт без перехода на летнее время с 2014 года
UA = "moex-trader/1.0 (+https://github.com/; python-urllib)"

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
SKILLS_DIR = os.path.dirname(os.path.dirname(LIB_DIR))

# ---------------------------------------------------------------- папки и файлы

def trader_dir():
    """Папка с состоянием: $TRADER_DIR или ./trader в текущей папке проекта."""
    d = os.environ.get("TRADER_DIR") or os.path.join(os.getcwd(), "trader")
    os.makedirs(d, exist_ok=True)
    return d


def tpath(*parts):
    p = os.path.join(trader_dir(), *parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        die("Файл %s повреждён (не JSON): %s" % (path, e))


def save_json(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def die(msg, code=2):
    sys.stderr.write("ОШИБКА: " + msg + "\n")
    sys.exit(code)


# ---------------------------------------------------------------- конфиг

DEFAULT_CONFIG = {
    "account_size": 100000,          # ₽, размер торгового счёта
    "max_loss_per_trade": "0.5%",    # максимальный убыток на сделку: "0.5%" счёта или сумма в ₽
    "daily_loss_limit": "2%",        # дневной лимит убытка (реализованного): торговля останавливается
    "weekly_loss_limit": "4%",       # недельный лимит убытка
    "max_open_risk": "3%",           # суммарный риск всех открытых позиций и заявок до их стопов
    "dd_half_pct": 10,               # просадка от максимума капитала, после которой объём вдвое меньше
    "dd_stop_pct": 15,               # просадка, после которой пауза и разбор
    "corr_threshold": 0.7,           # корреляция дневных доходностей, выше которой позиции считаются одной
    "corr_group_risk_mult": 1.0,     # общий риск связанных позиций — не больше N рисков одной сделки
    "issuer_limit_pct": 5,           # на одного корпоративного эмитента облигаций не больше N% счёта
    "max_trades_per_day": 5,
    "max_open_positions": 3,
    "max_position_pct": 30,          # % счёта в одной позиции (для фьючерсов — по ГО)
    "min_reward_risk": 2.0,
    "min_turnover_rub": 100000000,   # ₽, минимальный дневной оборот бумаги для сделки
    "fee_pct": 0.05,                 # % комиссия брокера за сторону (тариф «Трейдер» ~0,05%)
    "futures_fee_rub": 2.0,          # ₽ за контракт за сторону (брокер + биржа, примерно)
    "slippage_ticks": 1,             # проскальзывание в шагах цены на каждую сторону
    "min_target_cost_multiple": 4,   # цель должна быть дальше 4 издержек сделки туда-обратно
    "regime_filter": True,           # лонг только если IMOEX выше EMA50 дневной (свинг) / не в сильном падении
    "require_backtest": True,        # блокировать сетапы без бэктеста или с отрицательным мат. ожиданием
    "require_trend_alignment": True, # сделка только по тренду недели и дня (месяц не против)
    "trail_atr": 3.0,                # подтягиваемый стоп: экстремум с входа минус K ATR
    "max_hold_days_position": 60,    # максимум дней в позиции для торговли по тренду
    "allow_short": False,
    "allow_futures": False,
    "allow_bonds": True,
    "allow_currency": False,
    "allow_evening_session": False,  # торговать ли в вечернюю сессию
    "allow_morning_session": False,  # торговать ли в утреннюю сессию
    "allow_weekend": False,          # торговать ли в выходные сессии
    "key_rate": None,                # ключевая ставка вручную, если cbr.ru недоступен
    "block_dividend_gap": True,      # блокировать свинг-лонг через дату отсечки
    "paper": True,                   # песочница Т-Инвестиций по умолчанию
    "tinvest_account_id": "",        # счёт Т-Инвестиций (или песочницы)
    "data_source": "auto",           # auto | iss | tinvest
    "sessions": {
        # Время МСК. С 14.09.2026 основная сессия фондового рынка 09:00–19:00,
        # утренняя 06:50–09:00, вечерняя 19:00–23:50. Проверьте актуальность на moex.com.
        "shares":   {"morning": ["06:50", "09:00"], "main": ["09:00", "18:50"], "evening": ["19:00", "23:50"],
                      "auction_open_min": 10, "auction_close": ["18:40", "18:50"]},
        "bonds":    {"main": ["09:00", "18:50"], "evening": ["19:00", "23:50"], "auction_open_min": 10},
        "currency": {"main": ["08:50", "19:00"], "evening": ["19:00", "23:50"], "auction_open_min": 10},
        "futures":  {"morning": ["07:00", "09:00"], "main": ["09:00", "18:50"], "evening": ["19:05", "23:50"],
                      "clearing": [["14:00", "14:05"], ["18:50", "19:05"]], "auction_open_min": 0},
        "index":    {"main": ["09:00", "18:50"]},
    },
    "orb_start": "09:00",            # от какого времени считать диапазон открытия
    "flat_by": "18:30",              # к этому времени внутридневная позиция закрыта
    "no_entry_after": "18:00",       # новые внутридневные входы не позже
    "sec_news_feeds": [],            # дополнительные RSS-ленты
}


def deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def config():
    user = load_json(tpath("config.json"), {}) or {}
    return deep_merge(DEFAULT_CONFIG, user)


def money_limit(value, account_size):
    """'1%' -> 1% счёта; 1500 -> 1500."""
    if isinstance(value, str) and value.strip().endswith("%"):
        return account_size * float(value.strip()[:-1].replace(",", ".")) / 100.0
    return float(value)


# ---------------------------------------------------------------- время

def now_msk(override=None):
    """Текущее время МСК (naive). override: 'YYYY-MM-DD HH:MM' — для тестов и разборов."""
    if override:
        return parse_dt(override)
    return datetime.now(MSK).replace(tzinfo=None)


def parse_dt(s):
    s = s.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            pass
    raise ValueError("Не понимаю дату/время: %r" % s)


def hm(s):
    """'18:40' -> минуты от полуночи."""
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def minutes_of(dt):
    return dt.hour * 60 + dt.minute


def fmt_dt(dt):
    return dt.strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------- сессии

def session_of(kind, dt, cfg=None):
    """Какая сессия идёт в момент dt: morning/main/evening/clearing/auction/closed."""
    cfg = cfg or config()
    s = cfg["sessions"].get(kind) or cfg["sessions"]["shares"]
    m = minutes_of(dt)
    for a, b in s.get("clearing", []):
        if hm(a) <= m < hm(b):
            return "clearing"
    if "auction_close" in s and hm(s["auction_close"][0]) <= m < hm(s["auction_close"][1]):
        return "auction"
    main = s.get("main")
    if main and hm(main[0]) <= m < hm(main[1]):
        if m < hm(main[0]) + int(s.get("auction_open_min", 0)):
            return "auction"
        return "main"
    for name in ("morning", "evening"):
        rng = s.get(name)
        if rng and hm(rng[0]) <= m < hm(rng[1]):
            return name
    return "closed"


def is_weekday(dt):
    return dt.weekday() < 5


# ---------------------------------------------------------------- сеть

class NetError(RuntimeError):
    pass


NET_HINT = (
    "Нет доступа к %s. Скрипты пакета ходят в интернет напрямую (iss.moex.com, API Т-Инвестиций). "
    "Если вы работаете в облачной среде Claude с ограниченной сетью — запустите команду на своём "
    "компьютере (подключите компьютер к чату) или выгрузите данные в CSV и передайте через --file."
)


def http(url, params=None, data=None, headers=None, timeout=25, retries=2, method=None):
    fx = os.environ.get("MOEX_TRADER_FIXTURES")
    if fx:
        for ext in (".xml", ".txt"):
            p = os.path.join(fx, fixture_key(url, params or {}) + ext)
            if os.path.exists(p):
                with open(p, "rb") as f:
                    return f.read()
        raise NetError("нет фикстуры %s" % fixture_key(url, params or {}))
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    h = {"User-Agent": UA, "Accept": "application/json, */*"}
    h.update(headers or {})
    body = None
    if data is not None:
        body = data if isinstance(data, bytes) else json.dumps(data).encode("utf-8")
        h.setdefault("Content-Type", "application/json")
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=h, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            payload = e.read()
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                last = e
                continue
            err = NetError("HTTP %s от %s: %s" % (e.code, urllib.parse.urlsplit(url).netloc,
                                                  payload[:400].decode("utf-8", "replace")))
            err.status = e.code
            err.payload = payload
            raise err
        except (urllib.error.URLError, OSError) as e:
            last = e
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
    raise NetError(NET_HINT % urllib.parse.urlsplit(url).netloc + " (%s)" % last)


def http_json(url, params=None, **kw):
    fx = os.environ.get("MOEX_TRADER_FIXTURES")
    if fx:
        return _fixture(fx, url, params or {}, kw.get("data"))
    raw = http(url, params=params, **kw)
    return json.loads(raw.decode("utf-8"))


def fixture_key(url, params):
    u = urllib.parse.urlsplit(url)
    key = (u.netloc + u.path).replace("/", "_").replace(".json", "")
    if "interval" in params:
        key += "__interval-%s" % params["interval"]
    return key


def _fixture(folder, url, params, data):
    """Офлайн-режим для тестов: ответы берутся из файлов папки MOEX_TRADER_FIXTURES."""
    if int(params.get("start", 0) or 0) > 0:
        return {"candles": {"columns": [], "data": []}}
    key = fixture_key(url, params)
    if data and isinstance(data, dict) and data.get("query"):
        key += "__" + str(data["query"])
    for name in (key + ".json", key + ".json.gz"):
        p = os.path.join(folder, name)
        if os.path.exists(p):
            if p.endswith(".gz"):
                import gzip
                with gzip.open(p, "rt", encoding="utf-8") as f:
                    return json.load(f)
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    raise NetError("нет фикстуры %s" % key)


# ---------------------------------------------------------------- форматирование

def rub(x, digits=2):
    if x is None:
        return "—"
    s = "{:,.{d}f}".format(float(x), d=digits).replace(",", " ").replace(".", ",")
    return s + " ₽"


def num(x, digits=2):
    if x is None:
        return "—"
    return "{:,.{d}f}".format(float(x), d=digits).replace(",", " ").replace(".", ",")


def px(x):
    """Цена с разумным числом знаков: 83 465 · 324,28 · 11,4625 · 0,0532."""
    if x is None:
        return "—"
    x = float(x)
    a = abs(x)
    d = 0 if a >= 10000 else 1 if a >= 1000 else 2 if a >= 10 else 4 if a >= 0.1 else 6
    return num(x, d)


def pct(x, digits=2):
    return "—" if x is None else (num(x, digits) + " %")


def round_step(price, step, mode="nearest"):
    """Округление цены к шагу цены инструмента."""
    if not step:
        return price
    q = price / step
    import math
    if mode == "down":
        q = math.floor(q + 1e-9)
    elif mode == "up":
        q = math.ceil(q - 1e-9)
    else:
        q = round(q)
    decimals = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return round(q * step, decimals + 2)


def fingerprint(order):
    keys = ("symbol", "side", "lots", "entry", "stop", "target", "type", "date")
    s = json.dumps({k: order.get(k) for k in keys}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def table(rows, headers):
    """Простая текстовая таблица."""
    rows = [[("" if c is None else str(c)) for c in r] for r in rows]
    w = [len(h) for h in headers]
    for r in rows:
        for i, c in enumerate(r):
            w[i] = max(w[i], len(c))
    line = "  ".join(h.ljust(w[i]) for i, h in enumerate(headers))
    out = [line, "  ".join("-" * x for x in w)]
    for r in rows:
        out.append("  ".join(c.ljust(w[i]) for i, c in enumerate(r)))
    return "\n".join(out)


def print_json(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))
