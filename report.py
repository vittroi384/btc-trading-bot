# =====================================================================
#  report.py  ―  [분석/서빙 단계] 창고 데이터로 요약 + 대시보드 차트
# ---------------------------------------------------------------------
#  실행:  python report.py
#  창고(DB)에 쌓인 데이터를 읽어서:
#    1) 텍스트 요약 출력 (캔들 수·기간·매매 성적)
#    2) 다크 대시보드 차트(report.png) 저장:
#       상단 KPI 카드 + 가격(그라데이션)·볼린저밴드 + 거래량 + RSI + 매수/매도
#  → 포트폴리오에 넣을 '대시보드' 결과물.
#
#  ※ 실무에선 Grafana / Metabase 같은 BI 도구를 창고에 연결해서 본다.
#     여기선 의존성 가볍게 matplotlib 로 보기 좋은 이미지 한 장을 뽑는다.
# =====================================================================

import glob

import matplotlib
matplotlib.use("Agg")   # 화면 없는 서버에서도 그림 저장 가능하게(헤드리스)
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.colors as mcolors
import matplotlib.patheffects as pe
from matplotlib import font_manager, rcParams
from matplotlib.ticker import FuncFormatter
from matplotlib.patches import Rectangle, Polygon, Patch
import numpy as np
import pandas as pd

import db
from config import CONFIG

# ---- 다크 테마 색상 (한곳에서 관리) ----
C = {
    "bg": "#0f1320", "panel": "#141a2b", "card": "#192032",
    "card_edge": "#283250",
    "price": "#3ec6ff", "ma": "#ffb454",
    "bb_fill": "#8fa0c8", "bb_edge": "#39435f",
    "buy": "#22d39a", "sell": "#ff5470", "rsi": "#b08bff",
    "vol_up": "#22d39a", "vol_dn": "#ff5470",
    "grid": "#222a40", "spine": "#2b3450",
    "text": "#e7ebf5", "muted": "#8b93a7",
}


def _set_korean_font():
    """
    시스템에 한글 폰트(나눔고딕 등)가 있으면 차트에서 쓰도록 등록한다.
    찾으면 폰트 이름을 돌려주고, 없으면 None.
    (한글 폰트 설치 방법:  sudo apt install -y fonts-nanum)
    """
    patterns = [
        "/usr/share/fonts/**/Nanum*.ttf",
        "/usr/share/fonts/**/NotoSansCJK*.*",
        "/usr/share/fonts/**/NotoSansKR*.*",
        "/usr/share/fonts/**/malgun*.ttf",
    ]
    for pat in patterns:
        for path in glob.glob(pat, recursive=True):
            try:
                font_manager.fontManager.addfont(path)
            except Exception:
                pass
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in ["NanumGothic", "NanumBarunGothic", "NanumSquare",
                 "Noto Sans CJK KR", "Noto Sans KR", "Malgun Gothic"]:
        if name in available:
            rcParams["font.family"] = name
            rcParams["axes.unicode_minus"] = False  # 한글 폰트에서 마이너스 부호 깨짐 방지
            return name
    return None


def text_summary(cfg):
    """창고 현황과 매매 성적을 글로 요약해 출력한다."""
    market, timeframe, db_file = cfg["TICKER"], cfg["INTERVAL"], cfg["DB_FILE"]
    st = db.get_stats(db_file, market, timeframe)
    trades = db.get_trades(db_file)

    print("=" * 50)
    print(f"  데이터 창고 리포트 — {market} {timeframe}")
    print("=" * 50)
    print(f"저장된 캔들 수 : {st['candle_count']:,}개")
    print(f"기간           : {st['first']}  ~  {st['last']}")
    if len(trades):
        sells = trades[trades["side"] == "sell"]
        realized = sells["pnl"].fillna(0).sum() if len(sells) else 0.0
        wins = int((sells["pnl"].fillna(0) > 0).sum()) if len(sells) else 0
        win_rate = (wins / len(sells) * 100) if len(sells) else 0.0
        print(f"매매 횟수      : {len(trades)}회 (매도 {len(sells)}회)")
        print(f"실현 손익      : {realized:,.0f}원")
        print(f"승률           : {win_rate:.1f}%")
    else:
        print("매매 기록      : 아직 없음")
    print("=" * 50)


