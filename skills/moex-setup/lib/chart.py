"""График в один HTML-файл (SVG, без внешних библиотек): свечи, EMA20/50, Боллинджер, уровни, объём, RSI, MACD."""
import html

import ta


def render(bars, title, path, n=150, levels=None, plan=None):
    bars = bars[-max(n + 210, n):]
    c = [b["c"] for b in bars]
    h = [b["h"] for b in bars]
    l = [b["l"] for b in bars]
    e20, e50 = ta.ema(c, 20), ta.ema(c, 50)
    _, bu, bl, _ = ta.bollinger(c)
    r = ta.rsi(c)
    _, _, mh = ta.macd(c)
    cut = len(bars) - n if len(bars) > n else 0
    B = bars[cut:]
    sl = lambda x: x[cut:]
    e20, e50, bu, bl, r, mh = map(sl, (e20, e50, bu, bl, r, mh))

    W, PH, VH, RH, MH, PAD, LM = 1100, 380, 70, 90, 80, 8, 64
    H = PH + VH + RH + MH + PAD * 5 + 24
    xs = (W - LM - 10) / max(1, len(B))
    lo = min(min(b["l"] for b in B), *(x for x in bl if x is not None)) if any(bl) else min(b["l"] for b in B)
    hi = max(max(b["h"] for b in B), *(x for x in bu if x is not None)) if any(bu) else max(b["h"] for b in B)
    extra = [lv["price"] for lv in (levels or []) if lo * 0.97 <= lv["price"] <= hi * 1.03]
    if plan:
        extra += [plan[k] for k in ("entry", "stop", "target") if plan.get(k)]
    if extra:
        lo, hi = min(lo, *extra), max(hi, *extra)
    pad = (hi - lo) * 0.04 or 1
    lo, hi = lo - pad, hi + pad
    y = lambda p: PAD + (hi - p) / (hi - lo) * PH
    x = lambda i: LM + i * xs + xs / 2
    out = []
    a = out.append
    # сетка цен
    for k in range(6):
        p = lo + (hi - lo) * k / 5
        a('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="grid"/>' % (LM, W - 10, y(p), y(p)))
        a('<text x="%d" y="%.1f" class="ax">%s</text>' % (LM - 6, y(p) + 4, _fmt(p)))
    # уровни
    for lv in levels or []:
        if lo <= lv["price"] <= hi:
            cls = "sup" if lv["type"] == "поддержка" else "res"
            a('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="%s"/>' % (LM, W - 10, y(lv["price"]), y(lv["price"]), cls))
            a('<text x="%d" y="%.1f" class="lv %s">%s ×%d</text>' % (W - 12, y(lv["price"]) - 3, cls, _fmt(lv["price"]), lv["touches"]))
    if plan:
        for k, cls, name in (("entry", "pe", "вход"), ("stop", "ps", "стоп"), ("target", "pt", "цель")):
            if plan.get(k):
                a('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="%s"/>' % (LM, W - 10, y(plan[k]), y(plan[k]), cls))
                a('<text x="%d" y="%.1f" class="lv %s" text-anchor="start">%s %s</text>' % (LM + 4, y(plan[k]) - 3, cls, name, _fmt(plan[k])))
    # полосы и средние
    for series, cls in ((bu, "bb"), (bl, "bb"), (e20, "e20"), (e50, "e50")):
        pts = ["%.1f,%.1f" % (x(i), y(v)) for i, v in enumerate(series) if v is not None]
        if pts:
            a('<polyline points="%s" class="%s"/>' % (" ".join(pts), cls))
    # свечи
    bw = max(1.0, xs * 0.65)
    for i, b in enumerate(B):
        cls = "up" if b["c"] >= b["o"] else "dn"
        a('<line x1="%.1f" x2="%.1f" y1="%.1f" y2="%.1f" class="w%s"/>' % (x(i), x(i), y(b["h"]), y(b["l"]), cls))
        top, bot = y(max(b["o"], b["c"])), y(min(b["o"], b["c"]))
        a('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" class="%s"><title>%s  O %s H %s L %s C %s</title></rect>'
          % (x(i) - bw / 2, top, bw, max(1, bot - top), cls, b["t"], _fmt(b["o"]), _fmt(b["h"]), _fmt(b["l"]), _fmt(b["c"])))
    # объём
    y0 = PAD * 2 + PH
    vmax = max((b.get("v", 0) for b in B), default=1) or 1
    for i, b in enumerate(B):
        hgt = b.get("v", 0) / vmax * VH
        a('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" class="v%s"/>' % (
            x(i) - bw / 2, y0 + VH - hgt, bw, hgt, "up" if b["c"] >= b["o"] else "dn"))
    a('<text x="%d" y="%d" class="ax">объём</text>' % (LM - 6, y0 + 12))
    # RSI
    y1 = y0 + VH + PAD
    ry = lambda v: y1 + (100 - v) / 100 * RH
    for lvl in (30, 50, 70):
        a('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="%s"/>' % (LM, W - 10, ry(lvl), ry(lvl), "grid" if lvl == 50 else "rz"))
        a('<text x="%d" y="%.1f" class="ax">%d</text>' % (LM - 6, ry(lvl) + 4, lvl))
    pts = ["%.1f,%.1f" % (x(i), ry(v)) for i, v in enumerate(r) if v is not None]
    if pts:
        a('<polyline points="%s" class="rsi"/>' % " ".join(pts))
    a('<text x="%d" y="%.1f" class="lbl">RSI 14</text>' % (LM + 4, y1 + 12))
    # MACD гистограмма
    y2 = y1 + RH + PAD
    mvals = [abs(v) for v in mh if v is not None] or [1]
    mm = max(mvals) or 1
    my = lambda v: y2 + MH / 2 - v / mm * (MH / 2)
    a('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="grid"/>' % (LM, W - 10, my(0), my(0)))
    for i, v in enumerate(mh):
        if v is None:
            continue
        a('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" class="v%s"/>' % (
            x(i) - bw / 2, min(my(v), my(0)), bw, abs(my(v) - my(0)), "up" if v >= 0 else "dn"))
    a('<text x="%d" y="%.1f" class="lbl">MACD (гистограмма)</text>' % (LM + 4, y2 + 12))
    # даты
    yd = y2 + MH + 16
    step = max(1, len(B) // 8)
    for i in range(0, len(B), step):
        a('<text x="%.1f" y="%.1f" class="ax" text-anchor="middle">%s</text>' % (x(i), yd, B[i]["t"][5:16]))

    svg = '<svg viewBox="0 0 %d %d" xmlns="http://www.w3.org/2000/svg">%s</svg>' % (W, H, "".join(out))
    doc = TEMPLATE.replace("{{TITLE}}", html.escape(title)).replace("{{SVG}}", svg)
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)
    return path


