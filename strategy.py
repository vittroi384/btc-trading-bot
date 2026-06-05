# =====================================================================
#  strategy.py  ―  '언제 사고 언제 팔지'를 정하는 두뇌 부분
# ---------------------------------------------------------------------
#  크게 두 가지 일을 합니다.
#   1) 지표 계산: 가격 데이터로 이동평균선·RSI·볼린저밴드·일목균형표를 계산
#   2) 신호 판단: 그 지표들을 보고 'buy(사라) / sell(팔아라) / hold(가만히)' 결정
#
#  ★ 매매 규칙을 바꾸고 싶으면 맨 아래 generate_signal() 함수만 고치면 됩니다. ★
# =====================================================================

import pandas as pd   # pandas: 표(엑셀 같은 데이터)를 다루는 도구. 가격 데이터를 표로 처리합니다.


# =====================================================================
#  1부. 지표 계산 함수들
#  (df 는 가격 데이터 '표'. 한 줄(행)이 캔들 하나이고, close=종가, high=고가, low=저가)
# =====================================================================

def add_moving_average(df, short, long):
    """
    이동평균선(MA)을 계산해서 표에 새 칸으로 추가합니다.
    이동평균선 = 최근 N개 종가의 평균. 가격을 부드럽게 만들어 '추세'를 보여줍니다.
    short = 단기(짧은 기간), long = 장기(긴 기간).
    """
    df["ma_short"] = df["close"].rolling(short).mean()  # 최근 short개 종가의 평균
    df["ma_long"] = df["close"].rolling(long).mean()    # 최근 long개 종가의 평균
    return df


def add_rsi(df, period=14):
    """
    RSI(상대강도지수)를 계산합니다. 0~100 사이 숫자로 '최근에 얼마나 세게 올랐나'를 봅니다.
    보통 70 이상이면 과열(너무 올랐다), 30 이하면 과매도(너무 빠졌다)로 해석합니다.
    """
    delta = df["close"].diff()         # 전 봉 대비 가격 변화량 (이번 종가 - 직전 종가)
    gain = delta.clip(lower=0)         # 오른 경우의 상승폭만 남김 (내린 경우는 0)
    loss = -delta.clip(upper=0)        # 내린 경우의 하락폭만 남김 (양수로 변환)
    # 아래는 RSI의 표준 계산(Wilder 방식). 상승폭 평균과 하락폭 평균의 비율을 이용합니다.
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss           # 상승 평균 ÷ 하락 평균
    df["rsi"] = 100 - (100 / (1 + rs)) # 공식에 넣어 0~100 값으로 변환
    return df


def add_bollinger(df, period=20, std=2.0):
    """
    볼린저밴드를 계산합니다. 중심선(이동평균)과 그 위/아래로 폭을 둔 상단선·하단선으로 구성.
    가격이 상단에 닿으면 '비싸다', 하단에 닿으면 '싸다' 같은 신호로 봅니다.
    """
    mid = df["close"].rolling(period).mean()  # 중심선 = period 기간 이동평균
    sd = df["close"].rolling(period).std()    # 같은 기간의 표준편차(가격이 얼마나 출렁였는지)
    df["bb_mid"] = mid                        # 중심선
    df["bb_upper"] = mid + std * sd           # 상단선 = 중심선 + (폭 × 출렁임)
    df["bb_lower"] = mid - std * sd           # 하단선 = 중심선 - (폭 × 출렁임)
    return df


def add_ichimoku(df):
    """
    일목균형표를 계산합니다. 여러 선과 '구름(선행스팬1~2 사이 영역)'으로 추세를 봅니다.
    가격이 구름 위에 있으면 상승 추세, 아래면 하락 추세로 해석합니다.
    """
    high, low, close = df["high"], df["low"], df["close"]  # 고가/저가/종가를 따로 꺼냄
    tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2          # 전환선(최근 9봉 고점·저점 중간)
    kijun = (high.rolling(26).max() + low.rolling(26).min()) / 2         # 기준선(최근 26봉 고점·저점 중간)
    span_a = ((tenkan + kijun) / 2).shift(26)                            # 선행스팬1 (구름 한쪽 선, 26봉 앞으로 이동)
    span_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)  # 선행스팬2 (구름 다른쪽 선)
    chikou = close.shift(-26)                                            # 후행스팬(종가를 26봉 뒤로 이동)
    df["ichi_tenkan"] = tenkan
    df["ichi_kijun"] = kijun
    df["ichi_span_a"] = span_a
    df["ichi_span_b"] = span_b
    df["ichi_chikou"] = chikou
    return df


def compute_indicators(df, cfg):
    """위 지표들을 모두 한 번에 계산해서 표에 붙여 돌려줍니다. (cfg = 설정 꾸러미)"""
    df = df.copy()  # 원본을 건드리지 않도록 표를 복사해서 작업
    df = add_moving_average(df, cfg["MA_SHORT"], cfg["MA_LONG"])
    df = add_rsi(df, cfg["RSI_PERIOD"])
    df = add_bollinger(df, cfg["BB_PERIOD"], cfg["BB_STD"])
    df = add_ichimoku(df)
    return df