def _trade_stats(trades):
    """매매 기록에서 KPI(실현손익·승률·횟수)를 뽑는다."""
    n = len(trades)
    if n == 0:
        return 0, 0.0, 0.0
    sells = trades[trades["side"] == "sell"]
    realized = sells["pnl"].fillna(0).sum() if len(sells) else 0.0
    wins = int((sells["pnl"].fillna(0) > 0).sum()) if len(sells) else 0
    win_rate = (wins / len(sells) * 100) if len(sells) else 0.0
    return n, float(realized), float(win_rate)


def _style_axis(ax):
    """축 공통 스타일(다크): 위/오른쪽 테두리 제거, 옅은 가로 그리드."""
    ax.set_facecolor(C["panel"])
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(C["spine"])
        ax.spines[s].set_linewidth(0.8)
    ax.grid(axis="y", color=C["grid"], linewidth=0.9)
    ax.set_axisbelow(True)
    ax.tick_params(colors=C["muted"], labelsize=9, length=0)


def _gradient_under(ax, xnum, y, color, ylo, yhi):
    """선 아래를 위→아래로 옅어지는 그라데이션으로 채운다(면적 차트 느낌)."""
    rgb = mcolors.to_rgb(color)
    grad = np.empty((256, 1, 4))
    grad[:, :, 0], grad[:, :, 1], grad[:, :, 2] = rgb
    grad[:, :, 3] = np.linspace(0.42, 0.0, 256)[:, None]   # 위 진하게 → 아래 투명
    im = ax.imshow(grad, aspect="auto", origin="upper",
                   extent=[xnum.min(), xnum.max(), ylo, yhi], zorder=1)
    poly = Polygon(np.vstack([[xnum.min(), ylo],
                              np.column_stack([xnum, y]),
                              [xnum.max(), ylo]]),
                   closed=True, facecolor="none", edgecolor="none")
    ax.add_patch(poly)
    im.set_clip_path(poly)


def _draw_candles(ax, xnum, o, h, l, c, width, up_col, dn_col):
    """가격 패널에 캔들(봉)차트를 그린다. 상승(종가≥시가)=초록, 하락=빨강.
    - 심지(고가~저가)는 얇은 세로선, 몸통(시가~종가)은 사각형으로 그립니다."""
    up = c >= o
    colors = np.where(up, up_col, dn_col)
    # 심지: 고가~저가를 잇는 얇은 세로선(한 번에 그림)
    ax.vlines(xnum, l, h, color=colors, linewidth=0.9, zorder=4)
    # 몸통: 시가~종가 사각형(봉마다)
    for i in range(len(xnum)):
        col = up_col if up[i] else dn_col
        lo = min(o[i], c[i])
        hi = max(o[i], c[i])
        height = hi - lo
        if height <= 0:                       # 시가=종가(도지)면 아주 얇은 몸통으로 보이게
            height = (h[i] - l[i]) * 0.02 or (lo * 1e-4 or 1e-6)
        ax.add_patch(Rectangle((xnum[i] - width / 2, lo), width, height,
                               facecolor=col, edgecolor=col, linewidth=0.6, zorder=5))


