"""Офлайн-тесты пакета moex-trader. Запуск: python3 -m unittest discover -s tests -v

Используют фикстуры в формате ответов ISS (tests/make_fixtures.py) и имитацию ответов T-Invest API.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SK = os.path.join(ROOT, "skills")
LIB = os.path.join(SK, "moex-setup", "lib")
FX = os.path.join(HERE, "fixtures")
sys.path.insert(0, LIB)
os.environ["MOEX_TRADER_FIXTURES"] = FX

import common as C  # noqa: E402
import costs  # noqa: E402
import moex  # noqa: E402
import setups as S  # noqa: E402
import sim  # noqa: E402
import ta  # noqa: E402

DAY = "2026-10-30"


def _load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _text(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def run(script, *args, env=None, ok=True):
    e = dict(os.environ)
    e.update(env or {})
    p = subprocess.run([sys.executable, os.path.join(SK, script)] + list(args), capture_output=True, text=True, env=e,
                       cwd=e.get("TRADER_DIR"))
    if ok and p.returncode != 0:
        raise AssertionError("%s %s\n%s\n%s" % (script, args, p.stdout, p.stderr))
    return p


class TestIndicators(unittest.TestCase):
    def test_sma_ema(self):
        x = [1, 2, 3, 4, 5, 6]
        self.assertEqual(ta.sma(x, 3), [None, None, 2, 3, 4, 5])
        e = ta.ema(x, 3)
        self.assertAlmostEqual(e[2], 2.0)
        self.assertAlmostEqual(e[3], 3.0)

    def test_rsi_bounds(self):
        up = list(range(1, 40))
        self.assertEqual(ta.rsi(up)[-1], 100.0)
        down = list(range(40, 1, -1))
        self.assertAlmostEqual(ta.rsi(down)[-1], 0.0)
        zig = [10, 11] * 20
        self.assertAlmostEqual(ta.rsi(zig)[-1], 50, delta=5)

    def test_atr_bollinger(self):
        h = [11] * 30
        l = [9] * 30
        c = [10] * 30
        self.assertAlmostEqual(ta.atr(h, l, c)[-1], 2.0)
        mid, up, lo, w = ta.bollinger(c)
        self.assertEqual((mid[-1], up[-1], lo[-1]), (10, 10, 10))

    def test_patterns(self):
        bars = [{"o": 10, "h": 10.2, "l": 9, "c": 9.1}] * 5 + [
            {"o": 9.5, "h": 9.6, "l": 8.9, "c": 9.0}, {"o": 8.95, "h": 10.1, "l": 8.9, "c": 10.0}]
        names = [p for p, _ in ta.patterns(bars)]
        self.assertIn("бычье поглощение", names)

    def test_pivots(self):
        p = ta.pivots(110, 90, 100)
        self.assertAlmostEqual(p["P"], 100)
        self.assertAlmostEqual(p["R1"], 110)
        self.assertAlmostEqual(p["S1"], 90)


class TestData(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TRADER_DIR"] = self.tmp

    def test_resample(self):
        bars = [{"t": "2026-10-30 09:%02d" % m, "o": m, "h": m + 1, "l": m - 1, "c": m + 0.5, "v": 1} for m in range(10)]
        r = moex.resample(bars, 5)
        self.assertEqual(len(r), 2)
        self.assertEqual(r[0]["t"], "2026-10-30 09:00")
        self.assertEqual((r[0]["o"], r[0]["h"], r[0]["l"], r[0]["c"], r[0]["v"]), (0, 5, -1, 4.5, 5))

    def test_candles_and_timeframes(self):
        end = C.parse_dt(DAY + " 18:50")
        five = moex.candles("SBER", "5m", end=end, source="iss")
        hour = moex.candles("SBER", "1h", end=end, source="iss")
        day = moex.candles("SBER", "1d", end=end, source="iss")
        self.assertEqual(five[-1]["t"], DAY + " 18:45")
        self.assertAlmostEqual(five[-1]["c"], day[-1]["c"])
        self.assertAlmostEqual(hour[-1]["c"], day[-1]["c"])

    def test_futures_by_asset(self):
        r = moex.resolve("Si")
        self.assertEqual(r["secid"], "SiZ6")
        sp = moex.spec("SiZ6")
        self.assertEqual(sp["lot"], 1)
        self.assertEqual(sp["stepprice"], 1.0)
        self.assertEqual(sp["initial_margin"], 12500.0)

    def test_finam_csv(self):
        p = os.path.join(self.tmp, "x.csv")
        with open(p, "w") as f:
            f.write("<TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>\n")
            f.write("SBER,5,20261030,100000,300.1,301,299.5,300.8,1000\n")
            f.write("SBER,5,20261030,100500,300.8,302,300.5,301.9,1500\n")
        b = moex.load_file(p)
        self.assertEqual(b[0]["t"], "2026-10-30 10:00")
        self.assertEqual(b[1]["c"], 301.9)


class TestSetupsSim(unittest.TestCase):
    def test_orb_triggers_on_fixture(self):
        end = C.parse_dt(DAY + " 18:50")
        bars = [b for b in moex.candles("SBER", "5m", end=end, source="iss") if b["t"].startswith(DAY)]
        r = S.orb(bars, 5, "09:00", 15, at=C.parse_dt(DAY + " 09:26"))
        self.assertEqual(r["status"], "сработал")
        self.assertEqual(r["trigger_time"], DAY + " 09:20")
        self.assertGreater(r["trigger_price"], r["range_high"])
        early = S.orb(bars, 5, "09:00", 15, at=C.parse_dt(DAY + " 09:12"))
        self.assertEqual(early["status"], "диапазон формируется")

    def test_run_trade_conservative(self):
        bars = [{"t": "2026-10-30 10:00", "o": 100, "h": 106, "l": 94, "c": 100}]
        self.assertEqual(sim.run_trade(bars, 100, 95, 105, True)[0], "стоп")
        gap = [{"t": "2026-10-30 10:00", "o": 90, "h": 91, "l": 89, "c": 90}]
        o, px, _ = sim.run_trade(gap, 100, 95, 105, True)
        self.assertEqual((o, px), ("стоп", 90))
        flat = [{"t": "2026-10-30 18:30", "o": 101, "h": 101, "l": 101, "c": 101}]
        self.assertEqual(sim.run_trade(flat, 100, 95, 105, True, flat_by=C.hm("18:30"))[0], "время")

    def test_costs(self):
        cfg = dict(C.DEFAULT_CONFIG)
        sh = {"kind": "shares", "minstep": 0.01, "lot": 10}
        self.assertAlmostEqual(costs.per_unit(sh, 300, cfg), 300 * 0.0005 * 2 + 0.02)
        fu = {"kind": "futures", "minstep": 1, "stepprice": 1.0}
        self.assertAlmostEqual(costs.per_unit(fu, 84000, cfg), 4.0 + 2)
        self.assertEqual(costs.rub_per_point({"kind": "bonds", "facevalue": 1000}), 10)

    def test_stats(self):
        s = sim.stats([1, -1, 2, -1])
        self.assertEqual(s["trades"], 4)
        self.assertAlmostEqual(s["avg_r"], 0.25)
        self.assertAlmostEqual(s["profit_factor"], 1.5)
        self.assertAlmostEqual(s["max_dd_r"], -1)


class TestPipeline(unittest.TestCase):
    """Полная сделка: вход → выход → машина времени → бюджет → риск → брокер (имитация) → журнал."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.env = {"TRADER_DIR": cls.tmp, "TINVEST_TOKEN": "dummy-token-never-printed"}
        with open(os.path.join(cls.tmp, "config.json"), "w") as f:
            json.dump({"require_trend_alignment": False, "require_backtest": False, "min_reward_risk": 1.5,
                       "max_loss_per_trade": 1000}, f)   # здесь проверяем механику; правила риска — в TestRiskRules
        _write_tinvest_fixtures()

    def test_chain(self):
        env = self.env
        out = run("moex-entry/entry.py", "check", "SBER", "--setup", "orb", "--now", DAY + " 09:26", env=env).stdout
        self.assertIn("СРАБОТАЛ", out)
        run("moex-exit/exit.py", "plan", "--stop", "or-mid", "--target", "1.5R", "--write", "--now", DAY + " 09:26", env=env)
        plan = _load((os.path.join(self.tmp, "plan.json")))
        self.assertLess(plan["stop"], plan["entry"])
        self.assertAlmostEqual(plan["rr"], 1.5, delta=0.01)
        tm = run("moex-timemachine/timemachine.py", "run", "--write", env=env).stdout
        self.assertIn("цель раньше стопа", tm)
        tm2 = run("moex-timemachine/timemachine.py", "run", env=env).stdout
        self.assertEqual(tm.splitlines()[2], tm2.splitlines()[2])  # повторяемость seed
        run("moex-budget/size.py", "--write", env=env)
        o = _load((os.path.join(self.tmp, "orders.json")))[0]
        self.assertGreaterEqual(o["lots"], 1)
        self.assertLessEqual(o["max_loss"], 1000)
        self.assertLessEqual(o["capital"], 30000)
        # вне сессии — BLOCK
        r = run("moex-risk/check.py", "--now", DAY + " 08:00", env=env).stdout
        self.assertIn("BLOCK", r)
        b = run("moex-broker/broker.py", "--now", DAY + " 08:01", "place", "--index", "1", env=env, ok=False)
        self.assertNotEqual(b.returncode, 0)
        # в сессии — PASS
        r = run("moex-risk/check.py", "--now", DAY + " 09:26", env=env).stdout
        self.assertIn("PASS", r)
        b = run("moex-broker/broker.py", "--now", DAY + " 09:27", "place", "--index", "1", env=env, ok=False)
        self.assertIn("Нет вашего «да»", b.stderr)
        b = run("moex-broker/broker.py", "--now", DAY + " 09:27", "approve", "--reply", "наверное", env=env).stdout
        self.assertIn("не является подтверждением", b)
        run("moex-broker/broker.py", "--now", DAY + " 09:27", "approve", "--reply", "да", env=env)
        p = run("moex-broker/broker.py", "--now", DAY + " 09:27", "place", "--index", "1", env=env)
        self.assertIn("песочница", p.stdout)
        self.assertNotIn("dummy-token", p.stdout + p.stderr)
        again = run("moex-broker/broker.py", "--now", DAY + " 09:28", "place", "--index", "1", env=env, ok=False)
        self.assertIn("уже отправлялась", again.stderr)
        run("moex-broker/broker.py", "--now", DAY + " 09:30", "status", "--index", "1", env=env)
        run("moex-broker/broker.py", "--now", DAY + " 11:00", "fill", "--symbol", "SBER", "--side", "sell", "--lots",
            str(o["lots"]), "--price", str(o["target"]), env=env)
        j = _load((os.path.join(self.tmp, "journal.json")))
        self.assertEqual(len(j), 1)
        self.assertGreater(j[0]["r"], 1.0)
        st = run("moex-journal/journal.py", "stats", env=env).stdout
        self.assertIn("orb", st)

    def test_daily_limit_blocks(self):
        tmp = tempfile.mkdtemp()
        env = {"TRADER_DIR": tmp}
        os.makedirs(os.path.join(tmp, "state"))
        with open(os.path.join(tmp, "state", "broker.json"), "w") as f:
            json.dump({"events": [{"type": "fill", "at": DAY + " 09:10", "pnl": -3100, "symbol": "GAZP"}], "approvals": {}}, f)
        run("moex-entry/entry.py", "check", "SBER", "--setup", "orb", "--now", DAY + " 09:26", env=env)
        run("moex-exit/exit.py", "plan", "--write", "--now", DAY + " 09:26", env=env)
        run("moex-budget/size.py", "--write", env=env)
        r = run("moex-risk/check.py", "--now", DAY + " 09:26", env=env).stdout
        self.assertIn("BLOCK", r)
        self.assertIn("стоп на день", r)

    def test_futures_sizing(self):
        tmp = tempfile.mkdtemp()
        env = {"TRADER_DIR": tmp}
        run("moex-entry/entry.py", "check", "SiZ6", "--setup", "orb", "--now", DAY + " 09:26", env=env)
        run("moex-exit/exit.py", "plan", "--stop", "atr:2", "--target", "2R", "--write", "--now", DAY + " 09:26", env=env)
        run("moex-budget/size.py", "--write", env=env)
        o = _load((os.path.join(tmp, "orders.json")))[0]
        self.assertEqual(o["kind"], "futures")
        self.assertLessEqual(o["capital"], 30000 + 1)   # ГО в пределах 30% счёта
        self.assertEqual(o["capital"], o["lots"] * 12500.0)