def _fmt(p):
    if p >= 1000:
        return "%.0f" % p
    if p >= 10:
        return "%.2f" % p
    return "%.4g" % p


TEMPLATE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{{TITLE}}</title>
<style>
:root{--bg:#fbfbf9;--fg:#1d1d1b;--muted:#8a8a85;--grid:#e6e5e0;--up:#1f9d6b;--dn:#d64545;--e20:#2f6fdf;--e50:#c47f00;--bb:#9a8fc9;--rsi:#6b4fc2}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#161615;--fg:#ecebe6;--muted:#8d8c86;--grid:#2c2c2a;--up:#3cc28a;--dn:#ef6b6b;--e20:#6b9cff;--e50:#e2a63b;--bb:#9d93d6;--rsi:#a58cf0}}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.4 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1140px;margin:0 auto;padding:16px}
h1{font-size:18px;margin:0 0 4px}.legend{color:var(--muted);font-size:12px;margin-bottom:8px}
.legend b{font-weight:600}.k1{color:var(--e20)}.k2{color:var(--e50)}.k3{color:var(--bb)}
svg{width:100%;height:auto;display:block}
.grid{stroke:var(--grid);stroke-width:1}.ax{fill:var(--muted);font-size:10px;text-anchor:end}
.lbl{fill:var(--muted);font-size:11px}
.up{fill:var(--up)}.dn{fill:var(--dn)}.wup{stroke:var(--up)}.wdn{stroke:var(--dn)}
.vup{fill:var(--up);opacity:.45}.vdn{fill:var(--dn);opacity:.45}
.e20{fill:none;stroke:var(--e20);stroke-width:1.4}.e50{fill:none;stroke:var(--e50);stroke-width:1.4}
.bb{fill:none;stroke:var(--bb);stroke-width:1;stroke-dasharray:3 3}
.rsi{fill:none;stroke:var(--rsi);stroke-width:1.3}.rz{stroke:var(--muted);stroke-dasharray:2 3;stroke-width:.8}
.sup{stroke:var(--up);stroke-dasharray:6 4;stroke-width:1}.res{stroke:var(--dn);stroke-dasharray:6 4;stroke-width:1}
.lv{font-size:10px;text-anchor:end}.lv.sup{fill:var(--up);stroke:none}.lv.res{fill:var(--dn);stroke:none}
.pe{stroke:var(--e20);stroke-width:1.5}.ps{stroke:var(--dn);stroke-width:1.5}.pt{stroke:var(--up);stroke-width:1.5}
.lv.pe{fill:var(--e20);stroke:none}.lv.ps{fill:var(--dn);stroke:none}.lv.pt{fill:var(--up);stroke:none}
</style></head><body><main><h1>{{TITLE}}</h1>
<div class="legend"><b class="k1">— EMA20</b> &nbsp; <b class="k2">— EMA50</b> &nbsp; <b class="k3">┄ Боллинджер 20,2</b> &nbsp; пунктир: уровни поддержки (зелёный) и сопротивления (красный), ×N — число касаний</div>
{{SVG}}</main></body></html>"""
