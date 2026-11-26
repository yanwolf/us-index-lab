"""從 txf-sim（MultiCharts 原碼）移植的四支策略 + 空方鏡像，改成指數 CFD 版（美股、日經、恆指）。

改動原則：進出場邏輯照原碼，只改三件事
  1. 停損、停利、移動停利的「點數」改成「單位」（見 engine.py：AvgRange 倍數或 0.1% 價格）
  2. 拿掉台指結算日（CheckDay / TN）；週末出場改由引擎統一處理（休市前 WKX 分鐘）
  3. 時段（CloseS）改為各商品的主時段 / 延長時段，交易日（CloseD）依各商品的切換點

TF_N（日線趨勢濾網）是美股版新增的，台指原版沒有；預設 0 = 關閉，行為與原版相同。

預設值 = 台指原參數的粗略換算（假設台指 60 分 K 的 AvgRange(100) 約 90 點），
只是起點，本來就要在美股上重新回測調整。
"""
from .engine import Strategy


class TMFF(Strategy):
    name = "TMFF"
    desc = "多空。收盤站上/跌破 MA(N)±AvgRange(200)×倍數，且日線三段連漲/連跌，前高/前低 stop 進場"
    minutes = 60
    inputs = dict(N=30, BB=2.4, SS=0.1, SWing=1, BLOSS=1.4, BWIN=3.3, SLOSS=0.8, SWIN=2.8,
                  BMax=2.2, BTB=1.1, SMax=1.1, STB=1.7)
    doc = dict(N="均線期數", BB="多方過濾：AvgRange(200) 的倍數", SS="空方過濾：AvgRange(200) 的倍數",
               SWing="SwingHigh/Low 左右各幾根", BLOSS="多單停損（單位）", BWIN="多單停利（單位）",
               SLOSS="空單停損（單位）", SWIN="空單停利（單位）", BMax="多單啟動移動停利的浮盈（單位）",
               BTB="多單移動停利回吐（單位）", SMax="空單啟動移動停利的浮盈（單位）", STB="空單移動停利回吐（單位）")

    def __init__(self, params=None):
        super().__init__(params)
        self.BS = 0

    def on_bar(self):
        p = self.p
        if self.closeD(1) > self.closeD(2) and self.closeD(3) > self.closeD(4) and self.closeD(5) > self.closeD(6):
            self.BS = 1
        if self.closeD(1) < self.closeD(2) and self.closeD(3) < self.closeD(4) and self.closeD(5) < self.closeD(6):
            self.BS = -1
        ma = self.avg("C", p["N"])
        ar = self.avgrange(200)
        c = self.C()
        if self.mp != 1 and c > ma + ar * p["BB"] and self.BS == 1:
            self.buy_stop(self.swinghigh(p["SWing"], 200), "SwingHigh 突破")
        if self.mp != -1 and c < ma - ar * p["SS"] and self.BS == -1:
            self.sellshort_stop(self.swinglow(p["SWing"], 200), "SwingLow 跌破")
        ep = self.entryprice
        if self.mp > 0:
            self.sell_stop(ep - self.U(p["BLOSS"]), "停損")
            self.sell_limit(ep + self.U(p["BWIN"]), "停利")
            if self.maxprofit >= self.U(p["BMax"]):
                self.sell_stop(ep + self.maxprofit - self.U(p["BTB"]), "移動停利")
        if self.mp < 0:
            self.buytocover_stop(ep + self.U(p["SLOSS"]), "停損")
            self.buytocover_limit(ep - self.U(p["SWIN"]), "停利")
            if self.maxprofit >= self.U(p["SMax"]):
                self.buytocover_stop(ep - self.maxprofit + self.U(p["STB"]), "移動停利")