class TestTrend(unittest.TestCase):
    def _series(self, drift, n=400):
        import math
        bars, p = [], 100.0
        from datetime import date, timedelta
        d = date(2025, 1, 1)
        for i in range(n):
            o = p
            p = p * (1 + drift + 0.004 * math.sin(i / 3.0))
            bars.append({"t": d.strftime("%Y-%m-%d") + " 00:00", "o": o, "h": max(o, p) * 1.003, "l": min(o, p) * 0.997,
                         "c": p, "v": 1000})
            d += timedelta(days=1)
        return bars

    def test_top_down_directions(self):
        import trend
        up = trend.top_down(self._series(0.002))
        self.assertEqual(up["verdict"]["direction"], "long")
        down = trend.top_down(self._series(-0.002))
        self.assertEqual(down["verdict"]["direction"], "short")

    def test_trailing_only_tightens_and_rides(self):
        import trend
        bars = self._series(0.003, 120)
        out, px, held, mfe = trend.run_trailing(bars, 60, bars[60]["o"], bars[60]["o"] * 0.95, True, 3.0)
        self.assertEqual(out, "открыта")       # тренд не сломался — позиция держится до конца данных
        self.assertGreater(px, bars[60]["o"])
        rev = bars[:80] + [dict(b, o=b["o"] * 0.7, h=b["h"] * 0.7, l=b["l"] * 0.7, c=b["c"] * 0.7) for b in bars[80:90]]
        out2, px2, _, _ = trend.run_trailing(rev, 60, rev[60]["o"], rev[60]["o"] * 0.95, True, 3.0)
        self.assertIn(out2, ("трейлинг", "стоп"))

    def test_backtest_runs_and_is_causal(self):
        import trend
        bars = self._series(0.002)
        spec = {"kind": "shares", "minstep": 0.01, "lot": 1}
        tr = trend.backtest(bars, spec, dict(C.DEFAULT_CONFIG))
        self.assertTrue(tr)
        self.assertTrue(all(t["dir"] == "long" for t in tr))
        # будущие свечи не должны менять уже совершённые сделки
        tr_cut = trend.backtest(bars[:300], spec, dict(C.DEFAULT_CONFIG))
        closed = [t for t in tr_cut if t["outcome"] != "открыта"]
        self.assertEqual([t["date"] for t in closed], [t["date"] for t in tr[:len(closed)]])

    def test_risk_blocks_against_trend(self):
        tmp = tempfile.mkdtemp()
        env = {"TRADER_DIR": tmp}
        run("moex-entry/entry.py", "check", "SBER", "--setup", "orb", "--now", DAY + " 09:26", env=env)
        run("moex-exit/exit.py", "plan", "--write", "--now", DAY + " 09:26", env=env)
        run("moex-budget/size.py", "--write", env=env)
        r = run("moex-risk/check.py", "--now", DAY + " 09:26", env=env).stdout
        self.assertIn("тренд", r)

    def test_position_plan_without_target(self):
        tmp = tempfile.mkdtemp()
        env = {"TRADER_DIR": tmp}
        with open(os.path.join(tmp, "config.json"), "w") as f:
            json.dump({"require_trend_alignment": False}, f)
        run("moex-exit/exit.py", "plan", "--symbol", "SBER", "--stop", "atr:2", "--target", "none", "--trail", "3",
            "--mode", "position", "--write", "--now", DAY + " 12:00", env=env)
        p = _load(os.path.join(tmp, "plan.json"))
        self.assertIsNone(p["target"])
        self.assertEqual(p["trail"], 3.0)
        run("moex-budget/size.py", "--write", env=env)
        r = run("moex-risk/check.py", "--now", DAY + " 12:00", env=env).stdout
        self.assertIn("подтягиваемый стоп", r)
        u = run("moex-exit/exit.py", "update", "--now", DAY + " 18:50", env=env).stdout
        self.assertIn("трейлинг", u)


