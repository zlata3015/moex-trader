#!/usr/bin/env python3
"""moex-setup: настройка и проверка пакета.

  setup.py init                  создать trader/config.json с настройками по умолчанию
  setup.py check                 проверить доступ к Мосбирже и Т-Инвестициям (токен не показывается)
  setup.py config                показать действующие настройки
  setup.py set fee_pct 0.04      поменять одну настройку
  setup.py strategy              какая стратегия действует, результаты по стратегиям
  setup.py strategy risky --month 2026-12    выбор на месяц, до первой сделки месяца
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
import common as C  # noqa: E402


def cmd_init(a):
    p = C.tpath("config.json")
    if os.path.exists(p) and not a.force:
        print("Уже есть %s (перезаписать: --force)" % p)
        return
    cfg = dict(C.DEFAULT_CONFIG)
    if a.account:
        cfg["account_size"] = a.account
    C.save_json(p, cfg)
    print("Создан %s. Главное проверить: account_size, max_loss_per_trade, daily_loss_limit, fee_pct (ваш тариф)." % p)


def cmd_check(a):
    import moex
    import tinvest
    ok = True
    try:
        s = moex.spec("SBER")
        print("✓ Мосбиржа ISS доступна: SBER %s (обновлено %s; без подписки данные с задержкой ~15 мин)" % (C.px(s["last"]), s.get("updated")))
    except C.NetError as e:
        ok = False
        print("✗ Мосбиржа ISS недоступна: %s" % e)
    cfg = C.config()
    for sandbox in ([True] if cfg["paper"] else [False, True]):
        name = "песочница" if sandbox else "боевой API"
        if not tinvest.has_token(sandbox):
            print("• Токен Т-Инвестиций (%s) не найден — заявки и данные в реальном времени недоступны. "
                  "Это нормально, если вы пока только анализируете." % name)
            continue
        try:
            accs = tinvest.accounts(sandbox)
            print("✓ Т-Инвестиции (%s): токен работает, счетов %d%s" % (name, len(accs),
                  (": " + ", ".join(x["id"] for x in accs)) if accs else " — откройте счёт песочницы: broker.py sandbox-open"))
        except C.NetError as e:
            ok = False
            print("✗ Т-Инвестиции (%s): %s" % (name, e))
    print("Папка состояния: %s" % C.trader_dir())
    print("Счёт: %s" % ("ПЕСОЧНИЦА (paper: true)" if cfg["paper"] else "РЕАЛЬНЫЙ (paper: false)"))
    print("Стратегия месяца: %s" % C.STRATEGY_RU[cfg["strategy"]])
    sys.exit(0 if ok else 1)


def cmd_config(a):
    C.print_json(C.config())


def cmd_set(a):
    p = C.tpath("config.json")
    cfg = C.load_json(p, {}) or {}
    try:
        v = json.loads(a.value)
    except json.JSONDecodeError:
        v = a.value
    if a.key not in C.DEFAULT_CONFIG:
        C.die("Нет такой настройки: %s" % a.key)
    cfg[a.key] = v
    C.save_json(p, cfg)
    print("%s = %s" % (a.key, json.dumps(v, ensure_ascii=False)))


def strategy_results(month):
    out = {}
    for t in C.load_json(C.tpath("journal.json"), []) or []:
        if (t.get("closed") or "")[:7] == month:
            k = t.get("strategy", "moderate")
            out[k] = out.get(k, 0) + (t.get("pnl") or 0)
    return out


def cmd_strategy(a):
    now = C.now_msk(a.now)
    this = now.strftime("%Y-%m")
    y, m = int(this[:4]), int(this[5:])
    prev = "%d-%02d" % (y - 1, 12) if m == 1 else "%d-%02d" % (y, m - 1)
    res = strategy_results(prev)
    print("Результат за %s: умеренная %s, рискованная %s. Фонд сверьте в ежемесячном разборе." % (
        prev, C.rub(res.get("moderate", 0), 0), C.rub(res.get("risky", 0), 0)))
    if not a.name:
        print("Сейчас действует: %s" % C.STRATEGY_RU[C.strategy_of(this)])
        return
    month = a.month or this
    if month < this:
        C.die("Прошлый месяц менять нельзя.")
    if a.name == "risky" and not C.config()["paper"]:
        C.die("Рискованная на реальный счёт не выбирается до этапов 3 и 4.")
    log = C.load_json(C.tpath("strategy.json"), {}) or {}
    ev = (C.load_json(C.tpath("state", "broker.json"), {}) or {}).get("events", [])
    if month == this and log.get(month, C.strategy_of(month)) != a.name and any(
            e.get("type") == "placed" and not e.get("closing") and (e.get("at") or "").startswith(month) for e in ev):
        C.die("В этом месяце сделки уже были: внутри месяца стратегия не меняется.")
    if a.name == "risky" and res.get("moderate", 0) < 0:
        print("⚠ Прошлый месяц умеренной убыточен: выбор рискованной похож на отыгрывание.")
    if res.get(a.name, 0) > 0 and res.get(a.name, 0) >= max(res.values()):
        print("⚠ Эта стратегия лучшая за прошлый месяц: проверьте, что это не погоня за прибылью.")
    log[month] = a.name
    C.save_json(C.tpath("strategy.json"), log)
    print("Записано: %s — %s." % (month, C.STRATEGY_RU[a.name]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init"); p.add_argument("--force", action="store_true"); p.add_argument("--account", type=float)
    p.set_defaults(fn=cmd_init)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    sub.add_parser("config").set_defaults(fn=cmd_config)
    p = sub.add_parser("set"); p.add_argument("key"); p.add_argument("value"); p.set_defaults(fn=cmd_set)
    p = sub.add_parser("strategy"); p.add_argument("name", nargs="?", choices=C.STRATEGIES); p.add_argument("--month")
    p.add_argument("--now"); p.set_defaults(fn=cmd_strategy)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
