"""1 分 K 資料庫：DATA_DIR/<source>/<symbol>/<YYYY-MM>.parquet

欄位：ts（UTC 秒，該分鐘起始）、o、h、l、c、v、sp（點差，價格單位；沒有就是 NaN）
source：duka（Dukascopy）或 mt5-<券商>（從 MT5 探測腳本匯入）
"""
import json
import os
import threading

import numpy as np
import pandas as pd

DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"))
COLS = ["ts", "o", "h", "l", "c", "v", "sp"]
_lock = threading.Lock()
_cache = {}            # (source, symbol) -> dict of numpy arrays（整份）


def _dir(source, symbol):
    return os.path.join(DATA_DIR, source, symbol)


def _norm(df):
    df = df.copy()
    for c in COLS:
        if c not in df.columns:
            df[c] = np.nan
    df = df[COLS]
    df["ts"] = df["ts"].astype("int64")
    for c in COLS[1:]:
        df[c] = df[c].astype("float64")
    return df


def save(source, symbol, df):
    """合併寫入（同一分鐘以新資料為準），按月分檔。"""
    if df is None or len(df) == 0:
        return 0
    df = _norm(df)
    d = _dir(source, symbol)
    os.makedirs(d, exist_ok=True)
    ym = pd.to_datetime(df["ts"], unit="s", utc=True).dt.strftime("%Y-%m")
    with _lock:
        for key, part in df.groupby(ym.values):
            path = os.path.join(d, f"{key}.parquet")
            if os.path.exists(path):
                part = pd.concat([pd.read_parquet(path), part])
            part = part.drop_duplicates("ts", keep="last").sort_values("ts")
            part.to_parquet(path, index=False)
        _write_meta(source, symbol)
        _cache.pop((source, symbol), None)
    return len(df)


def _write_meta(source, symbol):
    d = _dir(source, symbol)
    files = sorted(f for f in os.listdir(d) if f.endswith(".parquet"))
    rows, first, last, months = 0, None, None, []
    for f in files:
        t = pd.read_parquet(os.path.join(d, f), columns=["ts"])["ts"]
        if len(t) == 0:
            continue
        rows += len(t)
        first = int(t.min()) if first is None else min(first, int(t.min()))
        last = int(t.max()) if last is None else max(last, int(t.max()))
        months.append({"month": f[:-8], "rows": int(len(t))})
    meta = {"rows": rows, "first": first, "last": last, "months": months}
    with open(os.path.join(d, "meta.json"), "w") as fh:
        json.dump(meta, fh)
    return meta


def has_month(source, symbol, ym, min_rows=1):
    p = os.path.join(_dir(source, symbol), f"{ym}.parquet")
    if not os.path.exists(p):
        return False
    try:
        return len(pd.read_parquet(p, columns=["ts"])) >= min_rows
    except Exception:
        return False


def days_present(source, symbol, ym):
    """該月已有資料的 UTC 日期集合（下載器用來跳過已抓過的日子）。"""
    p = os.path.join(_dir(source, symbol), f"{ym}.parquet")
    if not os.path.exists(p):
        return set()
    t = pd.read_parquet(p, columns=["ts"])["ts"]
    return set(pd.to_datetime(t, unit="s", utc=True).dt.strftime("%Y-%m-%d").unique())


def inventory():
    out = []
    if not os.path.isdir(DATA_DIR):
        return out
    for source in sorted(os.listdir(DATA_DIR)):
        sd = os.path.join(DATA_DIR, source)
        if not os.path.isdir(sd):
            continue
        for symbol in sorted(os.listdir(sd)):
            md = os.path.join(sd, symbol, "meta.json")
            if os.path.exists(md):
                with open(md) as fh:
                    m = json.load(fh)
                out.append({"source": source, "symbol": symbol, "rows": m["rows"], "first": m["first"],
                            "last": m["last"], "months": m.get("months", [])})
    return out


def load(source, symbol):
    """整份讀進記憶體（3 年約 110 萬列、50MB 左右），快取起來給回測與最佳化重複用。"""
    key = (source, symbol)
    with _lock:
        if key in _cache:
            return _cache[key]
    d = _dir(source, symbol)
    if not os.path.isdir(d):
        raise FileNotFoundError(f"沒有 {source}/{symbol} 的資料，請先到「資料」頁下載或匯入")
    files = sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".parquet"))
    if not files:
        raise FileNotFoundError(f"{source}/{symbol} 沒有任何資料檔")
    df = pd.concat([pd.read_parquet(f) for f in files]).drop_duplicates("ts").sort_values("ts")
    arr = {c: df[c].to_numpy() for c in COLS}
    with _lock:
        _cache[key] = arr
    return arr


def delete(source, symbol):
    import shutil
    d = _dir(source, symbol)
    with _lock:
        if os.path.isdir(d):
            shutil.rmtree(d)
        _cache.pop((source, symbol), None)