class TestRiskRules(unittest.TestCase):
    """Правила риска портфеля: позиции прошлых дней, неделя, просадка, корреляция, усреднение, стоп только вперёд."""

    BASE = {"require_trend_alignment": False, "require_backtest": False, "min_reward_risk": 1.5, "regime_filter": False}

    def prep(self, extra_cfg=None, positions=None, events=None):
        tmp = tempfile.mkdtemp()
        env = {"TRADER_DIR": tmp}
        cfg = dict(self.BASE, **(extra_cfg or {}))
        os.makedirs(os.path.join(tmp, "state"))
        with open(os.path.join(tmp, "config.json"), "w") as f:
            json.dump(cfg, f)
        if positions is not None:
            with open(os.path.join(tmp, "portfolio.json"), "w") as f:
                json.dump({"cash": None, "positions": positions}, f)
        with open(os.path.join(tmp, "state", "broker.json"), "w") as f:
            json.dump({"events": events or [], "approvals": {}}, f)
        run("moex-entry/entry.py", "check", "SBER", "--setup", "orb", "--now", DAY + " 09:26", env=env)
        run("moex-exit/exit.py", "plan", "--write", "--now", DAY + " 09:26", env=env)
        run("moex-budget/size.py", "--write", env=env)
        return env, tmp

    def check(self, env):
        return run("moex-risk/check.py", "--now", DAY + " 09:26", "--offline", env=env).stdout

    def line(self, out, name):
        return next((l for l in out.splitlines() if name in l), "")

    def test_clean_pass(self):
        env, _ = self.prep()
        out = self.check(env)
        self.assertIn("PASS", out)

    def test_swing_positions_count_in_open_risk(self):
        pos = {"ROSN": {"lots": 100, "avg": 380.0, "stop": 352.0, "side": "long", "kind": "shares", "lot_size": 1}}
        env, tmp = self.prep(positions=pos)        # позиция открыта давно, риск 2 800 ₽ до стопа
        o = _load(os.path.join(tmp, "orders.json"))[0]
        self.assertLessEqual(o["max_loss"], 200.5)  # бюджет урезал сделку до остатка лимита 3 000 − 2 800
        self.assertIn("✓", self.line(self.check(env), "открытый риск"))
        pos["ROSN"]["stop"] = 350.0                 # риск 3 000 ₽ — лимит выбран целиком
        env2, _ = self.prep(positions=pos)
        out2 = self.check(env2)
        self.assertIn("BLOCK", out2)

    def test_stop_in_profit_frees_risk(self):
        pos = {"ROSN": {"lots": 100, "avg": 380.0, "stop": 385.0, "side": "long", "kind": "shares", "lot_size": 1}}
        env, _ = self.prep(positions=pos)          # стоп подтянут выше входа: риска нет
        out = self.check(env)
        self.assertIn("✓", self.line(out, "открытый риск"))

    def test_position_without_stop_blocks(self):
        pos = {"ROSN": {"lots": 10, "avg": 380.0, "side": "long", "kind": "shares", "lot_size": 1}}
        env, _ = self.prep(positions=pos)
        self.assertIn("позиции без стопа", self.check(env))

    def test_weekly_limit(self):
        ev = [{"type": "fill", "at": "2026-10-26 12:00", "pnl": -2500, "symbol": "GAZP"},
              {"type": "fill", "at": "2026-10-28 12:00", "pnl": -1600, "symbol": "LKOH"}]
        env, _ = self.prep(events=ev)
        out = self.check(env)
        self.assertIn("✗", self.line(out, "стоп на неделю"))
        self.assertIn("✓", self.line(out, "стоп на день"))

    def test_drawdown_halves_then_pauses(self):
        ev = [{"type": "fill", "at": "2026-10-20 12:00", "pnl": -11000, "symbol": "GAZP"}]
        env, tmp = self.prep(extra_cfg={"max_open_risk": "10%"}, events=ev)
        o = _load(os.path.join(tmp, "orders.json"))[0]
        self.assertLessEqual(o["max_loss"], 250)   # 0,5% от 100 000 = 500, при просадке 11% — вдвое меньше
        out = self.check(env)
        self.assertIn("объём вдвое меньше", out)
        ev2 = [{"type": "fill", "at": "2026-10-20 12:00", "pnl": -16000, "symbol": "GAZP"}]
        env2, _ = self.prep(events=ev2)
        out2 = self.check(env2)
        self.assertIn("ПАУЗА", out2)
        self.assertIn("BLOCK", out2)

    def test_correlated_positions_count_as_one(self):
        pos = {"VTBR": {"lots": 1000, "avg": 56.0, "stop": 55.6, "side": "long", "kind": "shares", "lot_size": 1}}
        env, _ = self.prep(positions=pos)          # VTBR и SBER — одна отрасль; риск VTBR 400 ₽ + SBER ≈ 350 ₽ > 500 ₽
        out = self.check(env)
        self.assertIn("✗", self.line(out, "корреляция"))

    def test_no_averaging_down(self):
        pos = {"SBER": {"lots": 5, "avg": 455.0, "stop": 430.0, "side": "long", "kind": "shares", "lot_size": 1}}
        env, _ = self.prep(extra_cfg={"max_open_risk": "10%"}, positions=pos)
        out = self.check(env)
        self.assertIn("ЗАПРЕЩЕНА", out)

    def test_stop_only_moves_forward(self):
        pos = {"SBER": {"lots": 5, "avg": 440.0, "stop": 430.0, "side": "long", "kind": "shares", "lot_size": 1}}
        env, tmp = self.prep(positions=pos)
        bad = run("moex-broker/broker.py", "set-stop", "--symbol", "SBER", "--stop", "420", env=env, ok=False)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("Отодвигать стоп запрещено", bad.stderr)
        run("moex-broker/broker.py", "set-stop", "--symbol", "SBER", "--stop", "436", env=env)
        self.assertEqual(_load(os.path.join(tmp, "portfolio.json"))["positions"]["SBER"]["stop"], 436)


