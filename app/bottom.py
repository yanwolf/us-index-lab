"""抄底訊號研究：收集日線資料 + 事件研究（訊號出現後 N 天報酬 vs 任意一天買進）。

資料（全部存在 DATA_DIR/bottom/，可重複按「更新」只補缺的部分）：
  · 指數／商品日 K：Yahoo Finance chart API（^GSPC、^NDX、GC=F、SI=F）
  · VIX：Cboe 官方 VIX_History.csv（1990～），失敗改 GitHub 鏡像 datasets/finance-vix
  · CNN 恐懼貪婪指數：GitHub whit3rabbit/fear-greed-data（2011～，每天更新），最近幾天用 CNN 官方 API 補
  · 賣權買權比（P/C）：Cboe 歷史檔（2006-11～2019-10）＋ Cboe 每日統計（2019-10 之後逐日抓）
  · S5FI（S&P 500 成分股站上 50 日均線比例）：自己算。成分股用 fja05680/sp500 的「歷史成分股起訖日」，
    每天只算當時在指數裡、而且抓得到股價的股票（下市股票 Yahoo 多半沒有 → 仍有一點存活者偏差，覆蓋率會顯示）
  · NDFI（那斯達克 100 成分股站上 50 日均線比例）：沒有免費的歷史成分股，只能用「現在的」成分股往回算
    → 越早期越偏樂觀（當年弱勢、後來被踢掉的股票不在裡面），結果僅供參考

事件研究：
  · 訊號在第 t 天收盤成立，用第 t+1 天收盤價進場（恐懼貪婪、P/C 都是收盤後才公布，保守起見晚一天）
  · 「一波」＝前 cooldown 個交易日都沒有訊號的第一天（避免同一次崩盤算成幾十次）
  · 對照組：同一段期間（訊號用到的資料都存在的日子）任意一天進場；
    「勝過隨機」＝隨機抽同樣次數的日子 2000 次，事件平均報酬贏過幾 % 的隨機組
"""
import io
import json
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from . import store

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "*/*"}
YAHOO_DELAY = float(os.getenv("BOTTOM_YAHOO_DELAY", "0.35"))
YAHOO_WORKERS = int(os.getenv("BOTTOM_YAHOO_WORKERS", "2"))

INDICES = {"SPX": "^GSPC", "NDX": "^NDX", "GOLD": "GC=F", "SILVER": "SI=F"}
NAMES = {"SPX": "S&P 500", "NDX": "那斯達克 100", "GOLD": "黃金期貨", "SILVER": "白銀期貨", "VIX": "VIX",
         "FG": "恐懼貪婪指數", "PC_TOTAL": "Cboe 總 P/C", "PC_EQUITY": "Cboe 股票 P/C", "S5FI": "S5FI", "NDFI": "NDFI"}
URLS = {
    "vix": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv",
    "vix_mirror": "https://raw.githubusercontent.com/datasets/finance-vix/main/data/vix-daily.csv",
    "fg": "https://raw.githubusercontent.com/whit3rabbit/fear-greed-data/main/fear-greed.csv",
    "fg_cnn": "https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{start}",
    "pc_hist_total": "https://cdn.cboe.com/resources/options/volume_and_call_put_ratios/totalpc.csv",
    "pc_hist_equity": "https://cdn.cboe.com/resources/options/volume_and_call_put_ratios/equitypc.csv",
    "pc_daily": "https://cdn.cboe.com/data/us/options/market_statistics/daily/{d}_daily_options",
    "sp_members": "https://raw.githubusercontent.com/fja05680/sp500/master/sp500_ticker_start_end.csv",
    "ndx_wiki": "https://en.wikipedia.org/wiki/Nasdaq-100",
    "yahoo": "https://{host}/v8/finance/chart/{sym}?period1={p1}&period2={p2}&interval=1d&includeAdjustedClose=true&events=div,split",
}
PC_DAILY_START = date(2019, 10, 7)       # Cboe 歷史檔到 2019-10-04 為止
_lock = threading.Lock()


