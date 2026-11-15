"""GuYuan_2024 多空：舊版相容、鏡像對稱、EBX、CloseS 時段定義"""
import os, sys, importlib.util
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(HERE), HERE]
import numpy as np
from synth import make, make_jp
from app import engine, bars as B
from app.strategies import REGISTRY
from app.instruments import cost_defaults

COSTS = {"spread": 1.5, "slippage": 0.5, "commission": 0.0, "swap_long_pct": 0.0, "swap_short_pct": 0.0}


def run(name, m, params=None, sym="NAS100", ck=None):
    return engine.run(REGISTRY[name], params or {}, m, COSTS, symbol=sym, cache_key=ck)["trades"]


def flip(m, k=40000.0):
    return {"ts": m["ts"], "o": k - m["o"], "h": k - m["l"], "l": k - m["h"], "c": k - m["c"], "v": m["v"], "sp": m["sp"]}


def test_old_compat(old_dir):
    """預設參數（EBX=0、SES=0、均線關）要跟上一版 GuYuan_2024 一模一樣"""
    sys.path.insert(0, old_dir)
    for k in [k for k in sys.modules if k == "app" or k.startswith("app.")]:
        del sys.modules[k]
    import app.engine as oe, app.strategies as os_
    m = make(days=500, seed=21)
    old = oe.run(os_.REGISTRY["GuYuan_2024"], {}, {k: v.copy() for k, v in m.items()}, COSTS, symbol="NAS100")["trades"]
    sys.path.remove(old_dir)
    for k in [k for k in sys.modules if k == "app" or k.startswith("app.")]:
        del sys.modules[k]
    import app.engine as ne, app.strategies as ns
    new = ne.run(ns.REGISTRY["GuYuan_2024"], {}, {k: v.copy() for k, v in m.items()}, COSTS, symbol="NAS100")["trades"]
    same = len(old) == len(new) and all(a["entry_ts"] == b["entry_ts"] and abs(a["net"] - b["net"]) < 1e-6 for a, b in zip(old, new))
    print(f"舊版相容：舊 {len(old)} 筆、新 {len(new)} 筆，逐筆相同 = {same}")
    assert same


def test_mirror():
    """空方在「上下翻轉」的行情上，要跟多方在原行情上的交易一模一樣"""
    m = make(days=500, seed=33)
    pl = dict(N1=3, N2=3, N3=5, HH=10, LL=14, LOSS=1.1, WIN=4.4, Twin=0.3, Tstop=3.3, EBX=2)
    ps = dict(pl, HH=14, LL=10)          # 空方的進場是 LL、出場是 HH
    lt = run("GuYuan_2024", m, pl, ck=("mir", "a"))
    st = run("GuYuan_2024_S", flip(m), ps, ck=("mir", "b"))
    ok = len(lt) == len(st) and all(a["entry_ts"] == b["entry_ts"] and a["exit_ts"] == b["exit_ts"] and
                                    abs(a["gross"] - b["gross"]) < 1e-6 and a["out"] == b["out"] for a, b in zip(lt, st))
    print(f"鏡像對稱：多 {len(lt)} 筆、空(翻轉) {len(st)} 筆，逐筆相同 = {ok}；"
          f"EBX 出場 {sum(t['out'] == '進場K災難停損' for t in lt)} 筆")
    assert ok and all(t["dir"] == -1 for t in st)


def test_ebx():
    m = make(days=500, seed=44)
    b = B.build(B.minute_frame(m, "NAS100"), 60, symbol="NAS100")
    ar = B.avgrange(b, 200)
    for x in (0, 0.5, 1, 4):
        tr = run("GuYuan_2024_S", m, {"EBX": x}, ck=("ebx", "s"))
        ex = [t for t in tr if t["out"] == "進場K災難停損"]
        bad = 0
        for t in ex:
            i = int(np.searchsorted(b.ts_start, t["entry_ts"], side="right")) - 1
            j = int(np.searchsorted(b.ts_start, t["exit_ts"], side="right")) - 1
            dist = ar[i - 1] * x
            if i != j or t["exit"] < t["entry"] + dist - 0.011:      # 同一根 K、出場價不比停損價好
                bad += 1
        print(f"EBX={x}: {len(tr)} 筆，災難停損出場 {len(ex)} 筆，檢查不符 {bad}")
        assert bad == 0 and (x > 0 or not ex)


def test_ses_modes():
    for sym, m in (("NAS100", make(days=300, seed=55)), ("JP225", make_jp(days=300, seed=56))):
        mf = B.minute_frame(m, sym)
        b = B.build(mf, 60, symbol=sym)
        idx1, c1 = B.ses_series(b, 1)
        assert np.array_equal(idx1, b.day_idx) and np.array_equal(c1, b.day_close)
        for mode in (0, 1, 2, 3):
            idx, cl = B.ses_series(b, mode)
            assert np.all(np.diff(idx) >= 0) and len(cl) == idx[-1] + 1
            per_day = (idx[-1] + 1) / (b.day_idx[-1] + 1)
            tr = run("GuYuan_2024", m, {"SES": mode}, sym=sym, ck=("ses", sym))
            print(f"{sym} SES={mode}: 每交易日 {per_day:.2f} 段，GuYuan_2024 {len(tr)} 筆")


if __name__ == "__main__":
    test_old_compat(sys.argv[1] if len(sys.argv) > 1 else "/tmp/claude-0/old/us-index-lab")
    test_mirror(); test_ebx(); test_ses_modes(); print("OK")
