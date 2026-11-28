"""古典型態策略（2026-11-28 新增）：把常見的「交易型態對照表」寫成規則來回測。

做法（全部只用當下已知的資料，沒有偷看）：
  1. 轉折點：ZigZag。價格從目前極值反向走超過 ZZ × AvgRange(200) 才確認上一個高／低點。
     確認一定比真正的轉折晚——這就是實盤會遇到的延遲，回測也照算。
  2. 型態：每確認一個新轉折，就用最近 3～5 個轉折點比對型態，產生「候選」：
     突破哪條線、停損放哪、量度目標多高。候選只在下一個轉折確認前有效（另外最後轉折點之後 EXP 根內沒突破就作廢）。
  3. 進場：收盤突破頸線／趨勢線（± BUF × AvgRange(200)）→ 下一根開盤市價進場（= 等收盤確認）。
  4. 出場：停損 = 最後一個轉折點外側 SLB × AvgRange(200)；停利 = 量度目標（型態高度 × TPX）；
     MAXB > 0 時持有超過幾根就出場。進場那根 K 的保護用引擎的 EBX。

PAT 型態代碼（0 = 全部，同時成立時依這個順序優先）：
  3 頭肩頂／頭肩底：H1 L1 H2 L2 H3，頭比兩肩高出 TOL 以上、兩肩差距 ≤ 2×TOL，頸線 = L1–L2 連線（可斜）
  2 三重頂／三重底：三個高點差距 ≤ TOL、兩個低點差距 ≤ TOL，頸線 = 兩個低點中較低的
  1 雙頂／雙底：H1 L1 H2，兩個高點差距 ≤ TOL，頸線 = L1
  4 上升／下降三角形：水平邊兩點差距 ≤ TOL、另一邊墊高（或壓低）≥ TOL，突破水平邊
  5 對稱三角形：高點降低、低點墊高（各 ≥ TOL），往目前這一段的方向突破趨勢線
  6 楔形：上升楔形（兩線都往上、下緣較陡 → 收斂）跌破下緣做空；下降楔形鏡像做多
TOL 以 AvgRange(200) 為單位。旗形沒有做（「旗桿」很難客觀定義），之後有需要再加。
"""
from .engine import Strategy

PAT_NAMES = {1: ("雙頂", "雙底"), 2: ("三重頂", "三重底"), 3: ("頭肩頂", "頭肩底"),
             4: ("下降三角形", "上升三角形"), 5: ("對稱三角形↓", "對稱三角形↑"), 6: ("上升楔形", "下降楔形")}
PRIORITY = (3, 2, 1, 4, 5, 6)


def _line(i1, p1, i2, p2):
    s = (p2 - p1) / (i2 - i1) if i2 != i1 else 0.0
    return lambda i: p1 + s * (i - i1), s


def _const(v):
    return lambda i: v


