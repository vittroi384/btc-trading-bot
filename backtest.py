# =====================================================================
#  backtest.py  ―  '과거 데이터로 전략을 미리 시험'해 보는 파일
# ---------------------------------------------------------------------
#  실행 방법:  python backtest.py
#  - 최근 가격 기록을 가져와서, "만약 이 전략으로 그동안 매매했다면?"을 계산합니다.
#  - 매매 횟수, 승률, 수익률을 보여줍니다.
#  - ★ 실제 주문은 절대 하지 않습니다. 순수하게 계산만 합니다. ★
#
#  주의: 과거에 잘 됐다고 미래에도 잘 된다는 보장은 없습니다. 어디까지나 참고용.
# =====================================================================

import pyupbit  # 업비트에서 과거 가격을 가져오는 데 사용

from config import CONFIG
from strategy import compute_indicators, generate_signal, min_required_rows

FEE = 0.0005  # 매매 수수료 (실제와 비슷하게 반영)


def run_backtest(df, cfg, start_krw=1_000_000):
    """
    과거 가격 표(df)를 처음부터 끝까지 훑으며 전략대로 사고팔았다고 가정하고,
    최종 성적을 계산해서 돌려줍니다.
    start_krw = 시작 자금(가정). 기본 100만원.
    """
    df = compute_indicators(df, cfg)  # 먼저 지표 계산
    # 아래는 시뮬레이션 동안 값이 변하는 변수들입니다.
    krw = start_krw   # 현재 가진 원화
    coin = 0.0        # 현재 가진 코인 수량
    entry = 0.0       # 산 가격
    peak = 0.0        # 보유 중 최고가 (트레일링 스탑용)
    holding = False   # 들고 있는지
    trades = sells = wins = 0   # 거래 수, 매도 수, 이긴 횟수
    realized = 0.0    # 누적 실현 손익
    start = min_required_rows(cfg)  # 지표가 안정되는 시점부터 시작

    # 한 봉씩 앞으로 가면서, 그 시점까지의 데이터로 판단을 반복합니다.
    for i in range(start, len(df)):
        window = df.iloc[: i + 1]            # 처음부터 i번째 봉까지 (그 시점에 볼 수 있는 데이터)
        price = float(window.iloc[-1]["close"])  # 그 시점의 종가

        # (1) 손절/트레일링/익절 먼저 확인 (trader.check_risk 와 동일한 규칙)
        if holding and entry > 0:
            peak = max(peak, price)                   # 보유 중 최고가 갱신
            chg = (price - entry) / entry * 100        # 산 가격 대비 변화율(%)
            hit = False
            if cfg["STOP_LOSS_PCT"] < 0 and chg <= cfg["STOP_LOSS_PCT"]:
                hit = True                            # 손절
            else:
                trail = cfg.get("TRAIL_STOP_PCT", 0.0)
                if trail and trail > 0:               # 트레일링 켜짐
                    peak_gain = (peak - entry) / entry * 100
                    if peak_gain >= trail and (peak - price) / peak * 100 >= trail:
                        hit = True                    # 최고가 대비 trail% 하락
                elif cfg["TAKE_PROFIT_PCT"] > 0 and chg >= cfg["TAKE_PROFIT_PCT"]:
                    hit = True                        # 고정 익절(트레일링 끈 경우만)
            if hit:  # 위험관리 선에 닿으면 팝니다
                proceeds = coin * price * (1 - FEE)
                pnl = proceeds - coin * entry
                realized += pnl
                krw += proceeds
                wins += 1 if pnl > 0 else 0
                sells += 1
                trades += 1
                holding, coin, entry, peak = False, 0.0, 0.0, 0.0
                continue

        # (2) 전략 신호 확인
        sig, _ = generate_signal(window, holding, cfg)  # 백테스트에선 사유는 안 씀
        if sig == "buy" and not holding:        # 사라 신호 + 현금
            spend = krw * cfg["ORDER_RATIO"] if cfg["USE_RATIO"] else min(cfg["ORDER_KRW"], krw)
            if spend >= 5000:
                coin = spend * (1 - FEE) / price
                entry = price
                peak = price
                krw -= spend
                holding = True
                trades += 1
        elif sig == "sell" and holding:         # 팔아라 신호 + 보유
            proceeds = coin * price * (1 - FEE)
            pnl = proceeds - coin * entry
            realized += pnl
            krw += proceeds
            wins += 1 if pnl > 0 else 0
            sells += 1
            trades += 1
            holding, coin, entry, peak = False, 0.0, 0.0, 0.0

    # 끝났을 때, 들고 있는 코인은 마지막 가격으로 평가해 총자산을 계산합니다.
    last_price = float(df.iloc[-1]["close"])
    equity = krw + coin * last_price   # 총자산 = 현금 + (코인 × 현재가)
    return {
        "trades": trades,
        "sells": sells,
        "win_rate": (wins / sells * 100) if sells else 0.0,
        "start_krw": start_krw,
        "end_equity": equity,
        "roi": (equity - start_krw) / start_krw * 100,  # 수익률(%)
        "realized": realized,
    }


# 이 파일을 '직접' 실행하면 아래가 동작합니다.
if __name__ == "__main__":
    cfg = CONFIG
    # 업비트에서 최근 봉 가져오기 (200개를 넘으면 pyupbit 가 알아서 나눠 받아옵니다)
    df = pyupbit.get_ohlcv(cfg["TICKER"], interval=cfg["INTERVAL"], count=2000)
    if df is None:
        print("데이터를 못 가져왔어. 인터넷/티커를 확인해.")
    else:
        r = run_backtest(df, cfg)  # 시뮬레이션 실행
        # 결과를 화면에 출력
        print(f"[백테스트] {cfg['TICKER']} / {cfg['INTERVAL']} / 최근 {len(df)}봉")
        print(f"총 매매 {r['trades']}회, 매도 {r['sells']}회, 승률 {r['win_rate']:.1f}%")
        print(f"시작 {r['start_krw']:,.0f}원 → 평가 {r['end_equity']:,.0f}원 (수익률 {r['roi']:.2f}%)")
        print("※ 과거 성과가 미래 수익을 보장하지 않음.")
