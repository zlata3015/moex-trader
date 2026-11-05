"""Т-Инвестиции (T-Invest API, REST-шлюз): свечи в реальном времени, цены, счета, заявки.

Токен никогда не передаётся через чат. Скрипт берёт его из переменной окружения
TINVEST_TOKEN (для песочницы можно отдельный TINVEST_SANDBOX_TOKEN) или из файла
~/.config/moex-trader/tinvest_token (права 600). Токен нигде не печатается.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

import common as C

HOSTS = {
    False: ["https://invest-public-api.tbank.ru/rest/", "https://invest-public-api.tinkoff.ru/rest/"],
    True: ["https://sandbox-invest-public-api.tbank.ru/rest/", "https://sandbox-invest-public-api.tinkoff.ru/rest/"],
}
PKG = "tinkoff.public.invest.api.contract.v1."

CLASS_FOR_KIND = {"shares": ["TQBR"], "etf": ["TQTF", "TQIF"], "bonds": ["TQOB", "TQCB", "TQIR"],
                  "futures": ["SPBFUT"], "currency": ["CETS"]}
KIND_ENUM = {"shares": "INSTRUMENT_TYPE_SHARE", "etf": "INSTRUMENT_TYPE_ETF", "bonds": "INSTRUMENT_TYPE_BOND",
             "futures": "INSTRUMENT_TYPE_FUTURES", "currency": "INSTRUMENT_TYPE_CURRENCY"}
INTERVAL = {"1m": ("CANDLE_INTERVAL_1_MIN", 1), "5m": ("CANDLE_INTERVAL_5_MIN", 1),
            "10m": ("CANDLE_INTERVAL_10_MIN", 1), "15m": ("CANDLE_INTERVAL_15_MIN", 1),
            "30m": ("CANDLE_INTERVAL_30_MIN", 2), "1h": ("CANDLE_INTERVAL_HOUR", 7),
            "4h": ("CANDLE_INTERVAL_4_HOUR", 30), "1d": ("CANDLE_INTERVAL_DAY", 365)}


def _token(sandbox=False):
    names = (["TINVEST_SANDBOX_TOKEN"] if sandbox else []) + ["TINVEST_TOKEN"]
    for n in names:
        if os.environ.get(n):
            return os.environ[n].strip()
    base = os.path.expanduser("~/.config/moex-trader")
    files = (["tinvest_sandbox_token"] if sandbox else []) + ["tinvest_token"]
    for f in files:
        p = os.path.join(base, f)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as fh:
                t = fh.read().strip()
                if t:
                    return t
    return None


def has_token(sandbox=False):
    return bool(_token(sandbox))


def call(service, method, body=None, sandbox=False):
    tok = _token(sandbox)
    if not tok:
        raise C.NetError("Нет токена Т-Инвестиций. Создайте токен в приложении (Настройки → Токены T-Invest API) "
                         "и положите его в переменную окружения TINVEST_TOKEN или в файл "
                         "~/.config/moex-trader/tinvest_token. В чат токен не вставляйте.")
    last = None
    for host in HOSTS[bool(sandbox)]:
        try:
            return C.http_json(host + PKG + service + "/" + method, data=body or {},
                               headers={"Authorization": "Bearer " + tok}, retries=1)
        except C.NetError as e:
            last = e
            if getattr(e, "status", None):   # сервер ответил — второй хост не поможет
                raise
    raise last


# ---------------------------------------------------------------- деньги

def q2f(q):
    if q is None:
        return None
    if isinstance(q, (int, float)):
        return float(q)
    return int(q.get("units") or 0) + int(q.get("nano") or 0) / 1e9


def f2q(x):
    units = int(x) if x >= 0 else -int(-x)
    nano = int(round((x - units) * 1e9))
    if abs(nano) >= 1000000000:
        units += nano // 1000000000
        nano = nano % 1000000000
    return {"units": str(units), "nano": nano}


# ---------------------------------------------------------------- инструменты

def instrument(symbol, kind=None):
    """Тикер Мосбиржи -> инструмент Т-Инвестиций {uid, figi, ticker, classCode, lot, minstep}."""
    cache_p = C.tpath("cache", "tinvest.json")
    cache = C.load_json(cache_p, {}) or {}
    key = "%s|%s" % (symbol, kind or "")
    if key in cache:
        return cache[key]
    if kind is None or (kind == "futures" and not any(ch.isdigit() for ch in symbol)):
        import moex
        r = moex.resolve(symbol, kind)
        symbol, kind = r["secid"], r["kind"]
    body = {"query": symbol, "apiTradeAvailableFlag": True}
    if kind in KIND_ENUM:
        body["instrumentKind"] = KIND_ENUM[kind]
    found = call("InstrumentsService", "FindInstrument", body).get("instruments", [])
    exact = [i for i in found if (i.get("ticker") or "").upper() == symbol.upper()]
    pref = CLASS_FOR_KIND.get(kind or "shares", [])
    exact.sort(key=lambda i: (pref.index(i["classCode"]) if i.get("classCode") in pref else 99))
    if not exact:
        raise C.NetError("Т-Инвестиции не нашли %s для торговли через API" % symbol)
    i = exact[0]
    full = call("InstrumentsService", "GetInstrumentBy",
                {"idType": "INSTRUMENT_ID_TYPE_UID", "id": i["uid"]}).get("instrument", {})
    out = {"uid": i["uid"], "figi": i.get("figi"), "ticker": i.get("ticker"), "classCode": i.get("classCode"),
           "name": i.get("name"), "lot": int(full.get("lot") or 1), "minstep": q2f(full.get("minPriceIncrement")),
           "kind": kind, "short_enabled": full.get("shortEnabledFlag")}
    cache[key] = out
    C.save_json(cache_p, cache)
    return out


def _to_msk(ts):
    dt = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return (dt + timedelta(hours=3)).replace(tzinfo=None)


def _utc(dt_msk):
    return (dt_msk - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")


def candles(symbol, tf, start, end, kind=None):
    ins = instrument(symbol, kind)
    enum, chunk = INTERVAL[tf]
    out, cur = [], start
    while cur < end:
        nxt = min(end, cur + timedelta(days=chunk))
        js = call("MarketDataService", "GetCandles",
                  {"instrumentId": ins["uid"], "from": _utc(cur), "to": _utc(nxt), "interval": enum})
        for c in js.get("candles", []):
            out.append({"t": C.fmt_dt(_to_msk(c["time"])), "o": q2f(c["open"]), "h": q2f(c["high"]),
                        "l": q2f(c["low"]), "c": q2f(c["close"]),
                        "v": float(c.get("volume") or 0) * ins["lot"], "complete": c.get("isComplete", True)})
        cur = nxt
    seen, uniq = set(), []
    for b in sorted(out, key=lambda b: b["t"]):
        if b["t"] not in seen:
            seen.add(b["t"])
            uniq.append(b)
    return uniq


def last_price(symbol, kind=None):
    ins = instrument(symbol, kind)
    js = call("MarketDataService", "GetLastPrices", {"instrumentId": [ins["uid"]]})
    lp = js.get("lastPrices") or []
    return q2f(lp[0].get("price")) if lp else None


# ---------------------------------------------------------------- счета

def accounts(sandbox):
    if sandbox:
        return call("SandboxService", "GetSandboxAccounts", {}, sandbox=True).get("accounts", [])
    return call("UsersService", "GetAccounts", {}).get("accounts", [])


def open_sandbox(name="moex-trader"):
    return call("SandboxService", "OpenSandboxAccount", {"name": name}, sandbox=True).get("accountId")


def sandbox_payin(account_id, amount):
    js = call("SandboxService", "SandboxPayIn",
              {"accountId": account_id, "amount": dict(f2q(amount), currency="rub")}, sandbox=True)
    return q2f(js.get("balance"))


def portfolio(account_id, sandbox):
    if sandbox:
        return call("SandboxService", "GetSandboxPortfolio", {"accountId": account_id, "currency": "RUB"}, sandbox=True)
    return call("OperationsService", "GetPortfolio", {"accountId": account_id, "currency": "RUB"})


# ---------------------------------------------------------------- заявки

def post_order(account_id, symbol, side, lots, order_type="limit", price=None, kind=None, sandbox=True, order_id=None):
    ins = instrument(symbol, kind)
    body = {"instrumentId": ins["uid"], "quantity": str(int(lots)), "accountId": account_id,
            "direction": "ORDER_DIRECTION_BUY" if side == "buy" else "ORDER_DIRECTION_SELL",
            "orderType": "ORDER_TYPE_LIMIT" if order_type == "limit" else "ORDER_TYPE_MARKET",
            "orderId": order_id or str(uuid.uuid4())}
    if order_type == "limit":
        body["price"] = f2q(float(price))
    if sandbox:
        return call("SandboxService", "PostSandboxOrder", body, sandbox=True)
    return call("OrdersService", "PostOrder", body)


def post_stop(account_id, symbol, side, lots, stop_price, stop_type="stop_loss", limit_price=None, kind=None,
              sandbox=True):
    ins = instrument(symbol, kind)
    body = {"instrumentId": ins["uid"], "quantity": str(int(lots)), "accountId": account_id,
            "stopPrice": f2q(float(stop_price)),
            "direction": "STOP_ORDER_DIRECTION_BUY" if side == "buy" else "STOP_ORDER_DIRECTION_SELL",
            "expirationType": "STOP_ORDER_EXPIRATION_TYPE_GOOD_TILL_CANCEL",
            "stopOrderType": {"stop_loss": "STOP_ORDER_TYPE_STOP_LOSS", "take_profit": "STOP_ORDER_TYPE_TAKE_PROFIT",
                              "stop_limit": "STOP_ORDER_TYPE_STOP_LIMIT"}[stop_type]}
    if limit_price is not None:
        body["price"] = f2q(float(limit_price))
    if sandbox:
        return call("SandboxService", "PostSandboxStopOrder", body, sandbox=True)
    return call("StopOrdersService", "PostStopOrder", body)


def order_state(account_id, order_id, sandbox=True):
    if sandbox:
        return call("SandboxService", "GetSandboxOrderState", {"accountId": account_id, "orderId": order_id}, sandbox=True)
    return call("OrdersService", "GetOrderState", {"accountId": account_id, "orderId": order_id})


def cancel_order(account_id, order_id, sandbox=True):
    if sandbox:
        return call("SandboxService", "CancelSandboxOrder", {"accountId": account_id, "orderId": order_id}, sandbox=True)
    return call("OrdersService", "CancelOrder", {"accountId": account_id, "orderId": order_id})


def active_orders(account_id, sandbox=True):
    if sandbox:
        return call("SandboxService", "GetSandboxOrders", {"accountId": account_id}, sandbox=True).get("orders", [])
    return call("OrdersService", "GetOrders", {"accountId": account_id}).get("orders", [])


def cancel_stop(account_id, stop_order_id, sandbox=True):
    if sandbox:
        return call("SandboxService", "CancelSandboxStopOrder", {"accountId": account_id, "stopOrderId": stop_order_id},
                    sandbox=True)
    return call("StopOrdersService", "CancelStopOrder", {"accountId": account_id, "stopOrderId": stop_order_id})
