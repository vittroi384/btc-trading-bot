# =====================================================================
#  bot.py  ―  봇의 '심장'. 실제로 실행하는 메인 파일
# ---------------------------------------------------------------------
#  실행 방법:  python bot.py
#  종료 방법:  키보드 Ctrl+C  또는  텔레그램에서 /stop
#
#  하는 일(계속 반복):
#   1) 업비트에서 최근 가격(캔들)을 가져온다
#   2) 지표를 계산한다
#   3) 손절/익절 선에 닿았는지 확인한다
#   4) 전략이 '사라/팔아라' 신호를 주면 주문한다
#   5) 가끔 성적표(요약)를 텔레그램으로 보낸다
#
#  ★ 처음엔 .env 의 DRY_RUN=true (연습 모드)로 충분히 돌려본 뒤 실거래로 바꾸세요. ★
# =====================================================================

import json       # 일시정지/재개 신호를 파일로 주고받기 (웹 대시보드와 공유)
import threading  # 동시에 두 가지 일 하기 (차트 만드는 동안 중복 실행 막는 '자물쇠'에 사용)
import time       # 시간 관련 (일정 간격으로 쉬기 등)

import pyupbit  # 업비트 통신 도구

import db        # 데이터 창고 (차트 그리기 전 테이블 준비 확인용)

# 우리가 만든 다른 파일들에서 필요한 것들을 가져옵니다.
from config import CONFIG                                   # 설정 꾸러미
from strategy import compute_indicators, generate_signal    # 지표 계산 + 신호 판단
from trader import Trader                                    # 주문/기록 담당
from notifier import TelegramNotifier                        # 텔레그램 담당


# 웹 대시보드와 공유하는 '일시정지' 신호 파일.
#  - 대시보드의 ⏸/▶️ 버튼이 이 파일을 바꾸면, 봇이 매 루프마다 읽어서 따릅니다.
#  - 텔레그램 /pause·/resume 도 이 파일을 갱신해서, 둘이 항상 같은 상태를 봅니다.
CONTROL_FILE = "control.json"


def _read_paused():
    """control.json 을 읽어 '일시정지 중인지'(True/False)를 돌려줍니다. (없으면 False)"""
    try:
        with open(CONTROL_FILE, "r", encoding="utf-8") as fp:
            return bool(json.load(fp).get("paused", False))
    except Exception:
        return False


def _write_paused(value):
    """일시정지 상태를 control.json 에 기록합니다. (웹 대시보드와 공유하기 위해)"""
    try:
        with open(CONTROL_FILE, "w", encoding="utf-8") as fp:
            json.dump({"paused": bool(value)}, fp)
    except Exception:
        pass


def fmt(x):
    """숫자를 '1,234,000원' 처럼 보기 좋게 바꿔주는 도우미."""
    return f"{x:,.0f}원"


def status_text(trader, price, coin=None):
    """한 코인의 현재 성적표(상태)를 사람이 읽기 좋은 글로 만들어 돌려줍니다."""
    st = trader.stats(price)  # 통계 계산
    mode = "모의" if CONFIG["DRY_RUN"] else "실거래"
    head = coin if coin else CONFIG["TICKER"]
    lines = [
        f"📊 [{head}] 상태 ({mode})",
        f"현재가: {fmt(price)}" if price else "현재가: -",
        f"총 매매: {st['trade_count']}회 (매수 {st['buy_count']} / 매도 {st['sell_count']})",
        f"실현손익: {fmt(st['realized_pnl'])} (누적 수익률 {st['roi']:.2f}%)",
        f"승률: {st['win_rate']:.1f}%",
    ]
    if st["holding"] and st["unrealized"]:   # 코인을 들고 있으면 평가손익도 보여줍니다
        u = st["unrealized"]
        lines.append(f"보유중: 평균매수 {fmt(u['entry'])} → 평가 {u['pct']:+.2f}%")
    else:
        lines.append("보유중: 없음 (현금)")
    return "\n".join(lines)  # 여러 줄을 줄바꿈으로 합쳐서 하나의 글로 만듭니다


def status_all(traders, prices):
    """여러 코인의 상태를 한 번에 묶어서 돌려줍니다. (/status·정기요약용)"""
    mode = "모의" if CONFIG["DRY_RUN"] else "실거래"
    blocks = [f"📊 전체 현황 ({mode})"]
    for coin, trader in traders.items():
        blocks.append("──────────")
        blocks.append(status_text(trader, prices.get(coin), coin))
    return "\n".join(blocks)


