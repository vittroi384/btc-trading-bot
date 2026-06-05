# =====================================================================
#  config.py  ―  봇이 사용할 '설정값'을 한곳에서 읽어오는 파일
# ---------------------------------------------------------------------
#  - 실제 비밀값(API 키, 텔레그램 토큰 등)은 같은 폴더의 ".env" 파일에 적습니다.
#  - 이 파일은 그 .env 를 읽어, 프로그램이 쓰기 좋은 형태(CONFIG)로 정리해 줍니다.
#  - 즉, 값을 바꾸고 싶으면 이 파일이 아니라 ".env" 파일을 수정하면 됩니다.
# =====================================================================

import os                         # os: 컴퓨터의 '환경변수' 같은 시스템 정보를 읽는 도구
from dotenv import load_dotenv    # .env 파일 내용을 환경변수처럼 불러와 주는 도구

# 같은 폴더에 있는 .env 파일을 읽어들입니다. (.env 가 없으면 그냥 넘어갑니다)
load_dotenv()


# ---------------------------------------------------------------------
#  아래 3개는 '도우미 함수'입니다.
#  .env 에 적힌 값은 전부 글자(문자열)로 읽히는데, 숫자나 참/거짓이 필요할 때가
#  있어서 그 형태로 바꿔주는 역할을 합니다. (함수 = 작은 기능 묶음)
# ---------------------------------------------------------------------

def _get_bool(key, default="false"):
    """
    .env 에서 key 값을 읽어 '참/거짓(True/False)'으로 돌려줍니다.
    예: DRY_RUN=true  ->  True
    'true','1','yes','on' 중 하나면 참(True)으로 봅니다.
    """
    return os.getenv(key, default).strip().lower() in ("1", "true", "yes", "on")


def _get_float(key, default):
    """.env 에서 key 값을 읽어 '소수점 숫자'로 돌려줍니다. 예: '0.3' -> 0.3"""
    v = os.getenv(key)                       # 값을 읽음 (없으면 None)
    return float(v) if v not in (None, "") else float(default)  # 비어있으면 기본값 사용


def _get_int(key, default):
    """.env 에서 key 값을 읽어 '정수(소수점 없는 숫자)'로 돌려줍니다. 예: '60' -> 60"""
    v = os.getenv(key)
    return int(v) if v not in (None, "") else int(default)


# ---------------------------------------------------------------------
#  CONFIG: 봇이 사용할 모든 설정을 모아둔 '설정 꾸러미'입니다.
#  이런 "이름: 값" 형태의 꾸러미를 파이썬에서는 '딕셔너리'라고 부릅니다.
#  다른 파일들은 여기서  CONFIG["TICKER"]  처럼 필요한 값을 꺼내 씁니다.
# ---------------------------------------------------------------------
CONFIG = {
    # ----- 업비트 Open API 키 (거래소에 주문을 넣기 위한 '열쇠') -----
    "UPBIT_ACCESS_KEY": os.getenv("UPBIT_ACCESS_KEY", ""),  # 공개 키
    "UPBIT_SECRET_KEY": os.getenv("UPBIT_SECRET_KEY", ""),  # 비밀 키

    # ----- 텔레그램 (알림을 보낼 통로) -----
    "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN", ""),  # 텔레그램 봇 토큰
    "TELEGRAM_CHAT_ID": os.getenv("TELEGRAM_CHAT_ID", ""),      # 알림 받을 '내 채팅 번호'

    # ----- 거래 기본 설정 -----
    "TICKER": os.getenv("TICKER", "KRW-BTC"),       # 무엇을 거래할지. KRW-BTC = 원화로 비트코인
    "INTERVAL": os.getenv("INTERVAL", "minute60"),  # 캔들(봉) 하나의 시간 단위. minute60 = 1시간봉
    "LOOP_SECONDS": _get_int("LOOP_SECONDS", 60),   # 몇 초마다 시세를 다시 확인할지 (60 = 1분마다)
    "ORDER_KRW": _get_float("ORDER_KRW", 10000),    # 한 번 매수할 때 쓸 금액(원)
    "USE_RATIO": _get_bool("USE_RATIO", "true"),    # 금액 대신 '비율'로 살지 여부 (True/False)
    "ORDER_RATIO": _get_float("ORDER_RATIO", 0.3),  # 비율 매수일 때, 가진 원화의 몇 %를 쓸지 (0.3 = 30%)

    # ----- 리스크(위험) 관리. 값이 0이면 그 기능을 끕니다 -----
    "STOP_LOSS_PCT": _get_float("STOP_LOSS_PCT", -5.0),     # 손절: 산 가격보다 -5% 떨어지면 자동 매도
    "TAKE_PROFIT_PCT": _get_float("TAKE_PROFIT_PCT", 8.0),  # 익절: 산 가격보다 +8% 오르면 자동 매도

    # ----- 전략에 쓰이는 숫자들 (strategy.py 에서 사용) -----
    "MA_SHORT": _get_int("MA_SHORT", 5),       # 단기 이동평균선 기간 (예: 최근 5봉 평균)
    "MA_LONG": _get_int("MA_LONG", 20),        # 장기 이동평균선 기간 (예: 최근 20봉 평균)
    "RSI_PERIOD": _get_int("RSI_PERIOD", 9),   # RSI 계산 기간 (짧을수록 더 자주 신호. 9 = 민감)
    "RSI_BUY_DIP": _get_float("RSI_BUY_DIP", 38),     # RSI가 이 값 이하로 떨어지면 '과매도'로 보고 매수 신호
    "RSI_BUY_BELOW": _get_float("RSI_BUY_BELOW", 78), # 안전장치: RSI가 이 값 이상으로 과열이면 매수 안 함
    "RSI_SELL_ABOVE": _get_float("RSI_SELL_ABOVE", 62),  # RSI가 이 값 이상이면 매도 신호
    "BB_PERIOD": _get_int("BB_PERIOD", 20),    # 볼린저밴드 기간
    "BB_STD": _get_float("BB_STD", 2.0),       # 볼린저밴드 폭(표준편차 배수)

    # ----- 안전장치 -----
    # ★★★ 가장 중요! DRY_RUN 이 True 면 '연습 모드'라서 진짜 주문이 나가지 않습니다. ★★★
    #     처음엔 무조건 true 로 두고 충분히 확인한 뒤, 믿을 만하면 false 로 바꾸세요.
    "DRY_RUN": _get_bool("DRY_RUN", "true"),
    "SUMMARY_EVERY_MIN": _get_int("SUMMARY_EVERY_MIN", 360),  # 몇 분마다 '정기 요약'을 보낼지 (360 = 6시간)
    "STATE_FILE": os.getenv("STATE_FILE", "state.json"),      # 통계/상태를 저장할 파일 이름
    "DRY_RUN_START_KRW": _get_float("DRY_RUN_START_KRW", 1000000),  # 연습 모드에서 가정할 시작 원화(100만원)

    # ----- 데이터 파이프라인(DE) 설정 -----
    "DB_FILE": os.getenv("DB_FILE", "market.db"),                    # SQLite '데이터 창고' 파일 이름
    "PIPELINE_LOOP_SECONDS": _get_int("PIPELINE_LOOP_SECONDS", 60),  # 파이프라인 실행 주기(초)
    "CANDLE_COUNT": _get_int("CANDLE_COUNT", 200),                   # 한 번에 가져올 캔들 개수
}
