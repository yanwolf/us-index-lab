"""合成 1 分 K（美股 CFD 時段：週日 18:00–週五 17:00 美東，每日 17:00–18:00 休市）給測試用。"""
import numpy as np
import pandas as pd


def make(days=400, start="2024-01-07 18:00", price=4800.0, seed=7, drift=0.00002):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=days * 1440, freq="1min", tz="America/New_York")
    wd = idx.dayofweek; hm = idx.hour * 100 + idx.minute
    keep = ~((wd == 5) | ((wd == 6) & (hm < 1800)) | ((wd == 4) & (hm >= 1700)) | ((hm >= 1700) & (hm < 1800)))
    idx = idx[keep]
    n = len(idx)
    vol = np.where((hm[keep] >= 930) & (hm[keep] < 1600), 0.00045, 0.00022)
    r = rng.normal(drift / 60, vol)
    c = price * np.exp(np.cumsum(r))
    o = np.concatenate([[price], c[:-1]])
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, vol / 3)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, vol / 3)))
    ts = idx.tz_convert("UTC").as_unit("s").asi8.astype("int64")
    return {"ts": ts, "o": o, "h": h, "l": l, "c": c, "v": np.ones(n), "sp": np.full(n, np.nan)}


def make_jp(days=200, start="2024-01-08 00:00", price=33000.0, seed=11):
    """日經 CFD 時段（東京時間）：日盤 08:45–15:45、夜盤 17:00–翌日 06:00；週六 06:00 收到週一 08:45。"""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=days * 1440, freq="1min", tz="Asia/Tokyo")
    wd = idx.dayofweek; hm = idx.hour * 100 + idx.minute
    day = (hm >= 845) & (hm < 1545) & (wd < 5)
    night = (((hm >= 1700) & (wd < 5)) | ((hm < 600) & (wd >= 1) & (wd <= 5)))
    idx = idx[day | night]
    n = len(idx)
    r = rng.normal(0, 0.0003, n)
    c = price * np.exp(np.cumsum(r))
    o = np.concatenate([[price], c[:-1]])
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.0001, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.0001, n)))
    ts = idx.tz_convert("UTC").as_unit("s").asi8.astype("int64")
    return {"ts": ts, "o": o, "h": h, "l": l, "c": c, "v": np.ones(n), "sp": np.full(n, np.nan)}
