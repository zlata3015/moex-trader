"""Офлайн-тесты пакета moex-trader. Запуск: python3 -m unittest discover -s tests -v

Используют фикстуры в формате ответов ISS (tests/make_fixtures.py) и имитацию ответов T-Invest API.
"""
import importlib.util
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
        self.assertAlmostEqual(costs.per_unit(fu, 84000, cfg), 84000 * 0.0004 * 2 + 2)
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
            json.dump({"require_trend_alignment": False, "require_backtest": False, "require_high_rank": False, "min_reward_risk": 1.5,
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
            str(_load(os.path.join(self.tmp, "portfolio.json"))["positions"]["SBER"]["lots"]), "--price", str(o["target"]), env=env)
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

    def test_pullback_uses_ema10_and_confirm_never_blocks(self):
        import ta
        import trend
        bars = self._series(0.002, 300)
        i = 250
        c = [b["c"] for b in bars[:i + 1]]
        e = ta.ema(c, 10)[-1]
        a = ta.atr([b["h"] for b in bars[:i + 1]], [b["l"] for b in bars[:i + 1]], c)[-1]
        bars[i] = dict(bars[i], o=e, l=e - 0.1 * a, h=e + a, c=e + 0.5 * a)
        ctx = trend.signal_context(bars)
        s = trend.entry_signal(bars, "long", i, ctx=ctx)
        self.assertEqual(s["setup"], "trend-pullback")
        self.assertEqual(s["ema"], 10)
        ctx2 = dict(ctx, confirm=[10 ** 9] * len(bars))   # EMA20 против сигнала
        s2 = trend.entry_signal(bars, "long", i, ctx=ctx2)
        self.assertEqual(s2["setup"], "trend-pullback")
        self.assertFalse(s2["confirm"])
        self.assertIsNone(trend.entry_signal(bars, "long", i, ctx=ctx, setups=("trend-breakout",)))

    def test_stock_rule_ignores_day_but_short_needs_it(self):
        import trend
        st = {"1M": {"dir": "flat"}, "1w": {"dir": "up"}, "1d": {"dir": "flat"}}
        self.assertEqual(trend.combine(st, require=trend.STOCK_REQUIRE)["direction"], "long")
        self.assertIsNone(trend.combine(st)["direction"])
        st["1M"]["dir"] = "down"
        self.assertIsNone(trend.combine(st, require=trend.STOCK_REQUIRE)["direction"])

    def test_turnover_and_random_control(self):
        import trend
        bars = self._series(0.002)
        spec = {"kind": "shares", "minstep": 0.01, "lot": 1}
        thin = [dict(b, val=1000.0) for b in bars]
        self.assertEqual(trend.backtest(thin, spec, dict(C.DEFAULT_CONFIG), min_turnover=1e8), [])
        tr = trend.backtest(bars, spec, dict(C.DEFAULT_CONFIG), random_p=0.5, seed=3)
        self.assertTrue(tr and all(t["setup"] == "trend-random" for t in tr))

    def test_finance_is_warning_only(self):
        import moex
        page = """var aYearData = {
            'diagram' : {"categories":["2023","2024","LTM"],"data":[{"y":10.5,"color":"#e"},{"y":-2,"color":"#e"},{"y":1,"color":"#e"}],"field":"x"},
            'change_diagram' : {}};"""
        self.assertEqual(moex.parse_year_series(page), {"2023": 10.5, "2024": -2.0})
        f = moex.finance_warnings({"net_income": {"2023": 10.0, "2024": -2.0}, "net_debt": {"2024": 40.0},
                                   "ebitda": {"2024": 10.0}, "ocf": {"2024": -1.0}})
        text = " ".join(f["warnings"])
        self.assertIn("убыток", text)
        self.assertIn("4.0", text)
        self.assertIn("денежный поток", text)
        self.assertEqual(moex.finance_warnings({})["year"], None)
        ok = moex.finance_warnings({"net_income": {"2024": 5.0}, "net_debt": {"2024": 1.0}, "ebitda": {"2024": 10.0}, "ocf": {"2024": 2.0}})
        self.assertEqual(ok["warnings"], [])

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


class TestRank(unittest.TestCase):
    """Рейтинг только упорядочивает сигналы; отметка новости не меняется задним числом."""

    def setUp(self):
        import rank
        self.rank = rank
        self.tmp = tempfile.mkdtemp()
        self.old = os.environ.get("TRADER_DIR")
        os.environ["TRADER_DIR"] = self.tmp
        self.now = C.parse_dt("2026-11-05 12:00")
        self.bars = [{"c": 100 + i * 0.5 + (3 if i % 3 == 0 else -3)} for i in range(120)]

    def tearDown(self):
        os.environ.pop("TRADER_DIR") if self.old is None else os.environ.__setitem__("TRADER_DIR", self.old)

    def _conf(self, side_ok):
        return lambda b, d: {"EMA20": d == side_ok, "MACD": d == side_ok, "RSI": False}

    def test_tiers(self):
        old = self.rank.confirmations
        try:
            self.rank.confirmations = self._conf("long")
            self.assertEqual(self.rank.assess(self.bars, "long", "SBER", self.now)["tier"], "обычный")
            self.rank.add_mark("SBER", 1, "рост прибыли", "https://www.moex.com/n1", "2026-11-05", self.now)
            r = self.rank.assess(self.bars, "long", "SBER", self.now)
            self.assertEqual((r["tier"], r["k"], r["a"], r["news"]), ("высокий", 2, 0, 1))
            self.rank.add_mark("GAZP", -1, "плохо", "https://www.moex.com/n1", "2026-11-05", self.now)
            bad = self.rank.assess(self.bars, "long", "GAZP", self.now)
            self.assertEqual(bad["tier"], "низкий")
            self.assertLess(self.rank.sort_key(r), self.rank.sort_key(bad))
        finally:
            self.rank.confirmations = old

    def test_against_signal_is_low_even_with_good_news(self):
        old = self.rank.confirmations
        self.rank.confirmations = lambda b, d: {"EMA20": True, "MACD": d == "short", "RSI": False}
        try:
            self.rank.add_mark("SBER", 1, "t", "https://www.moex.com/n1", "2026-11-05", self.now)
            self.assertEqual(self.rank.assess(self.bars, "long", "SBER", self.now)["tier"], "низкий")
        finally:
            self.rank.confirmations = old

    def test_bad_news_is_high_for_sale(self):
        old = self.rank.confirmations
        self.rank.confirmations = self._conf("short")
        try:
            self.rank.add_mark("GAZP", -1, "убыток", "https://www.moex.com/n1", "2026-11-05", self.now)
            r = self.rank.assess(self.bars, "short", "GAZP", self.now)
            self.assertEqual((r["tier"], r["news"]), ("высокий", 1))
            self.assertEqual(self.rank.assess(self.bars, "long", "GAZP", self.now)["tier"], "низкий")
        finally:
            self.rank.confirmations = old

    def test_signal_log_keeps_untaken_once(self):
        r = {"symbol": "SBER", "direction": "long", "tier": "обычный", "k": 1, "a": 0, "news": 0}
        self.rank.log_signal(r, "откат", 300, 5, "2026-11-05", True)
        self.rank.log_signal(r, "откат", 300, 5, "2026-11-05", True)
        self.assertEqual(len(C.load_json(C.tpath("signals.json"), [])), 1)

    def test_risk_blocks_without_high_rank(self):
        spec = importlib.util.spec_from_file_location("risk_rank", os.path.join(SK, "moex-risk", "check.py"))
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        o = {"direction": "long", "kind": "shares", "symbol": "SBER", "lots": 1, "stop": 280.0, "entry": 290.0, "target": None, "trail": 3,
             "max_loss": 100, "capital": 2900, "mode": "position", "date": DAY, "setup": "trend-pullback"}
        st = {"realized_today": 0, "realized_week": 0, "open_risk": 0, "trades_today": 0, "positions": {}, "position_risk": {}}
        now = C.parse_dt(DAY + " 12:00")
        def verdict(rk, closing=False):
            res = m.check(dict(o, rank=rk, closing=closing), dict(C.DEFAULT_CONFIG, require_backtest=False), now, st, offline=True)
            return {r["check"]: r["ok"] for r in res}
        high = {"tier": "высокий", "direction": "long", "k": 2, "a": 0, "news": 1}
        self.assertTrue(verdict(high)["рейтинг"])
        self.assertFalse(verdict(dict(high, tier="обычный"))["рейтинг"])
        self.assertFalse(verdict(dict(high, tier="низкий"))["рейтинг"])
        self.assertFalse(verdict(dict(high, direction="short"))["рейтинг"])
        self.assertFalse(verdict(None)["рейтинг"])
        self.assertNotIn("рейтинг", verdict(None, closing=True))
        # рискованная стратегия: тот же запрет работает для шорта
        short = dict(o, direction="short", side="sell", stop=300.0, rank=dict(high, direction="short"))
        cfg = dict(C.DEFAULT_CONFIG, require_backtest=False, allow_short=True)
        res = {r["check"]: r["ok"] for r in m.check(short, cfg, now, st, offline=True)}
        self.assertTrue(res["рейтинг"]); self.assertTrue(res["шорт"])
        short["rank"] = dict(short["rank"], tier="обычный")
        self.assertFalse({r["check"]: r["ok"] for r in m.check(short, cfg, now, st, offline=True)}["рейтинг"])

    def test_news_expires_and_needs_source(self):
        self.rank.add_mark("SBER", 1, "t", "https://www.moex.com/n1", "2026-10-29", self.now)
        self.assertIsNone(self.rank.news_of("SBER", self.now))
        with self.assertRaises(ValueError):
            self.rank.add_mark("SBER", 1, "", "https://www.moex.com/n1", "2026-11-05", self.now)
        with self.assertRaises(ValueError):
            self.rank.add_mark("SBER", 2, "t", "https://www.moex.com/n1", "2026-11-05", self.now)

    def test_news_sources(self):
        self.rank.add_mark("A", 1, "t", "https://www.interfax.ru/1", "2026-11-05", self.now, "https://www.vedomosti.ru/2")
        self.rank.add_mark("B", 0, "t", "https://smart-lab.ru/1", "2026-11-05", self.now)
        for u, u2 in (("https://smart-lab.ru/1", None), ("https://www.interfax.ru/1", None),
                      ("https://www.interfax.ru/1", "https://interfax.ru/2"), ("https://fakemoex.com/1", None),
                      ("https://www.interfax.ru/1", "https://smart-lab.ru/2")):
            with self.assertRaises(ValueError):
                self.rank.add_mark("C", -1, "t", u, "2026-11-05", self.now, u2)

    def test_mark_written_later_is_invisible_earlier(self):
        self.rank.add_mark("SBER", 1, "t", "https://www.moex.com/n1", "2026-11-05", self.now)
        self.assertIsNone(self.rank.news_of("SBER", C.parse_dt("2026-11-05 11:00")))

    def test_short_mirrors_long(self):
        up = [{"c": 100 + i * 0.3 + (2 if i % 2 else -2)} for i in range(120)]
        c = self.rank.confirmations(up, "long")
        s = self.rank.confirmations(up, "short")
        self.assertTrue(c["EMA20"]); self.assertFalse(s["EMA20"])


class TestProfiles(unittest.TestCase):
    """Две стратегии: настройки поверх общих, выбор на месяц, раздельный учёт."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old = os.environ.get("TRADER_DIR")
        os.environ["TRADER_DIR"] = self.tmp
        self.month = C.now_msk().strftime("%Y-%m")

    def tearDown(self):
        os.environ.pop("TRADER_DIR") if self.old is None else os.environ.__setitem__("TRADER_DIR", self.old)

    def pick(self, name, month=None):
        C.save_json(C.tpath("strategy.json"), {month or self.month: name})

    def risk(self):
        spec = importlib.util.spec_from_file_location("risk_prof", os.path.join(SK, "moex-risk", "check.py"))
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        return m

    def test_default_is_moderate(self):
        cfg = C.config()
        self.assertEqual(cfg["strategy"], "moderate")
        self.assertFalse(cfg["allow_short"])
        self.assertEqual(cfg["max_loss_per_trade"], "0.5%")

    def test_risky_overrides_and_previous_month_continues(self):
        self.pick("risky", "2000-01")
        cfg = C.config()
        self.assertEqual((cfg["strategy"], cfg["max_open_positions"], cfg["dd_stop_pct"]), ("risky", 1, 30))
        self.assertTrue(cfg["allow_short"])
        self.assertTrue(cfg["paper"])

    def order(self, **k):
        o = {"direction": "short", "kind": "shares", "symbol": "SBER", "lots": 1, "stop": 300.0, "entry": 290.0, "target": None, "trail": 3,
             "max_loss": 100, "capital": 2900, "mode": "position", "date": DAY, "setup": "h10", "strategy": "risky",
             "rank": {"tier": "высокий", "direction": "short", "k": 2, "a": 0, "news": 1}}
        o.update(k)
        return o

    def run_check(self, o, cfg, positions=None):
        st = {"realized_today": 0, "realized_week": 0, "open_risk": 0, "trades_today": 0, "positions": positions or {}, "position_risk": {}}
        return {r["check"]: r["ok"] for r in self.risk().check(o, cfg, C.parse_dt(DAY + " 12:00"), st, offline=True)}

    def test_short_only_in_risky_and_sandbox(self):
        self.pick("risky")
        res = self.run_check(self.order(), C.config())
        self.assertTrue(res["шорт"]); self.assertTrue(res["шорт и этап"]); self.assertTrue(res["реестр"])
        real = self.run_check(self.order(), dict(C.config(), paper=False))
        self.assertFalse(real["шорт и этап"]); self.assertFalse(real["этап"])
        os.remove(C.tpath("strategy.json"))
        self.assertFalse(self.run_check(self.order(strategy="moderate"), C.config())["шорт"])

    def test_order_of_other_strategy_blocked(self):
        self.assertFalse(self.run_check(self.order(), C.config())["стратегия месяца"])

    def test_one_position_in_risky_even_if_correlated(self):
        self.pick("risky")
        res = self.run_check(self.order(), C.config(), {"VTBR": {"lots": 1, "avg": 100, "stop": 90}})
        self.assertFalse(res["одна позиция"])

    def test_drawdown_counted_per_strategy(self):
        import riskbook
        C.save_json(C.tpath("state", "broker.json"), {"events": [
            {"type": "fill", "pnl": -9000, "at": "2026-10-01 10:00", "strategy": "risky"},
            {"type": "fill", "pnl": 1000, "at": "2026-10-02 10:00"}], "approvals": {}})
        self.assertEqual(riskbook.equity(100000)[0], 101000)
        self.pick("risky", "2000-01")
        self.assertEqual(riskbook.equity(100000)[0], 91000)

    def test_month_choice_locked_after_first_trade(self):
        now = self.month + "-15 12:00"
        ok = run("moex-setup/setup.py", "strategy", "risky", "--now", now, env={"TRADER_DIR": self.tmp})
        self.assertEqual(C.load_json(C.tpath("strategy.json"))[self.month], "risky")
        self.assertEqual(ok.returncode, 0)
        C.save_json(C.tpath("state", "broker.json"), {"events": [{"type": "placed", "at": self.month + "-10 10:00"}], "approvals": {}})
        r = run("moex-setup/setup.py", "strategy", "moderate", "--now", now, env={"TRADER_DIR": self.tmp}, ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(C.load_json(C.tpath("strategy.json"))[self.month], "risky")


class TestRiskRules(unittest.TestCase):
    """Правила риска портфеля: позиции прошлых дней, неделя, просадка, корреляция, усреднение, стоп только вперёд."""

    BASE = {"require_trend_alignment": False, "require_backtest": False, "min_reward_risk": 1.5, "regime_filter": False, "require_high_rank": False}

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
        ev = [{"type": "fill", "at": "2026-10-20 12:00", "pnl": -8000, "symbol": "GAZP"}]
        env, tmp = self.prep(extra_cfg={"max_open_risk": "10%"}, events=ev)
        o = _load(os.path.join(tmp, "orders.json"))[0]
        self.assertLessEqual(o["max_loss"], 250)   # 0,5% от 100 000 = 500, при просадке 8% — вдвое меньше
        out = self.check(env)
        self.assertIn("объём вдвое меньше", out)
        ev2 = [{"type": "fill", "at": "2026-10-20 12:00", "pnl": -11000, "symbol": "GAZP"}]
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


class TestHypotheses(unittest.TestCase):
    def setUp(self):
        import hypo
        self.H = hypo
        self.cal = ["2020-01-%02d" % d for d in range(1, 11)]
        self.key = [("2020-01-01", 11.0)]

    def good(self, **kw):
        m = {"premium": 3.0, "p": 0.001, "periods": {"a": 1, "b": 2, "c": 1}, "premium_late": 1.0, "premium_cost2": 2.0,
             "legs": 6, "legs_won": 5, "switches": 12, "max_dd": -5.0, "hold_dd": -30.0}
        m.update(kw)
        return m

    def test_signal_trades_next_close(self):
        pos = self.H.positions(self.cal, [("2020-01-03", 1.0)])
        self.assertEqual(pos[:5], [0.0, 0.0, 0.0, 0.0, 1.0])  # сигнал 3-го, покупка закрытием 4-го, доход с 5-го

    def test_money_index_daily_accrual(self):
        m = self.H.money_index(self.cal, self.key, spread=1.0)
        self.assertAlmostEqual(m[1], 1 + 0.10 / 365, places=9)

    def test_cost_on_switch(self):
        asset = [100.0] * 10
        money = [1.0] * 10
        pos = [0, 0, 1, 1, 1, 0, 0, 0, 0, 0]
        r = self.H.daily_returns(self.cal, asset, money, pos, 0.001)
        self.assertAlmostEqual(sum(r), -0.002)

    def test_verdict_confirmed_needs_family_correction(self):
        self.assertEqual(self.H.verdict(self.good(), 1)[0], "confirmed")
        self.assertEqual(self.H.verdict(self.good(p=0.01), 22)[0], "candidate")

    def test_verdict_frequent_needs_100_trades(self):
        self.assertEqual(self.H.verdict(self.good(switches=120), 1, "frequent")[0], "candidate")
        self.assertEqual(self.H.verdict(self.good(switches=240), 1, "frequent")[0], "confirmed")

    def test_verdict_rejects_and_risk_filter(self):
        self.assertEqual(self.H.verdict(self.good(premium=-0.5, max_dd=-25.0), 1)[0], "rejected")
        self.assertEqual(self.H.verdict(self.good(premium=0.5, p=0.4), 1)[0], "risk_filter")
        self.assertEqual(self.H.verdict(self.good(premium_late=-1.0, max_dd=-25.0), 1)[0], "rejected")

    def test_registry_rule_is_frozen_and_gate(self):
        reg = {"hypotheses": []}
        self.H.add(reg, "X1", "утверждение", "f", "правило")
        with self.assertRaises(ValueError):
            self.H.add(reg, "X1", "другое", "f", "другое правило")
        with self.assertRaises(ValueError):
            self.H.add(reg, "X2", "без правила", "f", " ")
        self.assertEqual(self.H.tradable(reg), [])
        self.H.record(reg, "X1", self.good(premium=-1.0, max_dd=-25.0))
        self.assertEqual(reg["hypotheses"][0]["status"], "rejected")
        self.assertEqual(self.H.tradable(reg), [])
        self.assertEqual(self.H.family_size(reg, "f"), 1)
        with self.assertRaises(ValueError):
            self.H.promote(reg, "X1", "sandbox")

    def test_evaluate_on_synthetic_series(self):
        cal = []
        for y in range(2015, 2019):
            for m in range(1, 13):
                for d in (5, 15, 25):
                    cal.append("%d-%02d-%02d" % (y, m, d))
        asset = [100 * 1.001 ** i for i in range(len(cal))]
        sig = [(cal[0], 1.0)]
        r = self.H.evaluate(cal, asset, [(cal[0], 1.0)], sig, cal[0], [("all", "2015", "2018")], cost=0)
        self.assertGreater(r["premium"], 0)
        self.assertEqual(r["legs"], 1)
        self.assertEqual(r["max_dd"], 0)
        self.assertEqual(r["hold_dd"], 0)

    def test_evaluate_custom_base(self):
        cal = ["%d-%02d-15" % (y, m) for y in range(2015, 2019) for m in range(1, 13)]
        base = [100.0 * (0.99 if i > 3 and i % 2 else 1.0) for i in range(len(cal))]  # первые дни сигнал ещё не исполнен
        asset = [100.0] * len(cal)
        r = self.H.evaluate(cal, asset, [(cal[0], 1.0)], [(cal[0], 1.0)], cal[0], [("all", "2015", "2018")], cost=0, money=base)
        self.assertEqual(r["max_dd"], 0)
        self.assertEqual(r["hold_dd"], -1.0)

    def test_reject_external_only_rejects_planned(self):
        reg = {"hypotheses": []}
        self.H.add(reg, "X1", "утверждение", "f", "правило")
        self.H.reject_external(reg, "X1", "отказ", "документ")
        self.assertEqual(reg["hypotheses"][0]["status"], "rejected")
        with self.assertRaises(ValueError):
            self.H.reject_external(reg, "X1", "снова", "документ")

    def test_evaluate_events(self):
        cal = ["2020-%02d-%02d" % (m, d) for m in range(1, 13) for d in range(1, 26)]
        asset = [100 * 1.01 ** i for i in range(len(cal))]
        base = [100.0] * len(cal)
        ev = [cal[10], cal[12], cal[100]]
        r = self.H.evaluate_events(cal, asset, base, ev, 5, [("all", "2020", "2020")], cost=0.001)
        self.assertEqual(r["events"], 2)  # вторая попала в открытую позицию
        self.assertAlmostEqual(r["premium"], (1.01 ** 5 - 1 - 0.002) * 100, places=2)
        self.assertEqual(r["legs_won"], 2)
        self.assertEqual(self.H.verdict(r, 1)[0] in ("candidate", "rejected", "confirmed"), True)

    def test_registry_file_valid(self):
        reg = self.H.load(os.path.join(SK, "moex-hypotheses", "registry.json"))
        self.assertEqual(self.H.tradable(reg), [])
        for h in reg["hypotheses"]:
            self.assertIn(h["status"], self.H.VERDICTS)
            self.assertTrue(h["rule"])


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


class TestProtection(unittest.TestCase):
    def test_futures_order_without_target(self):
        import importlib.util
        sp = importlib.util.spec_from_file_location("risk_check", os.path.join(SK, "moex-risk", "check.py"))
        m = importlib.util.module_from_spec(sp)
        sp.loader.exec_module(m)
        o = {"direction": "long", "kind": "futures", "symbol": "SiZ6", "lots": 1, "stop": 80000.0, "entry": 82000.0, "target": None, "trail": 3,
             "max_loss": 500, "capital": 12000, "mode": "swing", "date": DAY, "spec": {"high_limit": 90000, "low_limit": 70000}}
        st = {"realized_today": 0, "realized_week": 0, "open_risk": 0, "trades_today": 0, "positions": {}, "position_risk": {}}
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("TRADER_DIR")
            os.environ["TRADER_DIR"] = tmp
            try:
                res = m.check(o, dict(C.DEFAULT_CONFIG), C.parse_dt(DAY + " 12:00"), st, offline=True)
            finally:
                os.environ.pop("TRADER_DIR") if old is None else os.environ.__setitem__("TRADER_DIR", old)
        self.assertTrue(any(r["check"] == "ценовые лимиты" and r["ok"] for r in res))

    def test_futures_sessions_without_day_clearing(self):
        cfg = dict(C.DEFAULT_CONFIG)
        self.assertEqual(C.session_of("futures", C.parse_dt(DAY + " 14:02"), cfg), "main")
        self.assertEqual(C.session_of("futures", C.parse_dt(DAY + " 18:55"), cfg), "main")
        self.assertEqual(C.session_of("futures", C.parse_dt(DAY + " 23:55"), cfg), "clearing")

    def test_dataqc(self):
        import dataqc
        now = C.parse_dt("2026-10-30")
        good = [{"t": "2026-10-2%d 00:00" % i, "o": 10, "h": 11, "l": 9, "c": 10, "v": 5} for i in range(6, 10)]
        self.assertEqual(dataqc.check_daily(good, now)["errors"], [])
        self.assertTrue(dataqc.check_daily(good, C.parse_dt("2026-11-20"))["errors"])
        self.assertTrue(dataqc.check_daily(good + [dict(good[-1], t="2026-10-30 00:00", h=1)], now)["errors"])
        self.assertTrue(dataqc.check_daily(good + [good[-1]], now)["errors"])

    def _broker_env(self, tmp):
        env = {"TRADER_DIR": tmp, "TINVEST_SANDBOX_TOKEN": "dummy-token"}
        os.makedirs(os.path.join(tmp, "state"), exist_ok=True)
        with open(os.path.join(tmp, "config.json"), "w") as f:
            json.dump({"tinvest_account_id": "sb-123"}, f)
        return env

    def test_reconcile_and_halt(self):
        _write_tinvest_fixtures()
        with tempfile.TemporaryDirectory() as tmp:
            env = self._broker_env(tmp)
            self.assertIn("сходится", run("moex-broker/broker.py", "reconcile", env=env).stdout)
            with open(os.path.join(tmp, "portfolio.json"), "w") as f:
                json.dump({"positions": {"SBER": {"lots": 2, "avg": 300, "lot_size": 10, "kind": "shares", "side": "long"}}}, f)
            r = run("moex-broker/broker.py", "reconcile", env=env, ok=False)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("в учёте 20", r.stdout)
            run("moex-broker/broker.py", "halt", "--reason", "тест", env=env)
            self.assertTrue(os.path.exists(os.path.join(tmp, "HALT")))
            run("moex-broker/broker.py", "resume", env=env)
            self.assertFalse(os.path.exists(os.path.join(tmp, "HALT")))

    def test_tbank_certificate_scope(self):
        import hashlib
        import ssl
        with open(C.RU_CA_FILE) as f:
            der = ssl.PEM_cert_to_DER_cert(f.read())
        self.assertEqual(hashlib.sha256(der).hexdigest(), "d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31")
        self.assertIsNone(C.ssl_context("https://iss.moex.com/iss/index.json"))
        self.assertIsNone(C.ssl_context("https://tbank.ru.evil.example/rest/"))
        ctx = C.ssl_context("https://sandbox-invest-public-api.tbank.ru/rest/")
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)

    def test_paper_reads_go_through_sandbox(self):
        import tinvest
        seen = []
        old = tinvest.call
        tinvest.call = lambda service, method, body=None, sandbox=False: seen.append(sandbox) or {}
        with tempfile.TemporaryDirectory() as tmp:
            env_old = os.environ.get("TRADER_DIR")
            os.environ["TRADER_DIR"] = tmp
            try:
                tinvest.read("InstrumentsService", "FindInstrument", {})
                with open(os.path.join(tmp, "config.json"), "w") as f:
                    json.dump({"paper": False}, f)
                tinvest.read("InstrumentsService", "FindInstrument", {})
            finally:
                tinvest.call = old
                os.environ.pop("TRADER_DIR") if env_old is None else os.environ.__setitem__("TRADER_DIR", env_old)
        self.assertEqual(seen, [True, False])

    def _risk(self, cfg, setup, offline=True, positions=None):
        import importlib.util
        sp = importlib.util.spec_from_file_location("risk_check2", os.path.join(SK, "moex-risk", "check.py"))
        m = importlib.util.module_from_spec(sp)
        sp.loader.exec_module(m)
        o = {"direction": "long", "kind": "shares", "symbol": "SBER", "lots": 1, "stop": 280.0, "entry": 290.0, "target": None, "trail": 3,
             "max_loss": 100, "capital": 2900, "mode": "position", "date": DAY, "setup": setup}
        st = {"realized_today": 0, "realized_week": 0, "open_risk": 0, "trades_today": 0, "positions": positions or {}, "position_risk": {}}
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("TRADER_DIR")
            os.environ["TRADER_DIR"] = tmp
            try:
                return {r["check"]: r["ok"] for r in m.check(o, dict(C.DEFAULT_CONFIG, require_backtest=False, **cfg), C.parse_dt(DAY + " 12:00"), st, offline=offline)}
            finally:
                os.environ.pop("TRADER_DIR") if old is None else os.environ.__setitem__("TRADER_DIR", old)

    def test_real_order_needs_live_status(self):
        self.assertTrue(self._risk({}, "trend-pullback")["реестр"])
        real = self._risk({"paper": False}, "trend-pullback")
        self.assertFalse(real["этап"])
        self.assertFalse(real["офлайн"])
        self.assertFalse(self._risk({"paper": False}, "manual", offline=False)["этап"])

    def test_network_failure_blocks(self):
        import moex
        old = moex.candles
        def boom(*a, **k):
            raise C.NetError("нет связи")
        moex.candles = boom
        try:
            res = self._risk({}, "trend-pullback", offline=False)
        finally:
            moex.candles = old
        self.assertFalse(res["тренд"])
        self.assertFalse(res["режим рынка"])

    def test_real_order_cannot_switch_checks_off(self):
        res = self._risk({"paper": False, "regime_filter": False}, "trend-pullback")
        self.assertFalse(res["настройки"])

    def test_correlation_failure_blocks(self):
        import moex
        old = moex.candles
        def boom(*a, **k):
            raise C.NetError("нет связи")
        moex.candles = boom
        try:
            res = self._risk({}, "trend-pullback", offline=False, positions={"GAZP": {"lots": 1, "avg": 100, "stop": 90}})
        finally:
            moex.candles = old
        self.assertFalse(res["корреляция"])

    def test_closing_must_reduce_real_position(self):
        import riskbook
        o = {"symbol": "SBER", "side": "sell", "lots": 2, "kind": "shares", "lot_size": 10}
        self.assertIsNone(riskbook.closing_problem(o, {"SBER": (20, "share")}))
        self.assertIn("нет позиции", riskbook.closing_problem(o, {}))
        self.assertIn("больше позиции", riskbook.closing_problem(o, {"SBER": (10, "share")}))
        self.assertIn("увеличила", riskbook.closing_problem(dict(o, side="buy"), {"SBER": (20, "share")}))
        self.assertIn("увеличила", riskbook.closing_problem(o, {"SBER": (-20, "share")}))

    def test_paper_pass_not_valid_on_real_account(self):
        spec = importlib.util.spec_from_file_location("broker_gate", os.path.join(SK, "moex-broker", "broker.py"))
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        tmp = tempfile.mkdtemp()
        old = os.environ.get("TRADER_DIR")
        os.environ["TRADER_DIR"] = tmp
        try:
            os.makedirs(os.path.join(tmp, "state"))
            with open(os.path.join(tmp, "state", "risk.json"), "w") as f:
                json.dump({"verdicts": {"fp": {"verdict": "PASS", "date": DAY, "paper": True}}}, f)
            s = {"approvals": {"fp": {"at": DAY + " 10:00", "paper": True}}}
            o = {"fingerprint": "fp"}
            now = C.parse_dt(DAY + " 12:00")
            self.assertTrue(m.readiness(o, now, s)[0] and m.readiness(o, now, s)[1])
            with open(os.path.join(tmp, "config.json"), "w") as f:
                json.dump({"paper": False}, f)
            r = m.readiness(o, now, s)
            self.assertFalse(r[0]); self.assertFalse(r[1])
        finally:
            os.environ.pop("TRADER_DIR") if old is None else os.environ.__setitem__("TRADER_DIR", old)

    def test_live_needs_sandbox_journal(self):
        import hypo
        reg = {"hypotheses": [{"id": "S01", "status": "candidate", "family": "equities-trend"}]}
        hypo.promote(reg, "S01", "sandbox")
        with self.assertRaises(ValueError):
            hypo.promote(reg, "S01", "live", [])
        j = [{"setup": "trend-pullback", "closed": "2026-%02d-10 12:00" % (1 + i % 6)} for i in range(50)]
        hypo.promote(reg, "S01", "live", j)
        self.assertEqual(reg["hypotheses"][0]["status"], "live")

    def test_repeat_close_is_new_order(self):
        o = {"symbol": "SBER", "side": "sell", "lots": 1, "type": "market", "date": DAY, "closing": True, "time": DAY + " 12:00"}
        self.assertNotEqual(C.fingerprint(o), C.fingerprint(dict(o, time=DAY + " 15:00")))
        e = {"symbol": "SBER", "side": "buy", "lots": 1, "type": "limit", "date": DAY, "time": DAY + " 12:00"}
        self.assertEqual(C.fingerprint(e), C.fingerprint(dict(e, time=DAY + " 15:00")))

    def test_market_close_has_no_price(self):
        sys.path.insert(0, os.path.join(SK, "moex-broker"))
        import broker
        self.assertIsNone(broker.order_price({"entry": None, "closing": True}, {"minstep": 0.01}))
        self.assertEqual(broker.order_price({"entry": 291.913}, {"minstep": 0.01}), 291.91)

    def test_unknown_send_blocks_repeat(self):
        sys.path.insert(0, os.path.join(SK, "moex-broker"))
        import broker
        s = {"events": [{"type": "sending", "fingerprint": "x"}, {"type": "unknown", "fingerprint": "x"}]}
        self.assertTrue(broker.send_unknown(s, "x"))
        s["events"].append({"type": "resolved", "fingerprint": "x"})
        self.assertFalse(broker.send_unknown(s, "x"))
        self.assertFalse(broker.send_unknown(s, "y"))


class TestBonds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = {"TRADER_DIR": self.tmp}

    def test_irr_and_duration(self):
        import bondcalc as B
        from datetime import datetime
        s = datetime(2026, 1, 1)
        fl = [("2027-01-01", 1100.0)]
        self.assertAlmostEqual(B.irr(fl, 1000.0, s), 0.10, places=6)
        mac, mod = B.duration(fl, s, 0.10)
        self.assertAlmostEqual(mac, 1.0, places=6)
        self.assertAlmostEqual(mod, 1 / 1.1, places=6)

    def test_net_yield_below_gross(self):
        import bondcalc as B
        from datetime import datetime
        s = datetime(2026, 1, 1)
        info = B.build_flows([{"coupondate": "2026-07-01", "value": 50}, {"coupondate": "2027-01-01", "value": 50}],
                             [{"amortdate": "2027-01-01", "value": 1000}], [], s, 1000)
        self.assertEqual(info["horizon_type"], "погашение")
        g = B.irr(info["flows"], 960.0, s)
        n = B.net_yield(info, 960.0, 0.0, s, 0.13, 0.0)
        self.assertLess(n, g)
        # налог 13% с купонов 100 и разницы 40 = 18,2 ₽ на 960 ₽ за год
        self.assertAlmostEqual(g - n, 18.2 / 960, delta=0.003)

    def test_put_offer_is_horizon_and_call_is_not(self):
        import bondcalc as B
        from datetime import datetime
        s = datetime(2026, 1, 1)
        am = [{"amortdate": "2030-01-01", "value": 1000}]
        cp = [{"coupondate": "2027-01-01", "value": 100}, {"coupondate": "2028-01-01", "value": None}]
        put = B.build_flows(cp, am, [{"offerdate": "2028-01-01", "offertype": "Оферта"}], s, 1000)
        self.assertEqual(put["horizon"], "2028-01-01")
        self.assertEqual(put["unknown"], 1)
        call = B.build_flows(cp, am, [{"offerdate": "2028-01-01", "offertype": "Call"}], s, 1000)
        self.assertEqual(call["horizon"], "2030-01-01")

    def test_cli(self):
        out = run("moex-bonds/bonds.py", "--now", DAY + " 12:00", "screen", env=self.env).stdout
        self.assertIn("SU26999RMFS1", out)
        self.assertNotIn("SU29999RMFS1", out)   # флоатер
        self.assertNotIn("SU52999RMFS1", out)   # линкер
        self.assertIn("SU29999RMFS1", run("moex-bonds/bonds.py", "screen", "--floaters", env=self.env).stdout)
        with open(os.path.join(self.tmp, "config.json"), "w") as f:
            json.dump({"key_rate": 14.0}, f)
        c = run("moex-bonds/bonds.py", "--now", DAY + " 12:00", "show", "SU26999RMFS1", env=self.env).stdout
        self.assertIn("после налога", c)
        self.assertIn("рейтинг", c)
        lad = run("moex-bonds/bonds.py", "--now", DAY + " 12:00", "ladder", "--sum", "30000", "--rungs", "3", env=self.env).stdout
        self.assertIn("SU26999RMFS1", lad)


class TestPortfolio(unittest.TestCase):
    def test_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"TRADER_DIR": tmp}
            with open(os.path.join(tmp, "config.json"), "w") as f:
                json.dump({"account_size": 100000}, f)
            with open(os.path.join(tmp, "portfolio.json"), "w") as f:
                json.dump({"cash": 50000, "positions": {
                    "SBER": {"lots": 10, "avg": 300, "lot_size": 10, "kind": "shares", "side": "long", "stop": 290},
                    "GAZP": {"lots": 2, "avg": 130, "lot_size": 10, "kind": "shares", "side": "long"}}}, f)
            out = run("moex-portfolio/review.py", "--offline", "--now", DAY + " 12:00", env=env).stdout
            self.assertIn("акции", out)
            self.assertIn("Без стопа: GAZP", out)
            self.assertIn("февраль–март 2022", out)
            self.assertIn("-10 399", out.replace("\u00a0", " ").replace("−", "-"))


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
    w(sb, "OperationsService_GetSandboxPortfolio", {"positions": []})
    w(sb, "SandboxService_GetSandboxPortfolio", {"positions": []})
    w(sb, "SandboxService_GetSandboxStopOrders", {"stopOrders": []})
    w(sb, "SandboxService_GetSandboxAccounts", {"accounts": [{"id": "sb-123", "name": "moex-trader"}]})


class TestSplits(unittest.TestCase):
    @staticmethod
    def _bars(prices):
        return [{"t": d + " 00:00", "o": o, "h": h, "l": l, "c": c, "v": 100} for d, o, h, l, c in prices]

    def test_smartlab_divs_not_scaled_twice(self):
        bars = self._bars([("2024-07-12", 100, 101, 99, 100), ("2024-07-15", 1, 1.1, .9, 1)])
        divs = [{"date": "2023-05-10", "value": 2.5, "source": "smart-lab"}, {"date": "2023-05-10", "value": 2.5}]
        _, out, ev = moex.adjust_splits(bars, divs)
        self.assertEqual(len(ev), 1)
        self.assertEqual(out[0]["value"], 2.5)
        self.assertAlmostEqual(out[1]["value"], .025)

    def test_known_split_with_old_open(self):
        bars = self._bars([("2024-08-21", 4700, 4720, 4650, 4680), ("2024-08-22", 4223, 4223, 651, 714),
                           ("2024-08-23", 713, 719, 652, 687)])
        out, _, ev = moex.adjust_splits(bars, symbol="BELU")
        self.assertEqual(ev, [("2024-08-22", 1.0 / 8)])
        self.assertAlmostEqual(out[0]["c"], 585)
        self.assertAlmostEqual(out[1]["o"], 527.875)
        self.assertEqual(out[1]["l"], 651)
        self.assertEqual(out[2]["o"], 713)


if __name__ == "__main__":
    unittest.main()
