"""匯入 tools/mt5_probe.py 匯出的 CSV，並和 Dukascopy 比對價差與點差。

CSV 欄位：time_utc（YYYY-MM-DD HH:MM:SS，UTC）, open, high, low, close, tick_volume, spread_price
"""
import io

import numpy as np
import pandas as pd

from . import store


def import_csv(raw_bytes, broker, symbol):
    df = pd.read_csv(io.BytesIO(raw_bytes))
    need = {"time_utc", "open", "high", "low", "close"}
    if not need.issubset(df.columns):
        raise ValueError(f"CSV 欄位不符，需要 {sorted(need)}，實際是 {list(df.columns)}")
    ts = pd.to_datetime(df["time_utc"], utc=True).dt.as_unit("s").astype("int64")
    out = pd.DataFrame({"ts": ts, "o": df["open"], "h": df["high"], "l": df["low"], "c": df["close"],
                        "v": df.get("tick_volume", np.nan), "sp": df.get("spread_price", np.nan)})
    source = "mt5-" + "".join(ch for ch in broker.lower() if ch.isalnum())
    n = store.save(source, symbol, out)
    return {"source": source, "symbol": symbol, "rows": n,
            "first": str(pd.to_datetime(ts.min(), unit="s", utc=True)), "last": str(pd.to_datetime(ts.max(), unit="s", utc=True))}


def compare(symbol, source_a, source_b):
    a, b = store.load(source_a, symbol), store.load(source_b, symbol)
    da = pd.DataFrame({"ts": a["ts"], "ca": a["c"]})
    db = pd.DataFrame({"ts": b["ts"], "cb": b["c"], "sp": b["sp"]})
    j = da.merge(db, on="ts")
    if len(j) < 100:
        return {"overlap": int(len(j)), "note": "重疊的分鐘太少，無法比較（確認兩邊時間都是 UTC、期間有重疊）"}
    diff = j["cb"] - j["ca"]
    ra, rb = np.diff(np.log(j["ca"].to_numpy())), np.diff(np.log(j["cb"].to_numpy()))
    et = pd.to_datetime(j["ts"], unit="s", utc=True).dt.tz_convert("America/New_York")
    hm = et.dt.hour * 100 + et.dt.minute
    rth = (hm >= 930) & (hm < 1600)
    out = {"overlap": int(len(j)),
           "from": str(et.iloc[0])[:16], "to": str(et.iloc[-1])[:16],
           "diff_mean": round(float(diff.mean()), 3), "diff_std": round(float(diff.std()), 3),
           "diff_p95_abs": round(float(diff.abs().quantile(0.95)), 3),
           "ret_corr": round(float(np.corrcoef(ra, rb)[0, 1]), 4)}
    sp = j["sp"]
    if sp.notna().any():
        out["spread_median"] = round(float(sp.median()), 3)
        out["spread_rth"] = round(float(sp[rth].median()), 3) if rth.any() else None
        out["spread_eth"] = round(float(sp[~rth].median()), 3) if (~rth).any() else None
        byh = sp.groupby(et.dt.hour).median()
        out["spread_by_hour"] = [[int(h), round(float(v), 3)] for h, v in byh.items()]
    return out