class TestScripts(unittest.TestCase):
    def setUp(self):
        self.env = {"TRADER_DIR": tempfile.mkdtemp()}

    def test_scanner_ta_backtest(self):
        n = DAY + " 18:50"
        s = run("moex-scanner/scan.py", "orb", "--at", "09:30", "--now", n, env=self.env).stdout
        self.assertIn("SBER", s)
        t = run("moex-ta/ta_report.py", "mtf", "SBER", "--mode", "swing", "--now", n, env=self.env).stdout
        self.assertIn("Сводно", t)
        c = run("moex-ta/ta_report.py", "chart", "SBER", "--tf", "1h", "--now", n, env=self.env).stdout
        path = c.strip().split(": ", 1)[1]
        self.assertTrue(_text(path).startswith("<!doctype html>"))
        b = run("moex-backtest/backtest.py", "orb", "SBER", "--now", n, env=self.env).stdout
        self.assertIn("Издержки съедают", b)
        o = run("moex-backtest/backtest.py", "optimize", "SBER", "--strategy", "orb", "--now", n, env=self.env).stdout
        self.assertIn("Проверка", o)

    def test_alerts_oco(self):
        env = self.env
        run("moex-entry/entry.py", "check", "SBER", "--setup", "orb", "--now", DAY + " 09:26", env=env)
        run("moex-exit/exit.py", "plan", "--write", "--now", DAY + " 09:26", env=env)
        run("moex-alerts/alerts.py", "add", "--from-plan", env=env)
        out = run("moex-alerts/alerts.py", "--now", DAY + " 18:50", "check", env=env).stdout
        self.assertIn("ЦЕЛЬ", out)
        al = _load((os.path.join(env["TRADER_DIR"], "alerts.json")))
        stop = [x for x in al if x.get("note") == "СТОП"][0]
        self.assertTrue(stop["fired"].startswith("отменено"))