class TMFFShort(Strategy):
    """TMFF 只留空方那一半：日線三段連跌（closeD 1<2、3<4、5<6）且收盤跌破 MA(N)−AvgRange(200)×SS，
    SwingLow stop 放空。出場照 TMFF 空單（停損、停利、移動停利）。多了 TF_N 日線趨勢濾網，預設 0 = 關閉。"""
    name = "TMFF_S"
    desc = "只做空（TMFF 空方拆出）。日線三段連跌且收盤跌破 MA(N)−AvgRange(200)×SS，SwingLow stop 放空"
    minutes = 60
    inputs = dict(N=30, SS=0.1, SWing=1, SLOSS=0.8, SWIN=2.8, SMax=1.1, STB=1.7, TF_N=0)
    doc = dict(N="均線期數", SS="空方過濾：AvgRange(200) 的倍數", SWing="SwingLow 左右各幾根",
               SLOSS="空單停損（單位）", SWIN="空單停利（單位）", SMax="啟動移動停利的浮盈（單位）",
               STB="移動停利回吐（單位）", TF_N="日線趨勢濾網：前一日收盤在 N 日均線之下才放空，0 = 關閉")

    def __init__(self, params=None):
        super().__init__(params)
        self.BS = 0

    def on_bar(self):
        p = self.p
        if self.closeD(1) > self.closeD(2) and self.closeD(3) > self.closeD(4) and self.closeD(5) > self.closeD(6):
            self.BS = 1
        if self.closeD(1) < self.closeD(2) and self.closeD(3) < self.closeD(4) and self.closeD(5) < self.closeD(6):
            self.BS = -1
        ma = self.avg("C", p["N"])
        ar = self.avgrange(200)
        if self.mp != -1 and self.C() < ma - ar * p["SS"] and self.BS == -1 and self.trend_ok(p["TF_N"], -1):
            self.sellshort_stop(self.swinglow(p["SWing"], 200), "SwingLow 跌破")
        if self.mp < 0:
            ep = self.entryprice
            self.buytocover_stop(ep + self.U(p["SLOSS"]), "停損")
            self.buytocover_limit(ep - self.U(p["SWIN"]), "停利")
            if self.maxprofit >= self.U(p["SMax"]):
                self.buytocover_stop(ep - self.maxprofit + self.U(p["STB"]), "移動停利")


class ARCrossover(Strategy):
    name = "AR_crossover"
    desc = "只做多。最高價上穿 MA(H)+AvgRange×倍數 市價進場，每日最多 ETD 次"
    minutes = 60
    inputs = dict(MAN=15, ATRN=35, ATRX=0.1, ETD=2, LOSS=0.6, WIN=5.5, Twin=2.2, Tstop=1.1, TF_N=0)
    doc = dict(TF_N="日線趨勢濾網：前一日收盤在 N 日均線之上才進場，0 = 關閉", MAN="最高價均線期數", ATRN="AvgRange 期數", ATRX="AvgRange 倍數", ETD="每日最多進場次數",
               LOSS="停損（單位）", WIN="停利（單位）", Twin="啟動移動停利的浮盈（單位）", Tstop="移動停利回吐（單位）")

    def on_bar(self):
        p = self.p
        band0 = self.avg("H", p["MAN"]) + self.avgrange(p["ATRN"]) * p["ATRX"]
        band1 = self.avg("H", p["MAN"], 1) + self.avgrange(p["ATRN"], 1) * p["ATRX"]
        if self.entries_today < p["ETD"] and self.H(1) <= band1 and self.H() > band0 \
                and self.trend_ok(p["TF_N"], 1):
            self.buy_market("H 上穿 MA+AR")
        if self.mp > 0:
            ep = self.entryprice
            self.sell_stop(ep - self.U(p["LOSS"]), "停損")
            self.sell_limit(ep + self.U(p["WIN"]), "停利")
            if self.maxprofit >= self.U(p["Twin"]):
                self.sell_stop(ep + self.maxprofit - self.U(p["Tstop"]), "移動停利")


