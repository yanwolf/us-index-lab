"""從 1 分 K 組出 N 分 K、交易日、交易時段、週，並快取指標陣列。

時間規則見 instruments.py（每個商品用自己交易所的當地時間）。K 棒切法（align）：
  session（預設，MultiCharts 慣例）：每個時段開盤重新起算，主時段開盤是新 K 的起點
  clock：從交易日切換點起整點切
K 棒標籤用「結束時間」（MC 慣例）。

closeD / closeS / highW 的對照：
  交易日   day_roll 切換（例如美股 18:00、週日晚上那段算週一），CloseD(1) = 上一個完整交易日收盤
  時段     主時段 ↔ 延長時段交替（類比台指日盤／夜盤），CloseS(1) = 上一個完整時段收盤
  週       以交易日的 ISO 週分組，HighW(0) = 本週到目前為止的最高
"""
import numpy as np
import pandas as pd

from .instruments import hm2min, inst as get_inst

_bars_cache = {}


class Bars:
    pass


WEEK_GAP = 40 * 3600       # 兩分鐘之間超過 40 小時視為週末（或長假）休市


def minute_frame(m, symbol=None):
    """1 分 K 陣列 → 加上當地時間欄位（快取在 dict 裡，同一份資料只算一次）。"""
    if "_et" in m:
        return m
    I = get_inst(symbol)
    roll = hm2min(I["day_roll"])
    r0, r1 = hm2min(I["rth"][0]), hm2min(I["rth"][1])
    e0 = hm2min(I.get("eth_start", I["rth"][1]))
    et = pd.to_datetime(m["ts"], unit="s", utc=True).tz_convert(I["tz"])
    hhmm = (et.hour * 100 + et.minute).to_numpy()
    emin = (et.hour * 60 + et.minute).to_numpy()
    edate = et.normalize().tz_localize(None)
    # 交易日：day_roll 之後歸隔天
    tday = (et + pd.Timedelta(minutes=(1440 - roll) % 1440)).normalize().tz_localize(None)
    tday_ord = (tday.to_numpy().astype("datetime64[D]").astype("int64"))
    # 落在週六、週日的交易日順延到週一（週五夜盤歸下週一，期交所／大阪交易所慣例）
    wd0 = (tday_ord + 3) % 7
    tday_ord = tday_ord + np.where(wd0 == 5, 2, np.where(wd0 == 6, 1, 0))
    edate_ord = edate.to_numpy().astype("datetime64[D]").astype("int64")
    is_rth = (emin >= r0) & (emin < r1)
    ses_date = np.where(emin >= r0, edate_ord, edate_ord - 1)
    ses_key = ses_date * 2 + np.where(is_rth, 0, 1)
    # 時段開始的「分鐘時鐘」：主時段從 r0、延長時段從 e0（夜盤開盤）起算（跨午夜照實際時鐘差）
    mins_since = np.where(is_rth, emin - r0, (emin - e0) % 1440)
    # 每週最後一分鐘（下一分鐘在 40 小時以後，或資料結尾）
    ts = m["ts"]
    gap_next = np.concatenate([np.diff(ts), [WEEK_GAP + 1]])
    week_last = gap_next > WEEK_GAP
    # 每一分鐘距離「本週最後一分鐘」還有幾秒
    idx_last = np.flatnonzero(week_last)
    nxt = idx_last[np.searchsorted(idx_last, np.arange(len(ts)))]
    m["_et"] = True
    m["hhmm"] = hhmm
    m["tday"] = tday_ord
    m["tday_wd"] = ((tday_ord + 3) % 7)          # 1970-01-01 是週四 → 0=週一 … 4=週五
    m["ses_key"] = ses_key
    m["ses_mins"] = mins_since
    m["day_mins"] = (emin - roll) % 1440          # 交易日內第幾分鐘（切換點 = 0）
    m["to_week_end"] = ts[nxt] - ts               # 秒
    m["tz"] = I["tz"]
    return m