def _write_tinvest_fixtures():
    P = "tinkoff.public.invest.api.contract.v1."

    def w(host, svc, obj, suffix=""):
        key = (host + "_rest_" + P + svc) + suffix
        with open(os.path.join(FX, key + ".json"), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
    for h in ("invest-public-api.tbank.ru", "sandbox-invest-public-api.tbank.ru"):
        w(h, "InstrumentsService_FindInstrument", {"instruments": [{"figi": "BBG004730N88", "ticker": "SBER", "classCode": "TQBR",
                                                                     "uid": "e6123145-9665-43e0-8413-cd61b8aa9b13"}]}, "__SBER")
        w(h, "InstrumentsService_GetInstrumentBy", {"instrument": {"lot": 10, "minPriceIncrement": {"units": "0", "nano": 10000000}}})
    sb = "sandbox-invest-public-api.tbank.ru"
    w(sb, "SandboxService_PostSandboxOrder", {"orderId": "ord-1", "executionReportStatus": "EXECUTION_REPORT_STATUS_NEW",
                                              "lotsRequested": "6", "lotsExecuted": "0"})
    w(sb, "SandboxService_GetSandboxOrderState", {"orderId": "ord-1", "executionReportStatus": "EXECUTION_REPORT_STATUS_FILL",
                                                  "lotsRequested": "6", "lotsExecuted": "6",
                                                  "averagePositionPrice": {"currency": "rub", "units": "440", "nano": 950000000}})
    w(sb, "SandboxService_PostSandboxStopOrder", {"stopOrderId": "stop-1"})
    w(sb, "SandboxService_GetSandboxAccounts", {"accounts": [{"id": "sb-123", "name": "moex-trader"}]})


if __name__ == "__main__":
    unittest.main()