def find_patterns(piv, a, tol_x, enabled):
    """piv：已確認轉折 [(類型 'H'/'L', 位置, 價格)…]，最後一個決定目前這一段的方向。
    回傳候選 list：dict(pat, side, name, level(i), stop_ref, height, first)；side 1 = 做多、-1 = 做空。
    stop_ref 是最後一個轉折點的價格（停損放它外側）。"""
    out = []
    if len(piv) < 3:
        return out
    tol = tol_x * a
    last = piv[-1][0]
    # 做空用高點序列（最後一個是高點，目前往下走）；做多鏡像：價格取負號，用同一套判斷
    side = -1 if last == "H" else 1
    sg = 1.0 if side == -1 else -1.0          # 把做多的情況翻成「頂部」來判斷
    P = [(t, i, sg * p) for t, i, p in piv]   # 翻轉後：最後一個一定是「高點」
    n = len(P)

    def pts(k):
        return P[n - k:]

    def unflip(fn):
        return lambda i: sg * fn(i)

    def add(pat, level, height, first):
        out.append(dict(pat=pat, side=side, name=PAT_NAMES[pat][0 if side == -1 else 1],
                        level=unflip(level), stop_ref=sg * P[-1][2], height=height, first=first))

    # 頭肩
    if 3 in enabled and n >= 5:
        (_, i1, h1), (_, j1, l1), (_, i2, h2), (_, j2, l2), (_, i3, h3) = pts(5)
        if h2 > max(h1, h3) + tol and abs(h1 - h3) <= 2 * tol:
            neck, _ = _line(j1, l1, j2, l2)
            add(3, neck, h2 - neck(i2), i1)
    # 三重頂
    if 2 in enabled and n >= 5:
        (_, i1, h1), (_, j1, l1), (_, i2, h2), (_, j2, l2), (_, i3, h3) = pts(5)
        if max(h1, h2, h3) - min(h1, h2, h3) <= tol and abs(l1 - l2) <= tol:
            lv = min(l1, l2)
            add(2, _const(lv), max(h1, h2, h3) - lv, i1)
    # 雙頂
    if 1 in enabled:
        (_, i1, h1), (_, j1, l1), (_, i2, h2) = pts(3)
        if abs(h1 - h2) <= tol:
            add(1, _const(l1), max(h1, h2) - l1, i1)
    if n >= 4:
        (_, j1, l1), (_, i1, h1), (_, j2, l2), (_, i2, h2) = pts(4)
        # 下降三角形（翻轉後就是上升三角形，一樣的式子）：低點水平、高點壓低，跌破水平邊
        if 4 in enabled and abs(l1 - l2) <= tol and h1 - h2 >= tol:
            lv = min(l1, l2)
            add(4, _const(lv), h1 - lv, j1)
        lower, sl = _line(j1, l1, j2, l2)
        upper, su = _line(i1, h1, i2, h2)
        # 對稱三角形：高點壓低、低點墊高，跌破下緣
        if 5 in enabled and h1 - h2 >= tol and l2 - l1 >= tol:
            add(5, lower, h1 - l1, j1)
        # 上升楔形：兩線都往上、下緣比上緣陡（收斂），跌破下緣
        if 6 in enabled and h2 - h1 >= tol and l2 - l1 >= tol and sl > su > 0:
            add(6, lower, h1 - l1, j1)
    return out


