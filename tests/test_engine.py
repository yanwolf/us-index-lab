import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from synth import make
from app import engine, metrics, bars as B
from app.strategies import REGISTRY
from app.instruments import cost_defaults


def test_all():
    m = make(days=1100)
    costs = cost_defaults("US500")
    for name, cls in REGISTRY.items():
        t0 = time.time()
        r = engine.run(cls, {}, m, costs, cache_key=("synth", "US500"))
        s = metrics.summarize(r["trades"], r["daily"], r["bars_in_pos"], r["n_bars"])
        dt = time.time() - t0
        print(f"{name:14s} {dt:5.2f}s trades={s['trades']:4d} net={s['net_pts']:9.1f} pf={s['pf']:5.2f} mdd={s['mdd_pts']:7.1f} exp={s['exposure_pct']}%")
        tr = r["trades"]
        # 不變式：沒有交易跨週末（週五 TNw 後出場）
        import pandas as pd
        for t in tr:
            et_in = pd.Timestamp(t["entry_ts"], unit="s", tz="UTC").tz_convert("America/New_York")
            et_out = pd.Timestamp(t["exit_ts"], unit="s", tz="UTC").tz_convert("America/New_York")
            assert (et_out - et_in).days < 4, (name, t)
            assert t["exit_ts"] >= t["entry_ts"]
        if cls.name in ("AR_crossover", "GuYuan_2024", "GuYuan_2025"):
            assert all(t["dir"] == 1 for t in tr)
        if cls.name == "AR_crossunder":
            assert all(t["dir"] == -1 for t in tr)


def test_bars():
    m = make(days=30)
    b = B.build(B.minute_frame(m), 60)
    import pandas as pd
    et = pd.to_datetime(b.ts_start, unit="s", utc=True).tz_convert("America/New_York")
    starts = sorted(set((et.hour * 100 + et.minute)))
    print("60 分 K 起點（美東）:", starts)
    assert 930 in starts and 1600 in starts and 1800 in starts
    # RTH 09:30–15:30 的 K 是完整 60 根 1 分（最後一根 15:30–16:00 是 30 根）
    sizes = b.m1 - b.m0
    hm = (et.hour * 100 + et.minute).to_numpy()
    assert (sizes[hm == 1030] == 60).all() and (sizes[hm == 1530] == 30).all()


if __name__ == "__main__":
    test_bars(); test_all(); print("OK")
