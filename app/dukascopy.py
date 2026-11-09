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
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "*/*",
    "Referer": "https://www.dukascopy.com/",
}
REC = struct.Struct(">5if")
WORKERS = int(os.getenv("DUKA_WORKERS", "2"))
DELAY = float(os.getenv("DUKA_DELAY", "0.5"))          # 每個請求之間的間隔（秒），降低被限流的機率
MAX_CONSEC_FAIL = int(os.getenv("DUKA_MAX_FAIL", "12")) # 連續失敗這麼多天就停（多半是被限流，硬跑只會更久）
DIVISORS = [1000, 100, 10, 1, 10000, 100000]


def url_for(code, d):
    return f"{BASE}/{code}/{d.year}/{d.month - 1:02d}/{d.day:02d}/BID_candles_min_1.bi5"


def fetch_raw(code, d, retries=4):
    """回傳解壓後的 bytes；沒有資料（404 或空檔）回 b""。"""
    url = url_for(code, d)
    for k in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
            if not data:
                return b""
            return lzma.decompress(data, format=lzma.FORMAT_ALONE)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return b""
            err = f"HTTP {e.code} {e.reason}"
            if e.code in (429, 503):
                time.sleep(5 * (k + 1))
        except urllib.error.URLError as e:
            err = f"連線失敗：{e.reason}"
        except lzma.LZMAError as e:
            err = f"解壓失敗（伺服器回傳的不是資料檔）：{e}"
        except Exception as e:      # 逾時等
            err = f"{type(e).__name__}: {e}"
        time.sleep(1.5 * (k + 1))
    raise RuntimeError(f"{d} 下載失敗：{err}")


def diagnose(symbol):
    """連線診斷：一般外網、Dukascopy 首頁、實際資料檔，各回報狀態。"""
    code = INSTRUMENTS[symbol]["duka"]
    d = datetime.now(timezone.utc).date() - timedelta(days=7)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    tests = [("一般外網", "https://pypi.org/simple/"),
             ("Dukascopy 首頁", "https://www.dukascopy.com/"),
             ("Dukascopy 資料檔", url_for(code, d))]
    out = []
    for name, url in tests:
        r = {"name": name, "url": url}
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read()
            r.update(status=resp.status, bytes=len(body), ctype=resp.headers.get("Content-Type"))
            if name == "Dukascopy 資料檔":
                try:
                    rows = parse(lzma.decompress(body, format=lzma.FORMAT_ALONE)) if body else []
                    r["rows"] = len(rows)
                    if len(rows):
                        r["sample_raw"] = rows[len(rows) // 2, 1:5].tolist()
                except Exception as e:
                    r["decode_error"] = str(e)
                    r["body_head"] = body[:200].decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            r.update(status=e.code, error=f"HTTP {e.code} {e.reason}")
            try:
                r["body_head"] = e.read()[:200].decode("utf-8", "replace")
            except Exception:
                pass
        except Exception as e:
            r.update(status=None, error=f"{type(e).__name__}: {getattr(e, 'reason', e)}")
        r["ms"] = int((time.time() - t0) * 1000)
        out.append(r)
    return out


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
        if stop_flag and stop_flag() or tripped[0]:
            return day, None
        time.sleep(DELAY)
        return day, to_frame(day, parse(fetch_raw(code, day, retries=3)), fmt)

    tripped = [False]
    consec = 0

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(work, x) for x in todo]
        for f in as_completed(futs):
            if stop_flag and stop_flag():
                for g in futs:
                    g.cancel()
                break
            if tripped[0]:
                for g in futs:
                    g.cancel()
                break
            try:
                day, df = f.result()
                consec = 0
                if df is not None and len(df):
                    with lock:
                        buf.append(df)
            except Exception as e:
                failed.append(str(e))
                consec += 1
                if consec >= MAX_CONSEC_FAIL:
                    tripped[0] = True
            done += 1
            if len(buf) >= 40:
                flush()
            if progress:
                progress(done, total)
    flush()
    log.append(f"處理 {done}／{total} 天，寫入 {saved:,} 根 1 分 K" + (f"，失敗 {len(failed)} 天" if failed else ""))
    if tripped[0]:
        log.append(f"連續 {MAX_CONSEC_FAIL} 天下載失敗，已自動停止：多半是 Dukascopy 暫時限流了你的伺服器 IP，"
                   "建議隔幾個小時再按一次（已抓到的會保留，會從缺的地方接著抓）")
    return {"days": total, "rows": saved, "failed": failed[:20], "log": log, "format": fmt}
