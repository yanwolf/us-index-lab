"""TurtleMA（刀神海龜突破＋均線三刀流）：鏡像對稱、規則逐筆核對、不偷看、SIDE、範本、與 GuYuan_2025 的相關性"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(HERE), HERE]
import numpy as np
from synth import make
from app import engine, bars as B, templates
from app.optimize import combos
from app.strategies import REGISTRY

COSTS = {"spread": 1.5, "slippage": 0.5, "commission": 0.0, "swap_long_pct": 0.0, "swap_short_pct": 0.0}
T = REGISTRY["TurtleMA"]


def run(m, params=None, ck=None, name="TurtleMA", minutes=None):
    return engine.run(REGISTRY[name], params or {}, m, COSTS, minutes=minutes, symbol="NAS100", cache_key=ck)


def flip(m, k=40000.0):
    return {"ts": m["ts"], "o": k - m["o"], "h": k - m["l"], "l": k - m["h"], "c": k - m["c"], "v": m["v"], "sp": m["sp"]}


def test_mirror():
    """只做空在上下翻轉的行情上，要跟只做多在原行情上逐筆相同"""
    m = make(days=500, seed=5, drift=0.0)
    lt = run(m, {"SIDE": 1}, ck=("tm", "a"))["trades"]
    st = run(flip(m), {"SIDE": -1}, ck=("tm", "b"))["trades"]
    ok = len(lt) == len(st) and all(a["entry_ts"] == b["entry_ts"] and a["exit_ts"] == b["exit_ts"]
                                    and abs(a["gross"] - b["gross"]) < 1e-6 for a, b in zip(lt, st))
    print(f"鏡像對稱：多 {len(lt)} 筆、空(翻轉) {len(st)} 筆，逐筆相同 = {ok}")
    assert ok and len(lt) > 5 and all(t["dir"] == -1 for t in st)


def test_side_and_exits():
    m = make(days=500, seed=9, drift=0.0)
    both = run(m, {}, ck=("tm", "c"))["trades"]
    lo = run(m, {"SIDE": 1}, ck=("tm", "c"))["trades"]
    so = run(m, {"SIDE": -1}, ck=("tm", "c"))["trades"]
    assert all(t["dir"] == 1 for t in lo) and all(t["dir"] == -1 for t in so)
    assert {t["dir"] for t in both} == {1, -1}
    outs = {}
    for t in both:
        outs[t["out"]] = outs.get(t["out"], 0) + 1
    print(f"多空 {len(both)} 筆（多 {len(lo)}、空 {len(so)}）；出場原因 {outs}")
    assert not any("停利" in k for k in outs), "TurtleMA 不該有固定停利"


def test_rules_no_lookahead():
    """每筆進場都要符合：進場前一根收盤時方向 + 均線排列 + 斜率；而且以下一根開盤成交"""
    m = make(days=400, seed=13, drift=0.0)
    p = T.all_inputs()
    r = run(m, {}, ck=("tm", "d"))
    b = B.build(B.minute_frame(m, "NAS100"), 60, cache_key=("tm", "d"), symbol="NAS100")
    m1, m2, m3 = B.sma(b, "C", p["MA1"]), B.sma(b, "C", p["MA2"]), B.sma(b, "C", p["MA3"])
    hh, ll = B.highest(b, "H", p["DC"]), B.lowest(b, "L", p["DC"])
    d = np.zeros(b.n)                       # 重算方向狀態
    for i in range(b.n):
        d[i] = d[i - 1] if i else 0
        if i >= p["DC"]:
            if b.c[i] > hh[i - 1]:
                d[i] = 1
            elif b.c[i] < ll[i - 1]:
                d[i] = -1
    checked = 0
    for t in r["trades"]:
        k = int(np.searchsorted(b.ts_start, t["entry_ts"], side="right")) - 1   # 成交那根
        s = k - 1                                                               # 訊號那根
        assert abs(t["entry"] - b.o[k]) < 0.006, "市價單要在下一根開盤成交"
        if t["dir"] > 0:
            assert d[s] == 1 and m1[s] > m2[s] > m3[s] and m1[s] > m1[s - 1]
        else:
            assert d[s] == -1 and m1[s] < m2[s] < m3[s] and m1[s] < m1[s - 1]
        checked += 1
    print(f"規則核對：{checked} 筆進場全部符合（方向用前一根以前的高低點、收盤確認）")
    assert checked > 5


def test_templates():
    ts = [t for t in templates.templates() if t["strategy"] == "TurtleMA"]
    assert len(ts) == 2, len(ts)
    for t in ts:
        assert set(t["params"]) <= set(T.all_inputs()), t["name"]
        assert set(t["grid"]) <= set(T.all_inputs()), t["name"]


def test_corr_vs_guyuan2025():
    """合成資料上看兩支的每日損益相關性（真實結論要在組合頁用真實資料看）"""
    m = make(days=500, seed=17)
    a = run(m, {}, ck=("tm", "e"))["daily"]
    g = run(m, {}, ck=("tm", "f"), name="GuYuan_2025")["daily"]
    da = dict((td, eq) for td, eq, _ in a); dg = dict((td, eq) for td, eq, _ in g)
    days = sorted(set(da) & set(dg))
    ra = np.diff([da[x] for x in days]); rg = np.diff([dg[x] for x in days])
    c = float(np.corrcoef(ra, rg)[0, 1]) if ra.std() > 0 and rg.std() > 0 else float("nan")
    print(f"合成資料每日損益相關係數 TurtleMA vs GuYuan_2025 = {c:.2f}（僅供參考）")


if __name__ == "__main__":
    test_mirror()
    test_side_and_exits()
    test_rules_no_lookahead()
    test_templates()
    test_corr_vs_guyuan2025()
    print("OK")