class Pattern(Strategy):
    name = "Pattern"
    desc = "多空。古典型態（雙頂底、三重頂底、頭肩、三角形、楔形）：ZigZag 確認轉折 → 收盤突破頸線／趨勢線進場，停損放最後轉折外側、停利 = 量度目標"
    minutes = 60
    inputs = dict(PAT=0, SIDE=0, ZZ=3.0, TOL=0.8, BUF=0.0, SLB=0.2, TPX=1.0, EXP=60, WMAX=400, MAXB=0,
                  MINRR=0.0, TF_N=0, EBX=0)
    doc = dict(PAT="型態：0 全部、1 雙頂底、2 三重頂底、3 頭肩、4 上升／下降三角形、5 對稱三角形、6 楔形",
               SIDE="0 = 多空都做、1 = 只做多、-1 = 只做空",
               ZZ="ZigZag 轉折門檻：反向走超過 AvgRange(200) 的幾倍才確認轉折（越大型態越大、越少）",
               TOL="「差不多高」的容許誤差：AvgRange(200) 的倍數",
               BUF="突破要超過線多少才算：AvgRange(200) 的倍數（0 = 收盤越過就算）",
               SLB="停損放在最後一個轉折點外側多少：AvgRange(200) 的倍數",
               TPX="停利 = 型態高度 × TPX（量度目標；1 = 教科書標準）",
               EXP="最後一個轉折點之後，幾根內沒突破就作廢",
               WMAX="型態最長幾根（從第一個轉折點算）",
               MAXB="持有超過幾根就出場，0 = 不限",
               MINRR="最低報酬風險比（目標距離 ÷ 停損距離，用訊號那根收盤估），0 = 不限",
               TF_N="日線趨勢濾網：前一日收盤在 N 日均線之上才做多、之下才做空，0 = 關閉",
               EBX="進場K災難停損：AvgRange(200) 的倍數，只在進場那根 K 有效，0 = 關閉")

    def __init__(self, params=None):
        super().__init__(params)
        self.piv = []           # 已確認轉折 [(類型, 位置, 價格)]
        self.zd = 0             # ZigZag 目前這一段：1 往上、-1 往下、0 還沒開始
        self.ext = self.exti = None
        self.hi = self.lo = None
        self.hii = self.loi = 0
        self.cands = []
        self.used = set()       # 已經進場過的（型態, 最後轉折位置），同一個型態只做一次
        self.pend = None        # 訊號那根算好的停損停利，成交後接手
        self.plan = None

    # ---------------------------------------------------------- ZigZag
    def _push(self, t, i, p):
        self.piv.append((t, i, p))
        if len(self.piv) > 12:
            del self.piv[0]
        a = self.avgrange(200)
        en = set(PRIORITY) if int(self.p["PAT"]) == 0 else {int(self.p["PAT"])}
        self.cands = find_patterns(self.piv, a, float(self.p["TOL"]), en)
        self.cands.sort(key=lambda c: PRIORITY.index(c["pat"]))

    def _zigzag(self):
        i, h, l = self.i, self.H(), self.L()
        thr = float(self.p["ZZ"]) * self.avgrange(200)
        if self.zd == 0:
            if self.hi is None or h > self.hi:
                self.hi, self.hii = h, i
            if self.lo is None or l < self.lo:
                self.lo, self.loi = l, i
            if self.hi - self.lo >= thr:
                if self.hii > self.loi:           # 先低後高 → 低點確認，往上
                    self._push("L", self.loi, self.lo)
                    self.zd, self.ext, self.exti = 1, self.hi, self.hii
                else:
                    self._push("H", self.hii, self.hi)
                    self.zd, self.ext, self.exti = -1, self.lo, self.loi
            return
        if self.zd == 1:
            if h > self.ext:
                self.ext, self.exti = h, i
            elif self.ext - l >= thr:
                self._push("H", self.exti, self.ext)
                self.zd, self.ext, self.exti = -1, l, i
        else:
            if l < self.ext:
                self.ext, self.exti = l, i
            elif h - self.ext >= thr:
                self._push("L", self.exti, self.ext)
                self.zd, self.ext, self.exti = 1, h, i

    # ---------------------------------------------------------- 主邏輯
    def on_bar(self):
        p = self.p
        self._zigzag()
        # 成交後接手停損停利
        if self.mp != 0 and self.plan is None and self.pend is not None:
            self.plan = self.pend
            self.plan["ei"] = self.i
        if self.mp == 0:
            self.plan = None
        self.pend = None

        if self.mp != 0 and self.plan is not None:
            pl = self.plan
            if int(p["MAXB"]) > 0 and self.i - pl["ei"] + 1 >= int(p["MAXB"]):
                self.exit_market("持有到期")
            elif self.mp > 0:
                self.sell_stop(pl["stop"], "停損")
                self.sell_limit(self.entryprice + pl["tdist"], "量度目標")
            else:
                self.buytocover_stop(pl["stop"], "停損")
                self.buytocover_limit(self.entryprice - pl["tdist"], "量度目標")
            return
        if self.mp != 0 or not self.cands or not self.piv:
            return

        i, c = self.i, self.C()
        a = self.avgrange(200)
        lastp = self.piv[-1]
        if i - lastp[1] > int(p["EXP"]):
            return
        side_ok = int(p["SIDE"])
        for cd in self.cands:
            s = cd["side"]
            if (side_ok and s != side_ok) or i - cd["first"] > int(p["WMAX"]):
                continue
            key = (cd["pat"], lastp[1])
            if key in self.used:
                continue
            lv = cd["level"](i)
            broke = c > lv + float(p["BUF"]) * a if s > 0 else c < lv - float(p["BUF"]) * a
            if not broke or not self.trend_ok(p["TF_N"], s):
                continue
            stop = cd["stop_ref"] - s * float(p["SLB"]) * a
            tdist = cd["height"] * float(p["TPX"])
            risk = (c - stop) * s
            if risk <= 0 or tdist <= 0:
                continue
            if float(p["MINRR"]) > 0 and tdist / risk < float(p["MINRR"]):
                continue
            self.used.add(key)
            self.pend = dict(stop=stop, tdist=tdist, pat=cd["pat"])
            if s > 0:
                self.buy_market(cd["name"] + "突破")
            else:
                self.sellshort_market(cd["name"] + "跌破")
            return
