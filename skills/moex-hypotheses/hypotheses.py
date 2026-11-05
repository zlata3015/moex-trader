#!/usr/bin/env python3
"""moex-hypotheses: реестр гипотез и единая проверка.

  hypotheses.py list [--status rejected] [--family bonds-rate]   все гипотезы, включая опровергнутые
  hypotheses.py show B01                                         правило, результат, причины
  hypotheses.py add G01 --claim "..." --family equities --rule "..." [--kind frequent] [--source "..."]
  hypotheses.py check B01 --asset a.csv --signal s.csv --key key.csv --since 2015-01-01   проверить и записать
  hypotheses.py check G02 ... --base long.csv --cost 0.2         сравнить не с деньгами, а с другим активом
  hypotheses.py signals                                          только сигналы, допущенные к рекомендациям
  hypotheses.py promote B01 sandbox                              следующий этап после confirmed
  hypotheses.py reject G05 --reason "..." --doc документ          перенести отказ из другой проверки
  hypotheses.py run-cycle                                        гипотезы о цикле ставки G02, G03 и варианты
  hypotheses.py run-bonds [--dry]                                воспроизвести проверку облигаций
"""
import argparse
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "moex-setup", "lib"))
import common as C  # noqa: E402
import hypo as H  # noqa: E402

REG = os.environ.get("MOEX_HYPOTHESES") or os.path.join(HERE, "registry.json")
PERIODS = [("2014-2019", "2014", "2019"), ("2020-2022", "2020", "2022"), ("2023-2026", "2023", "2026")]


def read_series(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 2 or not row[0][:4].isdigit():
                continue
            out.append((row[0][:10], float(row[1].replace(",", "."))))
    return sorted(out)


def cmd_list(a):
    reg = H.load(REG)
    rows = []
    for h in reg["hypotheses"]:
        if (a.status and h["status"] != a.status) or (a.family and h["family"] != a.family):
            continue
        r = h["result"] or {}
        rows.append([h["id"], h["status"], h["family"], r.get("premium", ""), r.get("p", ""), r.get("max_dd", ""), h["claim"][:60]])
    print(C.table(rows, ["id", "статус", "семейство", "премия", "p", "просадка", "утверждение"]))
    count = {}
    for h in reg["hypotheses"]:
        count[h["status"]] = count.get(h["status"], 0) + 1
    print("Всего %d: %s" % (len(reg["hypotheses"]), ", ".join("%s %d" % kv for kv in sorted(count.items()))))


def cmd_show(a):
    h = H.find(H.load(REG), a.id)
    if not h:
        C.die("нет гипотезы " + a.id)
    C.print_json(h)


def cmd_add(a):
    reg = H.load(REG)
    try:
        h = H.add(reg, a.id, a.claim, a.family, a.rule, a.kind, a.source)
    except ValueError as e:
        C.die(str(e))
    H.save(REG, reg)
    print("Записано %s, в семействе %s теперь %d" % (h["id"], h["family"], H.family_size(reg, h["family"])))


def cmd_check(a):
    reg = H.load(REG)
    asset = read_series(a.asset)
    cal = [d for d, _ in asset]
    base = None
    if a.base:
        base = H.step_values(cal, read_series(a.base))
        i = next(j for j, v in enumerate(base) if v is not None)
        base[:i] = [base[i]] * i
    m = H.evaluate(cal, [v for _, v in asset], read_series(a.key), read_series(a.signal), a.since, PERIODS,
                   cost=a.cost / 100, money=base)
    try:
        h = H.record(reg, a.id, m)
    except ValueError as e:
        C.die(str(e))
    H.save(REG, reg)
    print("%s: %s\n%s" % (h["id"], h["status"], "; ".join(h["reasons"])))
    C.print_json(m)


def cmd_signals(a):
    t = H.tradable(H.load(REG))
    if not t:
        print("Допущенных сигналов нет. Опираться на гипотезы реестра в рекомендации нельзя.")
    for h in t:
        print(h["id"], h["status"], h["claim"])


def cmd_promote(a):
    reg = H.load(REG)
    try:
        H.promote(reg, a.id, a.status)
    except ValueError as e:
        C.die(str(e))
    H.save(REG, reg)
    print(a.id, a.status)


def cmd_bonds(a):
    import bonds_rate
    bonds_rate.main(REG, PERIODS, dry=a.dry)


def cmd_cycle(a):
    import bonds_rate
    bonds_rate.cycle(REG, PERIODS)


def cmd_reject(a):
    reg = H.load(REG)
    try:
        H.reject_external(reg, a.id, a.reason, a.doc)
    except ValueError as e:
        C.die(str(e))
    H.save(REG, reg)
    print(a.id, "rejected")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list"); p.add_argument("--status"); p.add_argument("--family"); p.set_defaults(f=cmd_list)
    p = sub.add_parser("show"); p.add_argument("id"); p.set_defaults(f=cmd_show)
    p = sub.add_parser("add"); p.add_argument("id"); p.add_argument("--claim", required=True)
    p.add_argument("--family", required=True); p.add_argument("--rule", required=True)
    p.add_argument("--kind", default="rare", choices=["rare", "frequent"]); p.add_argument("--source", default="")
    p.set_defaults(f=cmd_add)
    p = sub.add_parser("check"); p.add_argument("id"); p.add_argument("--asset", required=True)
    p.add_argument("--signal", required=True); p.add_argument("--key", required=True)
    p.add_argument("--base", help="ряд сравнения вместо денег: вне сигнала держим его")
    p.add_argument("--since", default="2015-01-01"); p.add_argument("--cost", type=float, default=0.1, help="%% на сторону")
    p.set_defaults(f=cmd_check)
    p = sub.add_parser("signals"); p.set_defaults(f=cmd_signals)
    p = sub.add_parser("promote"); p.add_argument("id"); p.add_argument("status", choices=["sandbox", "live"]); p.set_defaults(f=cmd_promote)
    p = sub.add_parser("run-bonds"); p.add_argument("--dry", action="store_true"); p.set_defaults(f=cmd_bonds)
    p = sub.add_parser("run-cycle"); p.set_defaults(f=cmd_cycle)
    p = sub.add_parser("reject"); p.add_argument("id"); p.add_argument("--reason", required=True)
    p.add_argument("--doc", required=True); p.set_defaults(f=cmd_reject)
    a = ap.parse_args()
    if not os.path.exists(REG):
        C.die("нет реестра " + REG)
    a.f(a)


if __name__ == "__main__":
    main()