def build(m, minutes, align="session", cache_key=None, symbol=None):
    if cache_key is not None and (cache_key, minutes, align) in _bars_cache:
        return _bars_cache[(cache_key, minutes, align)]
    m = minute_frame(m, symbol)
    n = len(m["ts"])
    if align == "clock":
        slot = m["day_mins"] // minutes
        key = m["tday"] * 10000 + slot
    else:
        slot = m["ses_mins"] // minutes
        key = (m["tday"] % 1000) + 1000 * (slot + 2000 * m["ses_key"])   # 交易日不同也切開（避免跨 18:00）
    brk = np.flatnonzero(np.diff(key)) + 1
    m0 = np.concatenate([[0], brk])
    m1 = np.concatenate([brk, [n]])
    b = Bars()
    b.minutes = minutes
    b.m0, b.m1 = m0, m1
    b.o = m["o"][m0]
    b.h = np.maximum.reduceat(m["h"], m0)
    b.l = np.minimum.reduceat(m["l"], m0)
    b.c = m["c"][m1 - 1]
    b.ts_start = m["ts"][m0]
    # 結束時間標籤：時槽起點 + minutes，但不超過下一根 K 的起點（時段尾端的不完整 K）
    off = (m["day_mins"] if align == "clock" else m["ses_mins"])[m0] % minutes
    slot_end = m["ts"][m0] - (m["ts"][m0] % 60) - off * 60 + minutes * 60
    nxt = np.concatenate([m["ts"][m0][1:], [np.iinfo(np.int64).max]])
    b.ts_end = np.minimum(slot_end, nxt)
    b.tday = m["tday"][m0]
    b.tday_wd = m["tday_wd"][m0]
    b.ses_key = m["ses_key"][m0]
    nb = len(m0)
    b.n = nb

    # 交易日序列
    d_brk = np.flatnonzero(np.diff(b.tday)) + 1
    d0 = np.concatenate([[0], d_brk])
    d1 = np.concatenate([d_brk, [nb]])
    b.day_close = b.c[d1 - 1]
    b.day_idx = np.repeat(np.arange(len(d0)), d1 - d0)
    b.day_tday = b.tday[d0]
    # 時段序列
    s_brk = np.flatnonzero(np.diff(b.ses_key)) + 1
    s0 = np.concatenate([[0], s_brk])
    s1 = np.concatenate([s_brk, [nb]])
    b.ses_close = b.c[s1 - 1]
    b.ses_idx = np.repeat(np.arange(len(s0)), s1 - s0)
    # 週序列（以交易日的 ISO 週：週一為起點；ordinal+3 讓週一 = 0）
    wk = (b.tday + 3) // 7
    w_brk = np.flatnonzero(np.diff(wk)) + 1
    w0 = np.concatenate([[0], w_brk])
    w1 = np.concatenate([w_brk, [nb]])
    b.week_high = np.maximum.reduceat(b.h, w0)
    b.week_low = np.minimum.reduceat(b.l, w0)
    b.week_idx = np.repeat(np.arange(len(w0)), w1 - w0)
    s = pd.Series(b.h)
    b.wtd_high = s.groupby(b.week_idx).cummax().to_numpy()
    b.wtd_low = pd.Series(b.l).groupby(b.week_idx).cummin().to_numpy()
    b.cache = {}
    if cache_key is not None:
        _bars_cache[(cache_key, minutes, align)] = b
        if len(_bars_cache) > 12:
            _bars_cache.pop(next(iter(_bars_cache)))
    return b


def clear_cache():
    _bars_cache.clear()


# ---------------------------------------------------------------- 指標（全部預先算成陣列）
def series(b, key):
    return {"O": b.o, "H": b.h, "L": b.l, "C": b.c}[key]


def sma(b, key, n):
    n = max(1, int(n))
    k = ("sma", key, n)
    if k not in b.cache:
        b.cache[k] = pd.Series(series(b, key)).rolling(n, min_periods=1).mean().to_numpy()
    return b.cache[k]


def avgrange(b, n):
    n = max(1, int(n))
    k = ("ar", n)
    if k not in b.cache:
        b.cache[k] = pd.Series(b.h - b.l).rolling(n, min_periods=1).mean().to_numpy()
    return b.cache[k]


def highest(b, key, n):
    n = max(1, int(n))
    k = ("hh", key, n)
    if k not in b.cache:
        b.cache[k] = pd.Series(series(b, key)).rolling(n, min_periods=1).max().to_numpy()
    return b.cache[k]


def lowest(b, key, n):
    n = max(1, int(n))
    k = ("ll", key, n)
    if k not in b.cache:
        b.cache[k] = pd.Series(series(b, key)).rolling(n, min_periods=1).min().to_numpy()
    return b.cache[k]


def _swing(b, strength, length, high=True):
    """EL SwingHigh(1, H, strength, length)：在第 i 根時，最近一個「左邊 strength 根嚴格較低、
    右邊 strength 根不高於它」的高點；回看超過 length 根找不到回 -1。"""
    s = int(strength)
    length = int(length)
    k = ("swh" if high else "swl", s, length)
    if k in b.cache:
        return b.cache[k]
    x = b.h if high else b.l
    n = len(x)
    is_sw = np.zeros(n, dtype=bool)
    if n > 2 * s:
        ok = np.ones(n - 2 * s, dtype=bool)
        mid = x[s:n - s]
        for j in range(1, s + 1):
            left = x[s - j:n - s - j]
            right = x[s + j:n - s + j]
            if high:
                ok &= (left < mid) & (right <= mid)
            else:
                ok &= (left > mid) & (right >= mid)
        is_sw[s:n - s] = ok
    out = np.full(n, -1.0)
    last = -1
    for i in range(n):
        t = i - s                       # 第 i 根收盤時，最新可確認的轉折位置
        if t >= 0 and is_sw[t]:
            last = t
        if last >= 0 and last >= i - length + s + 1:
            out[i] = x[last]
    b.cache[k] = out
    return out


def swinghigh(b, strength, length):
    return _swing(b, strength, length, True)


def swinglow(b, strength, length):
    return _swing(b, strength, length, False)


def daily_sma(b, n):
    """日收盤（交易日，美東 18:00 切換）的 n 日均線，每個交易日一個值；不足 n 天為 NaN。"""
    n = max(1, int(n))
    k = ("dsma", n)
    if k not in b.cache:
        b.cache[k] = pd.Series(b.day_close).rolling(n, min_periods=n).mean().to_numpy()
    return b.cache[k]
