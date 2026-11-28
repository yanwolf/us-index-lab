"""Pattern（古典型態）：型態判斷手算核對、多空鏡像、不偷看（截斷資料前後訊號相同）、進出場規則逐筆核對、範本合法"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(HERE), HERE]
import numpy as np
from synth import make
from app import engine, bars as B, templates
from app.patterns import find_patterns, PRIORITY
from app.strategies import REGISTRY

COSTS = {"spread": 1.5, "slippage": 0.5, "commission": 0.0, "swap_long_pct": 0.0, "swap_short_pct": 0.0}
T = REGISTRY["Pattern"]
ALL = set(PRIORITY)


def run(m, params=None, ck=None):
    return engine.run(T, params or {}, m, COSTS, symbol="NAS100", cache_key=ck)


def flip(m, k=40000.0):
    return {"ts": m["ts"], "o": k - m["o"], "h": k - m["l"], "l": k - m["h"], "c": k - m["c"], "v": m["v"], "sp": m["sp"]}


def names(piv, tol=1.0, a=10.0, en=ALL):
    return {(c["name"], c["side"]) for c in find_patterns(piv, a, tol, en)}


def test_find_patterns():
    """手做轉折點，a = 10、TOL 1 → 容許誤差 10 點"""
    dt = [("L", 0, 100), ("H", 10, 200), ("L", 20, 150), ("H", 30, 205)]
    assert ("雙頂", -1) in names(dt)
    c = [x for x in find_patterns(dt, 10, 1, {1})][0]
    assert c["level"](40) == 150 and c["height"] == 55 and c["stop_ref"] == 205
    assert ("雙頂", -1) not in names([("L", 0, 100), ("H", 10, 200), ("L", 20, 150), ("H", 30, 230)])
    db = [("H", 0, 300), ("L", 10, 200), ("H", 20, 250), ("L", 30, 195)]
    c = [x for x in find_patterns(db, 10, 1, {1})][0]
    assert c["name"] == "雙底" and c["side"] == 1 and c["level"](40) == 250 and c["height"] == 55 and c["stop_ref"] == 195

    hs = [("L", 0, 100), ("H", 10, 200), ("L", 20, 150), ("H", 30, 260), ("L", 40, 160), ("H", 50, 205)]
    c = [x for x in find_patterns(hs, 10, 1, {3})][0]
    assert c["name"] == "頭肩頂"
    assert abs(c["level"](60) - 170) < 1e-9          # 頸線 150→160 每 20 根 +10，第 60 根 = 170
    assert abs(c["height"] - (260 - 155)) < 1e-9      # 頭部下方的頸線 = 155
    assert ("三重頂", -1) not in names(hs)
    tt = [("L", 0, 100), ("H", 10, 200), ("L", 20, 150), ("H", 30, 205), ("L", 40, 155), ("H", 50, 198)]
    c = [x for x in find_patterns(tt, 10, 1, {2})][0]
    assert c["name"] == "三重頂" and c["level"](0) == 150 and c["height"] == 55

    asc = [("H", 0, 200), ("L", 10, 150), ("H", 20, 203), ("L", 30, 175)]
    c = [x for x in find_patterns(asc, 10, 1, {4})][0]
    assert c["name"] == "上升三角形" and c["side"] == 1 and c["level"](40) == 203 and c["height"] == 53
    desc = [("L", 0, 100), ("H", 10, 200), ("L", 20, 102), ("H", 30, 170)]
    c = [x for x in find_patterns(desc, 10, 1, {4})][0]
    assert c["name"] == "下降三角形" and c["side"] == -1 and c["level"](40) == 100

    sym = [("H", 0, 200), ("L", 10, 100), ("H", 20, 180), ("L", 30, 120)]
    c = [x for x in find_patterns(sym, 10, 1, {5})][0]
    assert c["side"] == 1 and abs(c["level"](40) - 160) < 1e-9 and c["height"] == 100   # 上緣 200→180 每根 −1，第 40 根 = 160
    rw = [("L", 0, 100), ("H", 10, 200), ("L", 20, 160), ("H", 30, 220)]               # 下緣每根 +3 比上緣 +1 陡
    c = [x for x in find_patterns(rw, 10, 1, {6})][0]
    assert c["name"] == "上升楔形" and c["side"] == -1 and abs(c["level"](40) - 220) < 1e-9
    fw = [("H", 0, 300), ("L", 10, 200), ("H", 20, 240), ("L", 30, 190)]               # 上緣每根 −3 比下緣 −0.5 陡
    c = [x for x in find_patterns(fw, 10, 1, {6})][0]
    assert c["name"] == "下降楔形" and c["side"] == 1 and abs(c["level"](40) - 180) < 1e-9
    assert not names(rw, en={5}) and not names(sym, en={6})       # 楔形不是對稱三角形，反之亦然
    print("型態判斷手算核對：雙頂底、頭肩（斜頸線）、三重頂、上升／下降／對稱三角形、上升／下降楔形 OK")


def test_mirror():
    """只做空在上下翻轉的行情上，要跟只做多在原行情上逐筆相同（每一種型態都要）"""
    m = make(days=500, seed=5, drift=0.0)
    tot = 0
    for pat in [0] + list(PRIORITY):
        lt = run(m, {"SIDE": 1, "PAT": pat}, ck=("pt", "a"))["trades"]
        st = run(flip(m), {"SIDE": -1, "PAT": pat}, ck=("pt", "b"))["trades"]
        ok = len(lt) == len(st) and all(a["entry_ts"] == b["entry_ts"] and a["exit_ts"] == b["exit_ts"]
                                        and abs(a["gross"] - b["gross"]) < 1e-6 and a["out"] == b["out"]
                                        for a, b in zip(lt, st))
        assert ok, (pat, len(lt), len(st))
        tot += len(lt)
    print(f"鏡像對稱：7 種 PAT 設定共 {tot} 筆多單，翻轉後空單逐筆相同")
    assert tot > 20


def test_no_lookahead():
    """把資料截在中間：截點前已經出場的交易，截斷版要逐筆一樣（後面的資料不能影響前面的訊號）"""
    m = make(days=600, seed=21, drift=0.0)
    base = {x: v for x, v in m.items() if x in ("ts", "o", "h", "l", "c", "v", "sp")}
    full = run(m, {}, ck=("pt", "c"))["trades"]
    for frac in (0.4, 0.7):
        k = int(len(m["ts"]) * frac)
        mm = {x: v[:k] for x, v in base.items()}
        cut = mm["ts"][-1] - 86400 * 3
        part = [t for t in run(mm, {}, ck=("pt", f"d{frac}"))["trades"] if t["exit_ts"] < cut]
        ref = [t for t in full if t["exit_ts"] < cut]
        assert len(part) == len(ref) and all(a["entry_ts"] == b["entry_ts"] and a["exit_ts"] == b["exit_ts"]
                                             and a["in"] == b["in"] for a, b in zip(part, ref)), frac
        print(f"不偷看：截在 {frac:.0%}，截點前 {len(ref)} 筆交易逐筆相同")


def test_rules():
    """每筆：下一根開盤成交、出場原因只會是停損／量度目標／週末／持有到期；停損停利價位核對"""
    m = make(days=600, seed=13, drift=0.0)
    r = run(m, {"MAXB": 30, "WKX": 0}, ck=("pt", "e"))
    b = B.build(B.minute_frame(m, "NAS100"), 60, cache_key=("pt", "e"), symbol="NAS100")
    outs, pats = {}, {}
    for t in r["trades"]:
        k = int(np.searchsorted(b.ts_start, t["entry_ts"], side="right")) - 1
        assert abs(t["entry"] - b.o[k]) < 0.006, "市價單要在下一根開盤成交"
        outs[t["out"]] = outs.get(t["out"], 0) + 1
        pats[t["in"]] = pats.get(t["in"], 0) + 1
        if t["out"] == "持有到期":
            assert t["bars"] >= 29
    assert set(outs) <= {"停損", "量度目標", "持有到期", "回測結束"}, outs
    print(f"{len(r['trades'])} 筆；進場型態 {pats}；出場 {outs}")
    assert len(pats) >= 4


def test_params():
    m = make(days=400, seed=3, drift=0.0)
    n = {zz: len(run(m, {"ZZ": zz}, ck=("pt", "f"))["trades"]) for zz in (2, 3, 5)}
    print(f"ZZ 越大、型態越少：{n}")
    assert n[2] >= n[3] >= n[5]
    lo = run(m, {"SIDE": 1}, ck=("pt", "f"))["trades"]
    so = run(m, {"SIDE": -1}, ck=("pt", "f"))["trades"]
    assert all(t["dir"] == 1 for t in lo) and all(t["dir"] == -1 for t in so)
    rr = run(m, {"MINRR": 1.5}, ck=("pt", "f"))["trades"]
    assert len(rr) <= len(run(m, {}, ck=("pt", "f"))["trades"])


def test_templates():
    ts = [t for t in templates.templates() if t["strategy"] == "Pattern"]
    assert ts, "應該有 Pattern 範本"
    for t in ts:
        assert set(t["params"]) <= set(T.all_inputs()), t["name"]
        assert set(t["grid"]) <= set(T.all_inputs()), t["name"]
    print(f"範本 {len(ts)} 個，參數名稱都合法")


if __name__ == "__main__":
    test_find_patterns()
    test_mirror()
    test_no_lookahead()
    test_rules()
    test_params()
    test_templates()
    print("OK")
