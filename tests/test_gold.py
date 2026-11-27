"""黃金 XAUUSD（2026-11-26 加）：商品設定、時段切 K、交易日、庫存費方向、所有策略跑得動、範本、Dukascopy 價格倍率。
執行：python tests/test_gold.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
from synth import make
from app import engine, metrics, bars as B, dukascopy, templates
from app.strategies import REGISTRY
from app.instruments import INSTRUMENTS, cost_defaults


def gold(days=400, seed=21):
    return make(days=days, price=4100.0, seed=seed)      # 合成資料的時段（週日 18:00–週五 17:00 美東，每天 17–18 休市）正好跟黃金一樣


def test_instrument():
    I = INSTRUMENTS["XAUUSD"]
    assert (I["duka"], I["tz"], I["day_roll"], I["rth"], I["eth_start"]) == ("XAUUSD", "America/New_York", 1700, (820, 1330), 1330)
    c = cost_defaults("XAUUSD")
    assert c["spread"] == 0.25 and c["slippage"] == 0.1 and c["usd_per_point"] == 1.0, c
    assert c["swap_long_pct"] > 0 > c["swap_short_pct"], "做多付、做空收"
    # 換算檢查：每個交易日收一次 年化% ÷ 360 × 價格 ≈ 券商每週 7 晚的平均
    per_day_long = c["swap_long_pct"] / 100 / 360 * 4100
    assert abs(per_day_long - 0.81 * 7 / 5) < 0.05, per_day_long
    per_day_short = c["swap_short_pct"] / 100 / 360 * 4100
    assert abs(per_day_short + 0.28 * 7 / 5) < 0.05, per_day_short


def test_sessions_and_tday():
    m = gold(days=30)
    f = B.minute_frame(m, "XAUUSD")
    b = B.build(f, 60, symbol="XAUUSD")
    et = pd.to_datetime(b.ts_start, unit="s", utc=True).tz_convert("America/New_York")
    starts = set(et.hour * 100 + et.minute)
    assert 820 in starts and 1330 in starts, sorted(starts)
    assert 930 not in starts, "不是美股的 09:30 切法"
    # 17:00 之後(18:00 開盤)那段算隔天的交易日
    et_m = pd.to_datetime(m["ts"], unit="s", utc=True).tz_convert("America/New_York")
    tday = np.asarray(f["tday"])
    i_eve = int(np.argmax((et_m.hour == 18) & (et_m.dayofweek == 1)))      # 週二 18:00
    i_mor = int(np.argmax((et_m.hour == 10) & (et_m.dayofweek == 2)))      # 週三 10:00
    i_aft = int(np.argmax((et_m.hour == 16) & (et_m.dayofweek == 1)))      # 週二 16:00
    assert et_m[i_eve].dayofweek == 1 and et_m[i_mor].dayofweek == 2, "前提：找到對的時間點"
    assert tday[i_eve] == tday[i_mor] != tday[i_aft], (tday[i_eve], tday[i_mor], tday[i_aft])


def test_all_strategies_run():
    for name, cls in REGISTRY.items():
        m = gold(days=500, seed=31)
        r = engine.run(cls, {}, m, cost_defaults("XAUUSD"), cache_key=("synth-gold", name), symbol="XAUUSD")
        s = metrics.summarize(r["trades"], r["daily"], r["bars_in_pos"], r["n_bars"])
        print(f"XAUUSD {name:14s} trades={s['trades']:4d} net={s['net_pts']:9.1f} pf={s['pf']:5.2f}")
        for t in r["trades"]:
            assert t["exit_ts"] >= t["entry_ts"]
            held_days = (t["exit_ts"] - t["entry_ts"]) / 86400
            if t["swap"] and held_days > 0:
                assert (t["swap"] > 0) == (t["dir"] > 0), ("做多付庫存費、做空收", t)


def test_templates():
    ts = [t for t in templates.templates() if t["symbol"] == "XAUUSD" and t["group"].startswith("B.")]
    assert len(ts) == 6, len(ts)
    for t in ts:
        assert t["strategy"] in REGISTRY, t
        cls = REGISTRY[t["strategy"]]
        assert set(t["params"]) <= set(cls.all_inputs()), (t["name"], set(t["params"]) - set(cls.all_inputs()))
    assert templates.SYM_NAME["XAUUSD"] == "黃金"


def test_duka_divisor():
    # Dukascopy 原始整數：金價 4100.123 → 4100123（倍率 1000）
    raw = np.array([[60 * k, 4100000 + k, 4100500 + k, 4099500 + k, 4100200 + k, 1.0] for k in range(50)], dtype=float)
    fmt = dukascopy.detect_format(raw, INSTRUMENTS["XAUUSD"]["price_range"])
    assert fmt["div"] == 1000, fmt
    raw2 = raw.copy(); raw2[:, 1:5] = raw2[:, 1:5] / 10          # 萬一是兩位小數：也要判斷得出來
    assert dukascopy.detect_format(raw2, INSTRUMENTS["XAUUSD"]["price_range"])["div"] == 100


if __name__ == "__main__":
    test_instrument(); test_sessions_and_tday(); test_templates(); test_duka_divisor(); test_all_strategies_run()
    print("OK")