def main():
    """프로그램의 시작점. 여기서 모든 게 돌아갑니다."""
    cfg = CONFIG
    notifier = TelegramNotifier(cfg["TELEGRAM_BOT_TOKEN"], cfg["TELEGRAM_CHAT_ID"])  # 텔레그램 비서 준비

    # ----- 여러 종목을 한 봇에서 굴리기 -----
    coins = cfg["TICKERS"]                 # 예: ['KRW-BTC', 'KRW-ETH']
    primary = cfg["TICKER"] if cfg["TICKER"] in coins else coins[0]  # 차트는 이 종목 하나만 그림
    traders = {}                           # 코인 -> Trader. 코인별로 상태 파일을 따로 둡니다.
    for coin in coins:
        coin_cfg = dict(cfg)               # 설정을 복사해 종목별로 살짝 바꿔줍니다
        coin_cfg["TICKER"] = coin
        coin_cfg["STATE_FILE"] = f"state_{coin}.json"   # 예: state_KRW-BTC.json
        traders[coin] = Trader(coin_cfg)
    # 봇의 현재 상태를 담는 작은 꾸러미: 일시정지 중인지, 계속 돌지
    flags = {"paused": False, "running": True}

    # ----- 텔레그램 명령이 왔을 때 실행할 함수들을 정의합니다 -----
    def do_status():
        """/status 명령 -> 모든 종목의 현재 성적표를 보냅니다."""
        prices = {}
        for coin, tr in traders.items():
            try:
                prices[coin] = tr.get_price()
            except Exception:
                prices[coin] = None
        notifier.send(status_all(traders, prices))

    def do_pause():
        """/pause 명령 -> 매매를 잠시 멈춥니다. (웹 대시보드와도 상태 공유)"""
        flags["paused"] = True
        _write_paused(True)
        notifier.send("⏸ 매매를 일시정지했어. /resume 으로 재개.")

    def do_resume():
        """/resume 명령 -> 매매를 다시 시작합니다. (웹 대시보드와도 상태 공유)"""
        flags["paused"] = False
        _write_paused(False)
        notifier.send("▶️ 매매를 재개했어.")

    def do_stop():
        """/stop 명령 -> 봇을 종료합니다."""
        flags["running"] = False
        notifier.send("🛑 봇을 종료할게.")

    # 차트 만드는 작업이 '한 번에 하나만' 돌도록 막는 자물쇠 (버튼 빠르게 두 번 눌러도 안전)
    report_lock = threading.Lock()

    def do_report():
        """📊 차트 버튼 / /report 명령 -> 최신 차트를 만들어 텔레그램으로 '사진'으로 보냅니다."""
        # 이미 만드는 중이면 중복으로 또 만들지 않습니다.
        if not report_lock.acquire(blocking=False):
            notifier.send("📊 차트 만드는 중이야. 잠깐만!")
            return
        try:
            notifier.send("📊 차트 만드는 중… (몇 초 걸려)")
            db.init_db(cfg["DB_FILE"])          # 테이블 있는지 확인(없으면 생성 → 안전)
            from report import make_chart        # 무거운 그래프 라이브러리는 '필요할 때만' 불러옴
            chart_cfg = dict(cfg); chart_cfg["TICKER"] = primary   # 차트는 기준 종목 하나만 그림
            path = make_chart(chart_cfg)         # report.png 생성 후 경로 반환(데이터 없으면 None)
            if not path:
                notifier.send("아직 그릴 데이터가 부족해. 잠시 후 다시 눌러줘.")
                return
            prices = {}
            for coin, tr in traders.items():
                try:
                    prices[coin] = tr.get_price()
                except Exception:
                    prices[coin] = None
            # 차트(기준 종목) + 전체 상태 요약(캡션)을 함께 보냅니다.
            notifier.send_photo(path, caption=f"📈 차트: {primary}\n\n" + status_all(traders, prices))
        except Exception as e:
            notifier.send(f"⚠️ 차트 생성 실패: {e}")
        finally:
            report_lock.release()  # 무슨 일이 있어도 자물쇠는 반드시 풉니다

    # 위에서 만든 함수들을 텔레그램 비서에게 '이 명령엔 이 함수 써' 라고 연결합니다.
    notifier.on_status = do_status
    notifier.on_pause = do_pause
    notifier.on_resume = do_resume
    notifier.on_stop = do_stop
    notifier.on_report = do_report
    notifier.start_command_listener()  # 명령 듣기 시작

    # 시작 알림 보내기 (+ 화면 아래 '버튼 키보드' 띄우기)
    mode = "모의(DRY_RUN)" if cfg["DRY_RUN"] else "실거래"
    notifier.send(
        f"🤖 봇 시작! 종목 {', '.join(coins)} / {cfg['INTERVAL']} / 모드 {mode}\n"
        "아래 버튼으로 조작해 — 📊 차트 / 📈 상태 / ⏸ 정지 / ▶️ 재개\n"
        "명령: /status /report /pause /resume /stop",
        with_buttons=True,
    )

    last_summary = time.time()  # 마지막으로 요약 보낸 시각 기록

    # ===== 여기서부터가 '계속 반복되는 매매 루프' 입니다 =====
    while flags["running"]:        # 종료 신호가 오기 전까지 무한 반복
        try:
            flags["paused"] = _read_paused()   # 웹 대시보드/텔레그램이 바꾼 일시정지 상태 반영
            if flags["paused"]:    # 일시정지 상태면 아무것도 안 하고 잠깐 쉽니다
                time.sleep(cfg["LOOP_SECONDS"])
                continue

            # 종목마다 차례로: 데이터 받기 → 지표 → 위험관리 → 신호 매매
            for coin, trader in traders.items():
                df = pyupbit.get_ohlcv(coin, interval=cfg["INTERVAL"], count=200)
                if df is None or len(df) == 0:   # 이 종목 데이터를 못 받았으면 건너뛰고 다음 종목
                    continue
                df = compute_indicators(df, cfg)  # 지표 계산(전략 설정은 모든 종목 공통)

                price = trader.get_price()        # 이 종목 현재가
                if price is None:
                    continue

                # (a) 손절/익절/트레일링 먼저 확인
                trader.track_peak(price)          # 보유 중 최고가 갱신(트레일링용)
                risk = trader.check_risk(price)
                if risk:                          # 위험관리 선에 닿았으면 즉시 매도
                    result, err = trader.sell(price)
                    if result:
                        tag = {"stop_loss": "손절", "take_profit": "익절",
                               "trailing_stop": "트레일링"}.get(risk, "청산")
                        notifier.send(
                            f"🔻 [{coin}] 매도({tag}) @ {fmt(price)} / 손익 {fmt(result['pnl'])}\n\n"
                            + status_text(trader, price, coin)
                        )
                    elif err:                     # 주문이 실제로 실패하면 '성공'인 척하지 않고 알림
                        notifier.send(f"⚠️ [{coin}] 매도 실패(보유 유지): " + err)
                    continue                      # 이 종목은 끝, 다음 종목으로

                # (b) 전략 신호에 따라 매수/매도
                signal, reason = generate_signal(df, trader.state["holding"], cfg)
                if signal == "buy" and not trader.state["holding"]:      # 사라 신호 + 현금
                    result, err = trader.buy(price)
                    if result:
                        notifier.send(
                            f"🟢 [{coin}] 매수 @ {fmt(price)} / {fmt(result['krw'])}\n"
                            f"📌 사유: {reason}\n\n"
                            + status_text(trader, price, coin)
                        )
                    elif err:
                        notifier.send(f"[{coin}] 매수 보류: " + err)
                elif signal == "sell" and trader.state["holding"]:       # 팔아라 신호 + 보유
                    result, err = trader.sell(price)
                    if result:
                        notifier.send(
                            f"🔴 [{coin}] 매도 @ {fmt(price)} / 손익 {fmt(result['pnl'])}\n"
                            f"📌 사유: {reason}\n\n"
                            + status_text(trader, price, coin)
                        )
                    elif err:                     # 주문이 실제로 실패하면 '성공'인 척하지 않고 알림
                        notifier.send(f"⚠️ [{coin}] 매도 실패(보유 유지): " + err)

            # 일정 시간마다 정기 요약(전체 종목) 보내기
            if time.time() - last_summary >= cfg["SUMMARY_EVERY_MIN"] * 60:
                prices = {}
                for coin, tr in traders.items():
                    try:
                        prices[coin] = tr.get_price()
                    except Exception:
                        prices[coin] = None
                notifier.send("⏱ 정기 요약\n" + status_all(traders, prices))
                last_summary = time.time()

        except Exception as e:
            # 무슨 오류가 나도 봇이 죽지 않게 잡아서, 알림만 보내고 계속 진행합니다.
            notifier.send(f"⚠️ 오류(계속 진행): {e}")

        time.sleep(cfg["LOOP_SECONDS"])  # 다음 점검까지 잠깐 쉽니다

    # 반복문을 빠져나오면(=종료 신호) 마무리합니다.
    notifier.send("봇이 정상 종료됐어. 👋")
    notifier.stop()


# 이 파일을 '직접' 실행했을 때만 main()을 돌립니다.
# (다른 파일이 이 파일을 가져다 쓸 땐 자동 실행되지 않게 하는 표준 장치입니다.)
if __name__ == "__main__":
    main()