def make_chart(cfg, out_path="report.png"):
    """다크 대시보드 차트(가격·볼린저·거래량·RSI·매매·KPI)를 PNG로 저장한다."""
    market, timeframe, db_file = cfg["TICKER"], cfg["INTERVAL"], cfg["DB_FILE"]

    candles = db.get_recent_candles(db_file, market, timeframe, limit=cfg["CANDLE_COUNT"])
    ind = db.get_recent_indicators(db_file, market, timeframe, limit=cfg["CANDLE_COUNT"])
    if ind is None or len(ind) == 0 or candles is None or len(candles) == 0:
        print("그릴 데이터가 없음 (먼저 pipeline 실행 필요)")
        return None
    df = candles.join(
        ind[["ma_short", "ma_long", "rsi", "bb_mid", "bb_upper", "bb_lower"]], how="inner")

    # 한글 폰트는 그림 만들기 '전에' 지정해야 적용됨. 없으면 영어로 폴백.
    kor = _set_korean_font()
    if kor:
        T = {"close": "종가", "bb": "볼린저밴드", "ma": "장기 이평선", "vol": "거래량",
             "buy": "매수", "sell": "매도", "up": "상승", "dn": "하락",
             "k_price": "최근가", "k_pnl": "실현손익", "k_win": "승률", "k_cnt": "매매"}
    else:
        print("한글 폰트가 없어 영어 라벨로 표시합니다. "
              "한글로 보려면  sudo apt install -y fonts-nanum  후 다시 실행하세요.")
        T = {"close": "Close", "bb": "Bollinger", "ma": "Long MA", "vol": "Volume",
             "buy": "Buy", "sell": "Sell", "up": "Up", "dn": "Down",
             "k_price": "Last", "k_pnl": "Realized P&L", "k_win": "Win rate", "k_cnt": "Trades"}

    xnum = mdates.date2num(df.index.to_pydatetime())
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    close = df["close"].values
    step = np.median(np.diff(xnum)) if len(xnum) > 1 else 0.04   # 봉 간격(캔들·거래량 막대 너비용)
    last_t, last_p = xnum[-1], float(close[-1])
    hi_all, lo_all = float(np.nanmax(h)), float(np.nanmin(l))     # 심지까지 포함해 y범위 산정
    pad = (hi_all - lo_all) * 0.10 or hi_all * 0.02
    ylo, yhi = lo_all - pad, hi_all + pad

    trades = db.get_trades(db_file)
    n_tr, realized, win_rate = _trade_stats(trades)

    # ---- 도화지 + 4단(헤더/가격/거래량/RSI) ----
    fig, (ax0, ax1, ax2, ax3) = plt.subplots(
        4, 1, figsize=(12, 9.0), height_ratios=[0.9, 3, 0.8, 1.15])
    fig.patch.set_facecolor(C["bg"])
    fig.subplots_adjust(left=0.075, right=0.965, top=0.88, bottom=0.07, hspace=0.16)
    for ax in (ax1, ax2, ax3):
        _style_axis(ax)
        ax.set_xlim(xnum.min(), xnum.max())

    # ===== 제목 (도화지 상단) =====
    fig.text(0.075, 0.955, market, color=C["text"], fontsize=18, fontweight="bold", va="center")
    fig.text(0.075 + 0.0009 * len(market) * 18, 0.953, f"   ·   {timeframe}",
             color=C["muted"], fontsize=12, va="center")
    fig.text(0.965, 0.953, f"{str(df.index[0])[:10]}  ~  {str(df.index[-1])[:10]}",
             color=C["muted"], fontsize=10, va="center", ha="right")

    # ===== 헤더: KPI 카드 4개 =====
    ax0.axis("off")
    pnl_col = C["buy"] if realized >= 0 else C["sell"]
    kpis = [(T["k_price"], f"{last_p/1e4:,.0f}만", C["price"]),
            (T["k_pnl"], f"{realized:+,.0f}원", pnl_col),
            (T["k_win"], f"{win_rate:.0f}%", C["text"]),
            (T["k_cnt"], f"{n_tr}회", C["text"])]
    tile_w, gap = 0.235, 0.02
    for i, (label, val, col) in enumerate(kpis):
        x = i * (tile_w + gap)
        ax0.add_patch(Rectangle((x, 0.08), tile_w, 0.84, transform=ax0.transAxes,
                                facecolor=C["card"], edgecolor=C["card_edge"], linewidth=1.2))
        ax0.add_patch(Rectangle((x, 0.08), 0.006, 0.84, transform=ax0.transAxes,
                                facecolor=col, edgecolor="none"))
        ax0.text(x + 0.022, 0.66, label, transform=ax0.transAxes,
                 fontsize=10.5, color=C["muted"], va="center")
        ax0.text(x + 0.022, 0.33, val, transform=ax0.transAxes,
                 fontsize=16, fontweight="bold", color=col, va="center")

    # ===== 1) 가격 패널 (캔들/봉 차트) =====
    ax1.fill_between(xnum, df["bb_lower"], df["bb_upper"],
                     color=C["bb_fill"], alpha=0.10, zorder=2, label=T["bb"])
    ax1.plot(xnum, df["bb_upper"], color=C["bb_edge"], linewidth=0.8, zorder=2)
    ax1.plot(xnum, df["bb_lower"], color=C["bb_edge"], linewidth=0.8, zorder=2)
    ax1.plot(xnum, df["ma_long"], color=C["ma"], linewidth=1.3, alpha=0.95, zorder=3, label=T["ma"])
    _draw_candles(ax1, xnum, o, h, l, close, step * 0.7, C["vol_up"], C["vol_dn"])  # 종가 선 대신 캔들

    if n_tr:
        trades["ts"] = pd.to_datetime(trades["ts"])
        for side, mk, col, lab in [("buy", "^", C["buy"], T["buy"]),
                                   ("sell", "v", C["sell"], T["sell"])]:
            sub = trades[trades["side"] == side]
            if len(sub):
                ax1.scatter(mdates.date2num(sub["ts"].dt.to_pydatetime()), sub["price"],
                            marker=mk, s=100, color=col, edgecolors=C["bg"],
                            linewidths=1.2, zorder=6, label=lab)

    ax1.scatter([last_t], [last_p], s=30, color=C["price"], zorder=7,
                edgecolors=C["bg"], linewidths=1.0)
    ax1.annotate(f"{last_p/1e4:,.0f}만", (last_t, last_p), xytext=(8, 0),
                 textcoords="offset points", va="center", fontsize=9.5,
                 fontweight="bold", color=C["price"])
    ax1.set_ylim(ylo, yhi)
    ax1.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v/1e4:,.0f}만"))
    ax1.tick_params(labelbottom=False)
    # 범례에 상승/하락 봉 색을 함께 표시
    h_handles, h_labels = ax1.get_legend_handles_labels()
    up_proxy = Patch(facecolor=C["vol_up"], edgecolor=C["vol_up"])
    dn_proxy = Patch(facecolor=C["vol_dn"], edgecolor=C["vol_dn"])
    leg = ax1.legend([up_proxy, dn_proxy] + h_handles, [T["up"], T["dn"]] + h_labels,
                     loc="upper left", fontsize=9, frameon=True, facecolor=C["panel"],
                     edgecolor=C["card_edge"], framealpha=0.92, ncol=3, labelcolor=C["text"])
    leg.set_zorder(8)

    # ===== 2) 거래량 패널 =====
    step = np.median(np.diff(xnum)) if len(xnum) > 1 else 0.04
    up = df["close"].values >= df["open"].values
    ax2.bar(xnum, df["volume"], width=step * 0.8,
            color=np.where(up, C["vol_up"], C["vol_dn"]), alpha=0.55, linewidth=0)
    ax2.set_ylabel(T["vol"], fontsize=9, color=C["muted"])
    ax2.tick_params(labelleft=False, labelbottom=False)

    # ===== 3) RSI 패널 =====
    ax3.axhspan(cfg["RSI_SELL_ABOVE"], 100, color=C["sell"], alpha=0.07)   # 과매수
    ax3.axhspan(0, cfg["RSI_BUY_DIP"], color=C["buy"], alpha=0.07)         # 과매도
    ax3.plot(xnum, df["rsi"], color=C["rsi"], linewidth=1.4)
    ax3.axhline(cfg["RSI_SELL_ABOVE"], color=C["sell"], ls="--", lw=0.7, alpha=0.6)
    ax3.axhline(cfg["RSI_BUY_DIP"], color=C["buy"], ls="--", lw=0.7, alpha=0.6)
    ax3.set_ylim(0, 100)
    ax3.set_yticks([0, cfg["RSI_BUY_DIP"], 50, cfg["RSI_SELL_ABOVE"], 100])
    ax3.set_ylabel("RSI", fontsize=9, color=C["muted"])
    loc = mdates.AutoDateLocator()
    ax3.xaxis.set_major_locator(loc)
    ax3.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))

    fig.savefig(out_path, dpi=130, facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"차트 저장 완료 → {out_path}")
    return out_path


if __name__ == "__main__":
    db.init_db(CONFIG["DB_FILE"])
    text_summary(CONFIG)
    make_chart(CONFIG)
