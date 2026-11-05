#!/usr/bin/env python3
"""moex-setup: настройка и проверка пакета.

  setup.py init                  создать trader/config.json с настройками по умолчанию
  setup.py check                 проверить доступ к Мосбирже и Т-Инвестициям (токен не показывается)
  setup.py config                показать действующие настройки
  setup.py set fee_pct 0.04      поменять одну настройку
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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init"); p.add_argument("--force", action="store_true"); p.add_argument("--account", type=float)
    p.set_defaults(fn=cmd_init)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    sub.add_parser("config").set_defaults(fn=cmd_config)
    p = sub.add_parser("set"); p.add_argument("key"); p.add_argument("value"); p.set_defaults(fn=cmd_set)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
