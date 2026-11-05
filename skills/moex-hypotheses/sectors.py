"""Секторы и ключевая ставка. Индексы ценовые: дивиденды не учтены, сравнение только сектора с рынком."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "moex-setup", "lib"))
import common as C  # noqa: E402
import hypo as H  # noqa: E402
import bonds_rate as B  # noqa: E402


def run(reg_path, periods):
    today = C.now_msk().strftime("%d.%m.%Y")
    reg = H.load(reg_path)
    fn, mkt = B.index("MOEXFN"), B.index("IMOEX")
    key = B.key_rate(today)
    days = {d for d, _ in fn} & {d for d, _ in mkt}
    cal = sorted(d for d in days if d >= "2013-09-17")
    f, m = H.step_values(cal, fn), H.step_values(cal, mkt)
    k = H.step_values(cal, key)
    me = set(B.month_ends(cal))
    sign, sig, cuts = 0, [], []
    for i, d in enumerate(cal):
        if i and k[i - 1] is not None and k[i] != k[i - 1]:
            sign = 1 if k[i] > k[i - 1] else -1
            if sign < 0:
                cuts.append(d)
        if d in me:
            sig.append((d, 1.0 if sign < 0 else 0.0))
    g07 = H.evaluate(cal, f, key, sig, "2014-03-03", periods, cost=0.002, money=m)
    g20 = H.evaluate_events(cal, f, m, cuts, 20, periods)
    for hid, r in (("G07", g07), ("G20", g20)):
        h = H.find(reg, hid)
        if h["status"] == "planned":
            H.record(reg, hid, r)
        print(hid, h["status"], "; ".join(h["reasons"]))
        C.print_json(r)
    H.save(reg_path, reg)
    print("Семейство equities-macro:", H.family_size(reg, "equities-macro"), "| сигнал сейчас:", sig[-1])