class ARCrossunder(Strategy):
    name = "AR_crossunder"
    desc = "只做空。最低價下穿 MA(L)−AvgRange×倍數 市價放空（AR_crossover 的鏡像）"
    minutes = 60
    inputs = dict(ARCrossover.inputs)
    doc = dict(ARCrossover.doc, MAN="最低價均線期數", TF_N="日線趨勢濾網：前一日收盤在 N 日均線之下才放空，0 = 關閉")

    def on_bar(self):
        p = self.p
        band0 = self.avg("L", p["MAN"]) - self.avgrange(p["ATRN"]) * p["ATRX"]
        band1 = self.avg("L", p["MAN"], 1) - self.avgrange(p["ATRN"], 1) * p["ATRX"]
        if self.entries_today < p["ETD"] and self.L(1) >= band1 and self.L() < band0 \
                and self.trend_ok(p["TF_N"], -1):
            self.sellshort_market("L 下穿 MA−AR")
        if self.mp < 0:
            ep = self.entryprice
            self.buytocover_stop(ep + self.U(p["LOSS"]), "停損")
            self.buytocover_limit(ep - self.U(p["WIN"]), "停利")
            if self.maxprofit >= self.U(p["Twin"]):
                self.buytocover_stop(ep - self.maxprofit + self.U(p["Tstop"]), "移動停利")


GY_DOC = dict(
    N1="幾週前的週高/週低（0=本週）", N2="幾個時段前的收盤（時段定義看 SES）", N3="區間中值的期數",
    SES="CloseS 時段定義：0 主時段／延長時段、1 交易日、2 等長兩段、3 東京／倫敦／紐約三段",
    LOSS="停損（單位）", WIN="停利（單位）", Twin="啟動移動停利的浮盈（單位）", Tstop="移動停利回吐（單位）",
    MA1="均線濾網快線（1 = 直接用收盤價）", MA2="均線濾網慢線期數（0 = 關閉），只管進場",
    TF_N="日線趨勢濾網：前一日收盤在 N 日均線之上／下才進場，0 = 關閉",
    EBX="進場K災難停損：AvgRange(200) 的倍數，只在進場那根 K 有效，0 = 關閉")


def _gy_values(st):
    p = st.p
    mid = (st.highest("C", p["N3"]) + st.lowest("C", p["N3"])) * 0.5
    cs = st.closeS(p["N2"])
    return (st.highW(p["N1"]) + cs + mid) / 3, (st.lowW(p["N1"]) + cs + mid) / 3


class GuYuan2024(Strategy):
    """txf-sim sb_b21GuYuan_2024（只做多），2026-11 交接版：加上均線濾網、EBX、CloseS 時段定義。
    預設值 = 台指最終版換算（LOSS 100／WIN 400／Twin 30／Tstop 300 點 ≈ 1.1／4.4／0.3／3.3 單位），
    EBX 預設 0（台指最終版是 4，但在多方只有扣分或無影響；跑台指參數時另外帶入）。"""
    name = "GuYuan_2024"
    desc = "只做多。(週高+時段收+區間中值)/3 合成價位，H 站上 → N 根最高 stop 進場；L 跌破 → N 根最低 stop 出場"
    minutes = 60
    inputs = dict(N1=3, N2=3, N3=5, SES=0, HH=10, LL=10, LOSS=1.1, WIN=4.4, Twin=0.3, Tstop=3.3,
                  MA1=1, MA2=0, TF_N=0, EBX=0)
    doc = dict(GY_DOC, HH="進場：幾根最高價", LL="出場：幾根最低價")

    def on_bar(self):
        p = self.p
        v1, v2 = _gy_values(self)
        if self.mp != 1 and self.H() > v1 and self.ma_trend(p["MA1"], p["MA2"], 1) and self.trend_ok(p["TF_N"], 1):
            self.buy_stop(self.highest("H", p["HH"]), "N 根最高突破")
        if self.mp == 1 and self.L() < v2:
            self.sell_stop(self.lowest("L", p["LL"]), "N 根最低跌破")
        if self.mp > 0:
            ep = self.entryprice
            self.sell_stop(ep - self.U(p["LOSS"]), "停損")
            self.sell_limit(ep + self.U(p["WIN"]), "停利")
            if self.maxprofit > self.U(p["Twin"]):
                self.sell_stop(ep + self.maxprofit - self.U(p["Tstop"]), "移動停利")


