"""Dukascopy 免費歷史資料下載器（1 分 K，BID 價）。

來源：https://datafeed.dukascopy.com/datafeed/<代碼>/<年>/<月-1>/<日>/BID_candles_min_1.bi5
  · 一天一個檔（UTC 日），LZMA 壓縮，每筆 24 bytes：
    int32 當日秒數、4 個 int32 價格、float32 量
  · 月份從 0 起算（一月 = 00）
  · 休市時段 Dukascopy 會補「平盤 K」（OHLC 相同、量 0），這裡會濾掉

兩件事用自動判斷，不寫死，避免規格認錯：
  1. 價格倍率：試 1000/100/10/1/10000/100000，取讓價格落在商品合理區間的那個
  2. 四個價格欄位的開收順序（OCLH 或 OHLC）：取「本分鐘開盤 ≈ 上一分鐘收盤」比較成立的那種
     高低一律取四個價格的最大最小，不受順序影響
判斷結果記在 DATA_DIR/duka/<symbol>/format.json，之後沿用。
"""
import json
import lzma
import os
import struct
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from . import store
from .instruments import INSTRUMENTS

BASE = "https://datafeed.dukascopy.com/datafeed"
UA = {"User-Agent": "Mozilla/5.0 (us-index-lab historical downloader)"}
REC = struct.Struct(">5if")
WORKERS = int(os.getenv("DUKA_WORKERS", "4"))
DIVISORS = [1000, 100, 10, 1, 10000, 100000]


def url_for(code, d):
    return f"{BASE}/{code}/{d.year}/{d.month - 1:02d}/{d.day:02d}/BID_candles_min_1.bi5"


def fetch_raw(code, d, retries=4):
    """回傳解壓後的 bytes；沒有資料（404 或空檔）回 b""。"""
    url = url_for(code, d)
    for k in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            if not data:
                return b""
            return lzma.decompress(data, format=lzma.FORMAT_ALONE)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return b""
            err = e
        except Exception as e:      # 逾時、連線中斷、解壓失敗
            err = e
        time.sleep(1.5 * (k + 1))
    raise RuntimeError(f"{d} 下載失敗：{err!r}")


def parse(raw):
    """→ ndarray (n, 6)：秒數、p0..p3（原始整數）、量"""
    n = len(raw) // REC.size
    if n == 0:
        return np.empty((0, 6))
    a = np.frombuffer(raw[: n * REC.size], dtype=np.dtype([("t", ">i4"), ("p", ">i4", 4), ("v", ">f4")]))
    out = np.empty((n, 6))
    out[:, 0] = a["t"]
    out[:, 1:5] = a["p"]
    out[:, 5] = a["v"]
    return out


def detect_format(rows, price_range):
    """rows：多天合併的 parse() 結果（已依時間排序）。回 {"div":..., "oc":[開欄, 收欄]}"""
    lo, hi = price_range
    med = float(np.median(rows[:, 1:5]))
    div = next((d for d in DIVISORS if lo <= med / d <= hi), None)
    if div is None:
        raise RuntimeError(f"判斷不出價格倍率（原始中位數 {med}），請檢查商品代碼")
    live = rows[rows[:, 5] > 0]
    best, best_err = None, None
    for oc in ((1, 2), (1, 4)):            # (p0,p1)=OCLH、(p0,p3)=OHLC
        o, c = live[1:, oc[0]], live[:-1, oc[1]]
        err = float(np.median(np.abs(o - c)))
        if best_err is None or err < best_err:
            best, best_err = oc, err
    return {"div": div, "oc": list(best)}


def to_frame(day, rows, fmt):
    if len(rows) == 0:
        return None
    p = rows[:, 1:5] / fmt["div"]
    v = rows[:, 5]
    o, c = rows[:, fmt["oc"][0]] / fmt["div"], rows[:, fmt["oc"][1]] / fmt["div"]
    h, l = p.max(axis=1), p.min(axis=1)
    keep = ~((v <= 0) & (h == l))           # 濾掉休市平盤 K
    base = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
    df = pd.DataFrame({"ts": (base + rows[:, 0]).astype("int64"), "o": o, "h": h, "l": l, "c": c, "v": v})
    return df[keep]


def _fmt_path(symbol):
    return os.path.join(store.DATA_DIR, "duka", symbol, "format.json")


def load_fmt(symbol):
    p = _fmt_path(symbol)
    if os.path.exists(p):
        with open(p) as fh:
            return json.load(fh)
    return None


def save_fmt(symbol, fmt):
    os.makedirs(os.path.dirname(_fmt_path(symbol)), exist_ok=True)
    with open(_fmt_path(symbol), "w") as fh:
        json.dump(fmt, fh)


def download(symbol, start, end, progress=None, stop_flag=None, refresh_days=3):
    """下載 [start, end]（date）的 1 分 K 存進 store。已有的日子跳過，最近 refresh_days 天重抓。"""
    inst = INSTRUMENTS[symbol]
    code = inst["duka"]
    days = []
    d = start
    today = datetime.now(timezone.utc).date()
    while d <= min(end, today):
        if d.weekday() != 5:                # 週六整天休市，不抓
            days.append(d)
        d += timedelta(days=1)
    have = {}
    todo = []
    for d in days:
        ym = d.strftime("%Y-%m")
        if ym not in have:
            have[ym] = store.days_present("duka", symbol, ym)
        if d.isoformat() in have[ym] and (today - d).days > refresh_days:
            continue
        todo.append(d)

    fmt = load_fmt(symbol)
    log = []
    if fmt is None and todo:
        # 先抓幾個平日判斷格式
        sample = [x for x in todo if x.weekday() < 5][:6]
        rows = [parse(fetch_raw(code, x)) for x in sample]
        rows = [r for r in rows if len(r)]
        if not rows:
            raise RuntimeError("抓不到樣本資料，可能是網路被擋或商品代碼錯誤")
        fmt = detect_format(np.vstack(rows), inst["price_range"])
        save_fmt(symbol, fmt)
        log.append(f"格式判斷：價格倍率 ÷{fmt['div']}、開收欄位 {fmt['oc']}")

    total, done, saved, failed = len(todo), 0, 0, []
    buf = []
    lock = threading.Lock()

    def flush():
        nonlocal buf, saved
        if buf:
            df = pd.concat(buf)
            saved += store.save("duka", symbol, df)
            buf = []

    def work(day):
        return day, to_frame(day, parse(fetch_raw(code, day)), fmt)

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(work, x) for x in todo]
        for f in as_completed(futs):
            if stop_flag and stop_flag():
                for g in futs:
                    g.cancel()
                break
            try:
                day, df = f.result()
                if df is not None and len(df):
                    with lock:
                        buf.append(df)
            except Exception as e:
                failed.append(str(e))
            done += 1
            if len(buf) >= 40:
                flush()
            if progress:
                progress(done, total)
    flush()
    log.append(f"處理 {total} 天，寫入 {saved:,} 根 1 分 K" + (f"，失敗 {len(failed)} 天" if failed else ""))
    return {"days": total, "rows": saved, "failed": failed[:20], "log": log, "format": fmt}