# ------------------------------------------------------------------ 儲存
def _root(*p):
    return os.path.join(store.DATA_DIR, "bottom", *p)


def _save_series(name, df):
    os.makedirs(_root("series"), exist_ok=True)
    df = df.dropna(subset=["date"]).drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    df.to_parquet(_root("series", f"{name}.parquet"), index=False)
    return df


def load_series(name):
    p = _root("series", f"{name}.parquet")
    if not os.path.exists(p):
        return None
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"])
    return df


def _stock_path(t):
    return _root("stocks", f"{t.replace('/', '_')}.parquet")


def load_stock(t):
    p = _stock_path(t)
    if not os.path.exists(p):
        return None
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"])
    return df


def _meta_path():
    return _root("meta.json")


def _meta():
    try:
        with open(_meta_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_meta(m):
    os.makedirs(_root(), exist_ok=True)
    with open(_meta_path(), "w", encoding="utf-8") as fh:
        json.dump(m, fh, ensure_ascii=False, indent=1)


# ------------------------------------------------------------------ 抓資料
def http_get(url, headers=None, timeout=30, retries=3):
    err = None
    for k in range(retries):
        try:
            req = urllib.request.Request(url, headers={**UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise FileNotFoundError(url)
            err = f"HTTP {e.code} {e.reason}"
            time.sleep((6 if e.code in (429, 503) else 1.5) * (k + 1))
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            time.sleep(1.5 * (k + 1))
    raise RuntimeError(f"{url} 抓取失敗：{err}")


def yahoo_daily(sym, start=None, get=http_get):
    """Yahoo 日 K。回傳 DataFrame(date, open, high, low, close, adj)；沒有這檔回 None。"""
    p1 = int(datetime(1985, 1, 1, tzinfo=timezone.utc).timestamp()) if start is None else \
        int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    p2 = int(time.time()) + 86400
    last = None
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            raw = get(URLS["yahoo"].format(host=host, sym=urllib.request.quote(sym), p1=p1, p2=p2))
            j = json.loads(raw)
            res = (j.get("chart") or {}).get("result")
            if not res:
                e = (j.get("chart") or {}).get("error") or {}
                if "No data" in str(e.get("description", "")) or e.get("code") == "Not Found":
                    return None
                last = str(e) or "空結果"
                continue
            r = res[0]
            ts = r.get("timestamp") or []
            if not ts:
                return None
            q = r["indicators"]["quote"][0]
            adj = (r["indicators"].get("adjclose") or [{}])[0].get("adjclose") or q["close"]
            gmt = (r.get("meta") or {}).get("gmtoffset", 0) or 0
            df = pd.DataFrame({"date": pd.to_datetime([t + gmt for t in ts], unit="s").normalize(),
                               "open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"], "adj": adj})
            return df.dropna(subset=["close"]).drop_duplicates("date", keep="last")
        except FileNotFoundError:
            return None
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
    raise RuntimeError(f"Yahoo {sym} 失敗：{last}")


def _merge_save_stock(t, new):
    old = load_stock(t)
    df = new if old is None else pd.concat([old, new])
    df = df.drop_duplicates("date", keep="last").sort_values("date")
    os.makedirs(_root("stocks"), exist_ok=True)
    df[["date", "close", "adj"]].to_parquet(_stock_path(t), index=False)
    return df


def update_stock(t, get=http_get, ysym=None):
    """增量更新一檔股票。回傳 'ok' / 'nodata' / 'fresh'。"""
    old = load_stock(t)
    start = None
    if old is not None and len(old):
        last = old["date"].max().date()
        if (date.today() - last).days <= 1:
            return "fresh"
        start = last - timedelta(days=10)
    df = yahoo_daily(ysym or t.replace(".", "-"), start, get)
    if df is None or not len(df):
        if old is None:
            os.makedirs(_root("stocks"), exist_ok=True)
            pd.DataFrame({"date": pd.to_datetime([]), "close": [], "adj": []}).to_parquet(_stock_path(t), index=False)
        return "nodata"
    _merge_save_stock(t, df)
    return "ok"


def fetch_vix(get=http_get):
    for key in ("vix", "vix_mirror"):
        try:
            df = pd.read_csv(io.BytesIO(get(URLS[key])))
            df.columns = [c.strip().upper() for c in df.columns]
            out = pd.DataFrame({"date": pd.to_datetime(df["DATE"], format="mixed"), "close": pd.to_numeric(df["CLOSE"], errors="coerce"),
                                "high": pd.to_numeric(df.get("HIGH", df["CLOSE"]), errors="coerce")})
            return _save_series("VIX", out.dropna()), key
        except Exception as e:
            err = e
    raise RuntimeError(f"VIX 兩個來源都失敗：{err}")


def fetch_fg(get=http_get):
    df = pd.read_csv(io.BytesIO(get(URLS["fg"])))
    df.columns = [c.strip().lower() for c in df.columns]
    vcol = next(c for c in df.columns if "fear" in c or "value" in c)
    out = pd.DataFrame({"date": pd.to_datetime(df["date"]), "value": pd.to_numeric(df[vcol], errors="coerce")}).dropna()
    note = "GitHub"
    try:   # 最近 30 天用 CNN 官方補
        start = (date.today() - timedelta(days=30)).isoformat()
        j = json.loads(get(URLS["fg_cnn"].format(start=start),
                           headers={"Origin": "https://www.cnn.com", "Referer": "https://www.cnn.com/"}, retries=2))
        pts = (j.get("fear_and_greed_historical") or {}).get("data") or []
        if pts:
            cnn = pd.DataFrame({"date": pd.to_datetime([p["x"] for p in pts], unit="ms").normalize(), "value": [p["y"] for p in pts]})
            out = pd.concat([out, cnn])
            note += "＋CNN"
    except Exception:
        pass
    return _save_series("FG", out), note


def _parse_cboe_hist(raw):
    """Cboe 歷史 P/C 檔：前面有幾行說明，找到含 P/C 的表頭那行開始讀。"""
    lines = raw.decode("utf-8", "replace").splitlines()
    hi = next(i for i, l in enumerate(lines) if "P/C" in l.upper() and "DATE" in l.upper())
    df = pd.read_csv(io.StringIO("\n".join(lines[hi:])))
    df.columns = [c.strip().upper() for c in df.columns]
    pcol = next(c for c in df.columns if "P/C" in c)
    return pd.DataFrame({"date": pd.to_datetime(df["DATE"], format="mixed", errors="coerce"),
                         "value": pd.to_numeric(df[pcol], errors="coerce")}).dropna()


def _find_ratios(obj, out):
    """Cboe 每日統計 JSON 結構不寫死：遞迴找 name 含 PUT/CALL RATIO 的項目。"""
    if isinstance(obj, dict):
        name = str(obj.get("name", "")).upper()
        if "PUT/CALL RATIO" in name and "value" in obj:
            try:
                out[name] = float(obj["value"])
            except (TypeError, ValueError):
                pass
        for v in obj.values():
            _find_ratios(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _find_ratios(v, out)
    return out


def fetch_pc(progress=None, stop=lambda: False, get=http_get, log=None):
    """P/C：歷史檔一次抓；2019-10 之後逐日抓（已抓過的日子跳過，最近 5 天重抓）。"""
    m = _meta()
    tot, eq = load_series("PC_TOTAL"), load_series("PC_EQUITY")
    if tot is None or not m.get("pc_hist_done"):
        parts_t, parts_e = [], []
        for key, parts in (("pc_hist_total", parts_t), ("pc_hist_equity", parts_e)):
            try:
                parts.append(_parse_cboe_hist(get(URLS[key])))
            except Exception as e:
                (log or []).append(f"P/C 歷史檔 {key} 失敗：{e}")
        tot = pd.concat(([tot] if tot is not None else []) + parts_t) if (parts_t or tot is not None) else None
        eq = pd.concat(([eq] if eq is not None else []) + parts_e) if (parts_e or eq is not None) else None
        if parts_t and parts_e:
            m["pc_hist_done"] = True
    tried = set(m.get("pc_tried", []))
    today = date.today()
    days = []
    d = PC_DAILY_START
    while d < today:
        if d.weekday() < 5 and (d.isoformat() not in tried or (today - d).days <= 5):
            days.append(d)
        d += timedelta(days=1)
    rows_t, rows_e, fails = [], [], 0
    for k, d in enumerate(days):
        if stop():
            break
        try:
            j = json.loads(get(URLS["pc_daily"].format(d=d.isoformat()), retries=2))
            r = _find_ratios(j, {})
            if "TOTAL PUT/CALL RATIO" in r:
                rows_t.append((d, r["TOTAL PUT/CALL RATIO"]))
            if "EQUITY PUT/CALL RATIO" in r:
                rows_e.append((d, r["EQUITY PUT/CALL RATIO"]))
            tried.add(d.isoformat()); fails = 0
        except FileNotFoundError:          # 休市日
            tried.add(d.isoformat()); fails = 0
        except Exception as e:
            fails += 1
            if fails >= 15:
                (log or []).append(f"P/C 每日統計連續失敗，先停在 {d}：{e}")
                break
        if progress:
            progress(k + 1, len(days))
        time.sleep(0.15)
    if rows_t:
        tot = pd.concat([tot, pd.DataFrame(rows_t, columns=["date", "value"])]) if tot is not None else pd.DataFrame(rows_t, columns=["date", "value"])
    if rows_e:
        eq = pd.concat([eq, pd.DataFrame(rows_e, columns=["date", "value"])]) if eq is not None else pd.DataFrame(rows_e, columns=["date", "value"])
    for name, df in (("PC_TOTAL", tot), ("PC_EQUITY", eq)):
        if df is not None and len(df):
            df["date"] = pd.to_datetime(df["date"])
            _save_series(name, df)
    m["pc_tried"] = sorted(tried)
    _save_meta(m)
    return len(rows_t)


def sp_members(get=http_get):
    df = pd.read_csv(io.BytesIO(get(URLS["sp_members"])))
    df["start_date"] = pd.to_datetime(df["start_date"])
    df["end_date"] = pd.to_datetime(df["end_date"])
    os.makedirs(_root(), exist_ok=True)
    df.to_csv(_root("sp_members.csv"), index=False)
    return df


def ndx_members(get=http_get):
    html = get(URLS["ndx_wiki"]).decode("utf-8", "replace")
    tickers = []
    for tbl in pd.read_html(io.StringIO(html)):
        cols = [str(c).lower() for c in tbl.columns]
        col = next((tbl.columns[i] for i, c in enumerate(cols) if c in ("ticker", "symbol")), None)
        if col is not None and 80 <= len(tbl) <= 110:
            tickers = [str(t).strip() for t in tbl[col] if isinstance(t, str)]
            break
    if not tickers:
        raise RuntimeError("維基百科的那斯達克 100 成分股表格找不到（頁面格式可能改了）")
    with open(_root("ndx_members.json"), "w") as fh:
        json.dump({"tickers": tickers, "as_of": date.today().isoformat()}, fh)
    return tickers


def collect(progress, stop, with_breadth=True, get=http_get):
    """整套收集；每一步失敗都記下來繼續做下一步。"""
    log, step_total = [], 6 if with_breadth else 4
    m = _meta()

    def step(i):
        progress(i, step_total)

    step(0)
    for key, sym in INDICES.items():
        try:
            old = load_series(key)
            start = None if old is None or not len(old) else (old["date"].max() - timedelta(days=10)).date()
            df = yahoo_daily(sym, start, get)
            if df is not None:
                df = pd.concat([old, df]) if old is not None and start else df
                df = _save_series(key, df)
                log.append(f"{NAMES[key]}：{len(df):,} 天（{df['date'].min():%Y-%m-%d}～{df['date'].max():%Y-%m-%d}）")
        except Exception as e:
            log.append(f"{NAMES[key]} 失敗：{e}")
        time.sleep(YAHOO_DELAY)
    step(1)
    for name, fn in (("VIX", fetch_vix), ("恐懼貪婪", fetch_fg)):
        try:
            df, src = fn(get)
            log.append(f"{name}：{len(df):,} 天（{df['date'].min():%Y-%m-%d}～{df['date'].max():%Y-%m-%d}，來源 {src}）")
        except Exception as e:
            log.append(f"{name} 失敗：{e}")
    step(2)
    if stop():
        return {"log": log}
    try:
        n = fetch_pc(None, stop, get, log)
        t = load_series("PC_TOTAL")
        log.append(f"P/C：本次新增 {n} 天；總 P/C 共 {0 if t is None else len(t):,} 天"
                   + (f"（{t['date'].min():%Y-%m-%d}～{t['date'].max():%Y-%m-%d}）" if t is not None and len(t) else ""))
    except Exception as e:
        log.append(f"P/C 失敗：{e}")
    step(3)
    if with_breadth and not stop():
        tickers = {}
        try:
            mem = sp_members(get)
            mem = mem[(mem["end_date"].isna()) | (mem["end_date"] >= "2000-01-01")]
            for t in mem["ticker"]:
                tickers[t] = t
        except Exception as e:
            log.append(f"S&P 歷史成分股清單失敗：{e}")
        try:
            for t in ndx_members(get):
                tickers.setdefault(t, t)
        except Exception as e:
            log.append(f"那斯達克 100 成分股清單失敗：{e}")
        todo = sorted(tickers)
        res = {"ok": 0, "nodata": 0, "fresh": 0, "err": 0}
        errs = []
        done = [0]

        def one(t):
            if stop():
                return
            try:
                r = update_stock(t, get)
                res[r] += 1
            except Exception as e:
                res["err"] += 1
                if len(errs) < 5:
                    errs.append(f"{t}: {e}")
            time.sleep(YAHOO_DELAY)
            with _lock:
                done[0] += 1
                progress(3 + done[0] / max(1, len(todo)) * 2, step_total)

        with ThreadPoolExecutor(YAHOO_WORKERS) as ex:
            list(ex.map(one, todo))
        log.append(f"成分股 {len(todo)} 檔：更新 {res['ok']}、已是最新 {res['fresh']}、Yahoo 沒有資料（多半已下市）{res['nodata']}、失敗 {res['err']}")
        log += errs
        step(5)
        try:
            log += compute_breadth()
        except Exception as e:
            log.append(f"廣度指標計算失敗：{e}")
    step(step_total)
    m = _meta()
    m["collected_at"] = datetime.now(timezone.utc).isoformat()[:19]
    m["log"] = log
    _save_meta(m)
    return {"log": log}


def _closes(tickers):
    cols = {}
    for t in tickers:
        df = load_stock(t)
        if df is not None and len(df) > 60:
            cols[t] = df.set_index("date")["adj"].astype(float)
    return pd.DataFrame(cols).sort_index() if cols else pd.DataFrame()


def compute_breadth():
    log = []
    spx = load_series("SPX")
    if spx is None:
        return ["算廣度需要先有 S&P 500 指數資料"]
    days = pd.DatetimeIndex(spx["date"])
    p = _root("sp_members.csv")
    if os.path.exists(p):
        mem = pd.read_csv(p, parse_dates=["start_date", "end_date"])
        px = _closes(mem["ticker"].unique()).reindex(days)
        above = (px > px.rolling(50, min_periods=50).mean()).astype(float).where(px.rolling(50, min_periods=50).mean().notna())
        member = pd.DataFrame(False, index=days, columns=px.columns)
        for _, r in mem.iterrows():
            if r["ticker"] in member.columns:
                end = r["end_date"] if pd.notna(r["end_date"]) else days.max()
                member.loc[(days >= r["start_date"]) & (days <= end), r["ticker"]] = True
        valid = above.notna() & member
        cnt = valid.sum(axis=1)
        val = (above.where(valid).sum(axis=1) / cnt.replace(0, np.nan)) * 100
        n_members = member.sum(axis=1)
        df = pd.DataFrame({"date": days, "value": val.values, "n": cnt.values, "members": n_members.values})
        df = df[df["n"] >= 250]       # 有效股票太少的早期日子不算
        _save_series("S5FI", df)
        if len(df):
            cov = (df["n"] / df["members"]).iloc[[0, len(df) // 2, -1]]
            log.append(f"S5FI：{len(df):,} 天（{df['date'].min():%Y-%m-%d} 起）；成分股覆蓋率 起點 {cov.iloc[0]:.0%}、中段 {cov.iloc[1]:.0%}、最近 {cov.iloc[2]:.0%}")
    p = _root("ndx_members.json")
    ndx = load_series("NDX")
    if os.path.exists(p) and ndx is not None:
        with open(p) as fh:
            tk = json.load(fh)["tickers"]
        nd = pd.DatetimeIndex(ndx["date"])
        px = _closes(tk).reindex(nd)
        ma = px.rolling(50, min_periods=50).mean()
        above = (px > ma).astype(float).where(ma.notna())
        cnt = above.notna().sum(axis=1)
        df = pd.DataFrame({"date": nd, "value": (above.sum(axis=1) / cnt.replace(0, np.nan) * 100).values, "n": cnt.values})
        df = df[df["n"] >= 50]
        _save_series("NDFI", df)
        if len(df):
            log.append(f"NDFI：{len(df):,} 天（{df['date'].min():%Y-%m-%d} 起，用現在的成分股往回算，早期偏樂觀）")
    return log


# ------------------------------------------------------------------ 指標
def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def _on(df, idx, col="value"):
    """把另一條日線對齊到 idx（只用當天或之前最近一筆，最多往前 3 天）。"""
    if df is None or not len(df):
        return pd.Series(np.nan, index=idx)
    s = df.set_index("date")[col].astype(float).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    return s.reindex(idx, method="ffill", limit=3)


HORIZONS = [5, 20, 60, 120, 250]


def signal_defs():
    """(id, 標題, 標的, 條件函式(panel) -> bool Series, 需要的欄位, 說明)"""
    return [
        ("vix60", "VIX > 60", "SPX", lambda p: p["vix"] > 60, ["vix"], "收盤 VIX"),
        ("vix40", "VIX > 40", "SPX", lambda p: p["vix"] > 40, ["vix"], "收盤 VIX"),
        ("s5fi10", "S5FI < 10", "SPX", lambda p: p["s5fi"] < 10, ["s5fi"], "自己算，見資料說明"),
        ("ndfi10", "NDFI < 10", "NDX", lambda p: p["ndfi"] < 10, ["ndfi"], "現在成分股往回算，早期偏樂觀"),
        ("fg10", "恐懼貪婪 < 10", "SPX", lambda p: p["fg"] < 10, ["fg"], "2011 起"),
        ("spx_rsi", "S&P 日 RSI(14) < 30", "SPX", lambda p: p["rsi"] < 30, ["rsi"], ""),
        ("pct09", "總 P/C 5 日均 > 0.9", "SPX", lambda p: p["pct5"] > 0.9, ["pct5"], "Cboe 總 P/C 平常就在 0.9 附近"),
        ("pce09", "股票 P/C 5 日均 > 0.9", "SPX", lambda p: p["pce5"] > 0.9, ["pce5"], "股票 P/C 平常約 0.6"),
        ("four2", "四大訊號 ≥ 2 個", "SPX", lambda p: p["n4"] >= 2, ["fg", "vix", "rsi", "pct5"], "恐懼貪婪<10、VIX>40、RSI<30、總P/C 5日均>0.9"),
        ("four3", "四大訊號 ≥ 3 個", "SPX", lambda p: p["n4"] >= 3, ["fg", "vix", "rsi", "pct5"], ""),
        ("four4", "四大訊號全中", "SPX", lambda p: p["n4"] >= 4, ["fg", "vix", "rsi", "pct5"], ""),
        ("gold_rsi", "黃金日 RSI(14) < 30", "GOLD", lambda p: p["rsi"] < 30, ["rsi"], "期貨連續月"),
        ("silver_rsi", "白銀日 RSI(14) < 30", "SILVER", lambda p: p["rsi"] < 30, ["rsi"], "期貨連續月"),
    ]


def panel(target):
    px = load_series(target)
    if px is None or not len(px):
        return None
    px = px.drop_duplicates("date").set_index("date").sort_index()
    idx = px.index
    p = pd.DataFrame(index=idx)
    p["close"] = px["close"].astype(float)
    p["rsi"] = rsi(p["close"])
    if target in ("SPX", "NDX"):
        p["vix"] = _on(load_series("VIX"), idx, "close")
        p["fg"] = _on(load_series("FG"), idx)
        p["pct5"] = _on(load_series("PC_TOTAL"), idx).rolling(5, min_periods=5).mean()
        p["pce5"] = _on(load_series("PC_EQUITY"), idx).rolling(5, min_periods=5).mean()
        p["s5fi"] = _on(load_series("S5FI"), idx)
        p["ndfi"] = _on(load_series("NDFI"), idx)
        p["n4"] = (p["fg"] < 10).astype(int) + (p["vix"] > 40).astype(int) + (p["rsi"] < 30).astype(int) + (p["pct5"] > 0.9).astype(int)
    return p


def event_study(p, cond, need, cooldown=20, seed=7):
    ok = p[need].notna().all(axis=1) & p["close"].notna()
    cond = cond.fillna(False) & ok
    c = p["close"].to_numpy()
    n = len(c)
    pos = np.flatnonzero(cond.to_numpy())
    # 一波的起點：前 cooldown 天都沒有訊號
    starts = [i for k, i in enumerate(pos) if k == 0 or i - pos[k - 1] > cooldown]
    span = np.flatnonzero(ok.to_numpy())
    if not len(span):
        return None
    lo_i, hi_i = span[0], span[-1]

    def fwd(i, h):
        e = i + 1                      # 隔天收盤進場
        return c[e + h] / c[e] - 1 if e + h < n else np.nan

    out = {"signal_days": int(len(pos)), "episodes": len(starts),
           "span": [str(p.index[lo_i].date()), str(p.index[hi_i].date())], "h": {}, "list": []}
    rng = np.random.default_rng(seed)
    for h in HORIZONS:
        ev = np.array([fwd(i, h) for i in starts])
        ev = ev[~np.isnan(ev)]
        base_idx = np.arange(lo_i, min(hi_i, n - h - 2) + 1)
        base = c[base_idx + 1 + h] / c[base_idx + 1] - 1 if len(base_idx) else np.array([])
        row = {"n": int(len(ev))}
        if len(base):
            row.update(base_mean=float(base.mean() * 100), base_win=float((base > 0).mean() * 100))
        if len(ev):
            row.update(mean=float(ev.mean() * 100), median=float(np.median(ev) * 100), win=float((ev > 0).mean() * 100),
                       worst=float(ev.min() * 100))
            if len(base) and len(ev) >= 2:
                sims = rng.choice(base, size=(2000, len(ev))).mean(axis=1)
                row["beat"] = float((ev.mean() > sims).mean() * 100)
        out["h"][h] = row
    nxt = {s: (starts[k + 1] if k + 1 < len(starts) else n) for k, s in enumerate(starts)}
    for i in starts:
        e = i + 1
        if e >= n:
            out["list"].append({"date": str(p.index[i].date()), "pending": True})
            continue
        seg = c[e:min(n, e + 61)]
        seg250 = c[e:min(n, e + 251)]
        out["list"].append({
            "date": str(p.index[i].date()), "entry": float(c[e]),
            "ret": {h: (None if np.isnan(fwd(i, h)) else round(float(fwd(i, h)) * 100, 2)) for h in HORIZONS},
            "mae60": round(float(seg.min() / c[e] - 1) * 100, 2),
            "low_after": int(np.argmin(seg250)), "days_in_signal": int(((pos >= i) & (pos < nxt[i])).sum()),
        })
    return out


def study(cooldown=20):
    panels, res = {}, []
    for sid, title, target, fn, need, note in signal_defs():
        if target not in panels:
            panels[target] = panel(target)
        p = panels[target]
        if p is None:
            res.append({"id": sid, "title": title, "target": NAMES[target], "error": f"沒有 {NAMES[target]} 的資料"}); continue
        miss = [k for k in need if k not in p.columns or p[k].notna().sum() == 0]
        if miss:
            res.append({"id": sid, "title": title, "target": NAMES[target], "error": f"缺資料：{'、'.join(miss)}"}); continue
        r = event_study(p, fn(p), need, cooldown)
        if r is None:
            res.append({"id": sid, "title": title, "target": NAMES[target], "error": "資料重疊的期間是空的"}); continue
        r.update(id=sid, title=title, target=NAMES[target], note=note)
        res.append(r)
    return {"cooldown": cooldown, "horizons": HORIZONS, "results": res, "now": current_status(panels)}


def current_status(panels=None):
    panels = panels or {t: panel(t) for t in ("SPX", "NDX", "GOLD", "SILVER")}
    out = []

    def last(t, col):
        p = panels.get(t)
        if p is None or col not in p or p[col].dropna().empty:
            return None, None
        s = p[col].dropna()
        return float(s.iloc[-1]), str(s.index[-1].date())

    for label, t, col, thr, op in (("VIX", "SPX", "vix", 40, ">"), ("S5FI", "SPX", "s5fi", 10, "<"), ("NDFI", "NDX", "ndfi", 10, "<"),
                                   ("恐懼貪婪", "SPX", "fg", 10, "<"), ("S&P RSI(14)", "SPX", "rsi", 30, "<"),
                                   ("總 P/C 5 日均", "SPX", "pct5", 0.9, ">"), ("股票 P/C 5 日均", "SPX", "pce5", 0.9, ">"),
                                   ("黃金 RSI(14)", "GOLD", "rsi", 30, "<"), ("白銀 RSI(14)", "SILVER", "rsi", 30, "<")):
        v, d = last(t, col)
        out.append({"name": label, "value": v, "date": d, "threshold": f"{op} {thr}",
                    "hit": None if v is None else (v > thr if op == ">" else v < thr)})
    return out


def inventory():
    out = []
    for k in ("SPX", "NDX", "GOLD", "SILVER", "VIX", "FG", "PC_TOTAL", "PC_EQUITY", "S5FI", "NDFI"):
        df = load_series(k)
        out.append({"key": k, "name": NAMES[k], "rows": 0 if df is None else int(len(df)),
                    "first": None if df is None or not len(df) else str(df["date"].min().date()),
                    "last": None if df is None or not len(df) else str(df["date"].max().date())})
    d = _root("stocks")
    n = len(os.listdir(d)) if os.path.isdir(d) else 0
    m = _meta()
    return {"series": out, "stocks": n, "collected_at": m.get("collected_at"), "log": m.get("log", [])}