class GuYuan2024Short(Strategy):
    """GuYuan_2024 的空方鏡像（txf-sim GuYuan_2024_S）：L 跌破週低版合成價 → N 根最低 stop 放空；
    H 站上週高版合成價 → N 根最高 stop 回補。預設值 = 台指最終版（LL 14、EBX 1）。"""
    name = "GuYuan_2024_S"
    desc = "只做空。(週低+時段收+區間中值)/3 合成價位，L 跌破 → N 根最低 stop 放空；H 站上 → N 根最高 stop 回補（GuYuan_2024 鏡像）"
    minutes = 60
    inputs = dict(N1=3, N2=3, N3=5, SES=0, HH=10, LL=14, LOSS=1.1, WIN=4.4, Twin=0.3, Tstop=3.3,
                  MA1=1, MA2=0, TF_N=0, EBX=1)
    doc = dict(GY_DOC, LL="進場：幾根最低價", HH="出場：幾根最高價")

    def on_bar(self):
        p = self.p
        v1, v2 = _gy_values(self)
        if self.mp != -1 and self.L() < v2 and self.ma_trend(p["MA1"], p["MA2"], -1) and self.trend_ok(p["TF_N"], -1):
            self.sellshort_stop(self.lowest("L", p["LL"]), "N 根最低跌破")
        if self.mp == -1 and self.H() > v1:
            self.buytocover_stop(self.highest("H", p["HH"]), "N 根最高突破")
        if self.mp < 0:
            ep = self.entryprice
            self.buytocover_stop(ep + self.U(p["LOSS"]), "停損")
            self.buytocover_limit(ep - self.U(p["WIN"]), "停利")
            if self.maxprofit > self.U(p["Twin"]):
                self.buytocover_stop(ep - self.maxprofit + self.U(p["Tstop"]), "移動停利")


# 台指最終版參數（GUYUAN_HANDOFF 第 3 節，點數換成單位）：批次回測「台指參數」用
TXF_PRESET = {
    "GuYuan_2024": dict(N1=3, N2=3, N3=5, HH=10, LL=10, LOSS=1.1, WIN=4.4, Twin=0.3, Tstop=3.3, MA1=1, MA2=0, EBX=4),
    "GuYuan_2024_S": dict(N1=3, N2=3, N3=5, HH=10, LL=14, LOSS=1.1, WIN=4.4, Twin=0.3, Tstop=3.3, MA1=1, MA2=0, EBX=1),
}


class GuYuan2025(Strategy):
    name = "GuYuan_2025"
    desc = "只做多。合成價位 + 均線多頭排列（MA1>MA2），N 根最高 stop 進場，每日最多 ETD 次"
    minutes = 20
    inputs = dict(HN=25, N1=0, N2=3, N3=50, MA1=45, MA2=175, ETD=5, LOSS=1.0, WIN=6.2, TF_N=0)
    doc = dict(TF_N="日線趨勢濾網：前一日收盤在 N 日均線之上才進場，0 = 關閉", HN="進場：幾根最高價", N1="幾週前的週高（0=本週）", N2="幾天前的日收盤（0=目前）",
               N3="區間中值的期數", MA1="短均線期數", MA2="長均線期數", ETD="每日最多進場次數",
               LOSS="停損（單位）", WIN="停利（單位）")

    def on_bar(self):
        p = self.p
        mid = (self.highest("C", p["N3"]) + self.lowest("C", p["N3"])) * 0.5
        v1 = (self.highW(p["N1"]) + self.closeD(p["N2"]) + mid) / 3
        if self.mp != 1 and self.entries_today < p["ETD"] and self.H() > v1 \
                and p["MA1"] < p["MA2"] and self.avg("C", p["MA1"]) > self.avg("C", p["MA2"]) \
                and self.trend_ok(p["TF_N"], 1):
            self.buy_stop(self.highest("H", p["HN"]), "N 根最高突破")
        if self.mp > 0:
            ep = self.entryprice
            self.sell_stop(ep - self.U(p["LOSS"]), "停損")
            self.sell_limit(ep + self.U(p["WIN"]), "停利")