def min_required_rows(cfg):
    """
    신호를 제대로 계산하려면 최소 몇 개의 봉(데이터)이 필요한지 알려줍니다.
    일목균형표가 과거 52봉 + 26봉 이동을 쓰기 때문에 넉넉히 잡습니다.
    데이터가 이보다 적으면 아직 판단하지 않고 기다립니다.
    """
    return max(cfg["MA_LONG"], cfg["BB_PERIOD"], cfg["RSI_PERIOD"], 52) + 26 + 1


# =====================================================================
#  2부. 매매 신호 결정
#  ★★★ 여기가 '내 전략'을 적는 곳입니다. 이 함수만 고치면 매매 규칙이 바뀝니다. ★★★
# =====================================================================

def generate_signal(df, holding, cfg):
    """
    지금 사야 할지 / 팔아야 할지 / 가만히 있을지를 판단해 글자로 돌려줍니다.

    입력:
      df      : 지표까지 계산된 가격 표 (맨 아래 줄이 '가장 최근 봉')
      holding : 지금 코인을 들고 있는지 (True=보유 중 / False=현금)
      cfg     : 설정 꾸러미

    돌려주는 값: "buy"(사라) / "sell"(팔아라) / "hold"(가만히)
    """
    # 데이터가 충분히 쌓이기 전에는 섣불리 판단하지 않고 'hold'(대기)
    if len(df) < min_required_rows(cfg):
        return "hold"

    last = df.iloc[-1]   # 맨 마지막 줄 = 가장 최근 봉
    prev = df.iloc[-2]   # 그 바로 앞 봉 (직전 봉)

    # ===== 자주 매매하되 헛매수는 줄인 버전 =====
    # 매수는 '아래 묶음 중 하나라도' 뜨면 실행(OR). 단, 첫 번째 묶음은 두 조건을 '동시에'(AND) 본다.
    # 매도는 '아래 신호 중 하나라도'.

    # --- 미리 계산해 두는 값들 ---
    golden_cross = (prev["ma_short"] <= prev["ma_long"]) and (last["ma_short"] > last["ma_long"])  # 골든크로스
    dead_cross = (prev["ma_short"] >= prev["ma_long"]) and (last["ma_short"] < last["ma_long"])     # 데드크로스
    rsi = last["rsi"]
    cloud_top = max(last["ichi_span_a"], last["ichi_span_b"])  # 구름 윗선
    cloud_bot = min(last["ichi_span_a"], last["ichi_span_b"])  # 구름 아랫선
    prev_top = max(prev["ichi_span_a"], prev["ichi_span_b"])
    prev_bot = min(prev["ichi_span_a"], prev["ichi_span_b"])
    cloud_break_up = (prev["close"] <= prev_top) and (last["close"] > cloud_top)  # 구름 위로 돌파
    cloud_break_dn = (prev["close"] >= prev_bot) and (last["close"] < cloud_bot)  # 구름 아래로 이탈

    # ---------- [매수] 아래 중 하나라도 참이면 산다 (단, 과열이면 보류) ----------
    # 1번 묶음은 '둘 다'(AND) 만족해야 함 → 진짜 싼 자리에서만 사서 헛매수를 줄임
    dip_buy = (last["close"] < last["bb_lower"]) and (rsi <= cfg["RSI_BUY_DIP"])  # 볼린저 하단 AND RSI 과매도
    buy_signal = (
        dip_buy            # 1) (볼린저 하단 + RSI 과매도) 동시 → "확실히 싼 자리"
        or golden_cross    # 2) 골든크로스(상승 전환)
        or cloud_break_up  # 3) 일목 구름 상향 돌파(추세 전환)
    )
    not_too_hot = rsi < cfg["RSI_BUY_BELOW"]  # 안전장치: RSI가 너무 과열이면 매수 안 함
    if (not holding) and buy_signal and not_too_hot:
        return "buy"

    # ---------- [매도] 아래 중 하나라도 참이면 판다 ----------
    sell_signal = (
        dead_cross                            # 1) 데드크로스(하락 전환)
        or (rsi >= cfg["RSI_SELL_ABOVE"])     # 2) RSI 과매수(많이 오름)
        or (last["close"] > last["bb_upper"]) # 3) 볼린저 상단 위(상대적으로 비쌈)
        or cloud_break_dn                     # 4) 일목 구름 하향 이탈
    )
    if holding and sell_signal:
        return "sell"

    # 살 신호도 팔 신호도 없으면 가만히
    return "hold"

# -------------------------------------------------------------------
#  [전략을 바꾸고 싶다면?]
#   - 매매를 '더' 자주: RSI_BUY_DIP 를 올리고(예: 42) RSI_SELL_ABOVE 를 내리면(예: 58)
#     신호가 더 자주 뜹니다. (단, 너무 좁히면 사고팔기만 반복 + 수수료 손해)
#   - 매매를 '덜': 위 신호(or 줄)를 일부 빼거나, RSI 기준을 더 극단으로.
#   - 쓸 수 있는 지표 칸: ma_short, ma_long, rsi, bb_mid, bb_upper, bb_lower,
#     ichi_tenkan, ichi_kijun, ichi_span_a, ichi_span_b, ichi_chikou
#   - 바꾼 뒤에는 꼭 backtest.py 로 매매 빈도·결과를 확인하세요.
# -------------------------------------------------------------------