class TurtleMA(Strategy):
    """刀神「海龜突破 + 均線三刀流」（2026-11-26 新增，非台指移植）。

    方向（海龜）：收盤突破前 DC 根最高價 → 偏多；跌破前 DC 根最低價 → 偏空；狀態延續到反向突破。
      60 分 K 的 DC=80 ≈ 4 小時 K 的 20 根。用收盤確認、只看前一根以前的高低點，沒有偷看。
    進場（三刀流）：偏多且 MA1 > MA2 > MA3（20 > 60 > 240）、MA1 斜率向上 → 下一根開盤市價做多；空方鏡像。
      斜率也要同向，避免「張飛斜率出場」後下一根馬上又進場。
    出場：硬停損 LOSS 單位；張飛：MA1 斜率反轉（XS=1）；方向翻轉；X60=1 時收盤跌破／站上 MA2（關羽）也出場。
    跟 GuYuan_2025 的差別：突破週期約長 10 倍、突破只當方向濾網、沒有固定停利（讓獲利跑）、可做空。"""
    name = "TurtleMA"
    desc = "多空。前 DC 根高低點突破定方向 + MA1>MA2>MA3 排列進場，MA1 斜率反轉／方向翻轉／硬停損出場，無固定停利"
    minutes = 60
    inputs = dict(DC=80, MA1=20, MA2=60, MA3=240, SLP=1, LOSS=3.0, XS=1, X60=0, SIDE=0, TF_N=0)
    doc = dict(DC="海龜方向：前幾根的最高／最低（60 分 K 的 80 ≈ 4 小時 K 的 20）",
               MA1="張飛：短均線（斜率出場）", MA2="關羽：中均線", MA3="劉備：長均線",
               SLP="斜率：MA1 跟幾根前的 MA1 比", LOSS="硬停損（單位）",
               XS="1 = MA1 斜率反轉就出場（張飛收兵），0 = 關閉", X60="1 = 收盤跌破／站上 MA2 也出場（關羽），0 = 關閉",
               SIDE="0 = 多空都做、1 = 只做多、-1 = 只做空",
               TF_N="日線趨勢濾網：前一日收盤在 N 日均線之上才做多、之下才做空，0 = 關閉")

    def __init__(self, params=None):
        super().__init__(params)
        self.dir = 0

    def on_bar(self):
        p = self.p
        dc = int(p["DC"])
        if self.i >= dc:
            c = self.C()
            if c > self.highest("H", dc, 1):
                self.dir = 1
            elif c < self.lowest("L", dc, 1):
                self.dir = -1
        m1, m2, m3 = self.avg("C", p["MA1"]), self.avg("C", p["MA2"]), self.avg("C", p["MA3"])
        slope = m1 - self.avg("C", p["MA1"], int(p["SLP"]))
        warm = self.i >= max(int(p["MA3"]), dc)
        side = int(p["SIDE"])

        if self.mp > 0:
            if self.dir < 0:
                self.sell_market("海龜方向翻空")
            elif p["XS"] and slope < 0:
                self.sell_market("張飛：MA1 斜率轉負")
            elif p["X60"] and self.C() < m2:
                self.sell_market("關羽：跌破 MA2")
            else:
                self.sell_stop(self.entryprice - self.U(p["LOSS"]), "停損")
        elif self.mp < 0:
            if self.dir > 0:
                self.buytocover_market("海龜方向翻多")
            elif p["XS"] and slope > 0:
                self.buytocover_market("張飛：MA1 斜率轉正")
            elif p["X60"] and self.C() > m2:
                self.buytocover_market("關羽：站上 MA2")
            else:
                self.buytocover_stop(self.entryprice + self.U(p["LOSS"]), "停損")
        elif warm:
            if side >= 0 and self.dir > 0 and m1 > m2 > m3 and slope > 0 and self.trend_ok(p["TF_N"], 1):
                self.buy_market("海龜偏多 + 多頭排列")
            elif side <= 0 and self.dir < 0 and m1 < m2 < m3 and slope < 0 and self.trend_ok(p["TF_N"], -1):
                self.sellshort_market("海龜偏空 + 空頭排列")


REGISTRY = {c.name: c for c in (TMFF, TMFFShort, ARCrossover, ARCrossunder, GuYuan2024, GuYuan2024Short, GuYuan2025, TurtleMA)}
