# =====================================================================
#  trader.py  ―  실제 '주문 실행'과 '기록 관리'를 담당하는 부분
# ---------------------------------------------------------------------
#  하는 일:
#   - 업비트에 매수/매도 주문을 넣습니다. (단, DRY_RUN=True 면 진짜 주문 대신 '연습'만)
#   - 지금 코인을 들고 있는지, 평균 매수가는 얼마인지 등 '현재 상태'를 기억합니다.
#   - 매매 횟수, 실현 손익(번 돈), 승률 같은 '통계'를 쌓습니다.
#   - 이 모든 걸 state.json 파일에 저장해서, 봇을 껐다 켜도 이어지게 합니다.
# =====================================================================

import json                    # json: 파이썬 데이터를 파일로 저장/읽기 좋은 형식으로 바꿔주는 도구
import os                      # os: 파일이 있는지 확인하는 등 파일 작업용
import time                    # time: 주문 후 잔고가 반영될 때까지 잠깐 기다리기 위한 도구
from datetime import datetime  # 현재 날짜·시간을 기록하기 위한 도구

import pyupbit                 # pyupbit: 업비트와 통신(시세 조회, 주문 등)을 쉽게 해주는 도구

import db                      # 데이터 창고: 매매 기록을 DB(trades 테이블)에도 저장

FEE = 0.0005  # 업비트 매매 수수료(원화마켓 약 0.05%). 손익 계산에 반영합니다.


class Trader:
    """
    'class(클래스)'는 관련된 기능과 데이터를 하나로 묶은 '설계도'입니다.
    이 Trader 는 '매매 담당 직원' 한 명이라고 생각하면 쉽습니다.
    이 직원이 주문도 넣고, 잔고도 보고, 기록도 적습니다.
    """

    def __init__(self, cfg):
        """
        Trader 직원을 처음 만들 때 한 번 실행되는 부분 (초기 세팅).
        cfg = 설정 꾸러미.
        """
        self.cfg = cfg                       # 설정을 보관
        self.ticker = cfg["TICKER"]          # 거래 종목 (예: KRW-BTC)
        self.dry_run = cfg["DRY_RUN"]        # 연습 모드인지 여부
        self.state_file = cfg["STATE_FILE"]  # 상태 저장 파일 이름

        # 연습 모드가 아니라면(=실거래라면), 업비트에 로그인하는 객체를 만듭니다.
        if not self.dry_run:
            self.upbit = pyupbit.Upbit(cfg["UPBIT_ACCESS_KEY"], cfg["UPBIT_SECRET_KEY"])
        else:
            self.upbit = None  # 연습 모드면 실제 로그인은 안 함

        self.state = self._load_state()  # 저장돼 있던 이전 상태를 불러옴 (없으면 새로 시작)

        # 데이터 창고에 매매 기록도 남기기 위한 준비 (DE 파이프라인 연동)
        self.db_file = cfg.get("DB_FILE")
        if self.db_file:
            try:
                db.init_db(self.db_file)   # trades 테이블 없으면 생성
            except Exception:
                self.db_file = None        # 창고를 못 쓰더라도 매매 자체는 그대로 진행

    def _log_trade_db(self, side, price, krw, amount, pnl, at_time=None):
        """매매 한 건을 데이터 창고(trades 테이블)에도 저장. (실패해도 매매엔 영향 없음)
        at_time 을 주면 그 시각으로 기록(백테스트/시뮬용). 실거래는 안 주면 '지금'으로."""
        if not self.db_file:
            return
        try:
            db.save_trade(self.db_file, self.ticker, side, price, krw, amount, pnl,
                          "dry" if self.dry_run else "live", ts=at_time)
        except Exception:
            pass

    # -------------------- 상태 저장 / 불러오기 --------------------
    def _default_state(self):
        """
        상태의 '초기값'을 만들어 돌려줍니다.
        처음 시작하거나 저장 파일이 없을 때 이 값으로 시작합니다.
        """
        return {
            "holding": False,        # 지금 코인을 들고 있나? (처음엔 현금이라 False)
            "entry_price": 0.0,      # 평균 매수가 (얼마에 샀는지)
            "coin_amount": 0.0,      # 보유 코인 수량
            "krw_sim": self.cfg["DRY_RUN_START_KRW"],  # 연습 모드용 '가상 원화 잔고'
            "trade_count": 0,        # 총 거래 횟수 (매수 + 매도)
            "buy_count": 0,          # 매수 횟수
            "sell_count": 0,         # 매도 횟수
            "win_count": 0,          # 이익 보고 판 횟수
            "realized_pnl": 0.0,     # 실현 손익: 실제로 사고팔아 확정된 누적 손익(원)
            "invested": 0.0,         # 지금 들고 있는 코인을 사는 데 들어간 원화
            "trades": [],            # 거래 기록 목록(리스트). 사고팔 때마다 하나씩 추가됨
        }

    def _load_state(self):
        """저장된 state.json 파일이 있으면 읽어오고, 없으면 초기값으로 시작합니다."""
        if os.path.exists(self.state_file):           # 파일이 실제로 있으면
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    s = json.load(f)                  # 파일 내용을 읽어 데이터로 변환
                base = self._default_state()          # 초기값을 기준으로
                base.update(s)                        # 저장돼 있던 값으로 덮어씀(빠진 항목 보정)
                return base
            except Exception:
                pass  # 파일이 깨졌거나 문제가 있으면 무시하고 아래 초기값 사용
        return self._default_state()

    def _save_state(self):
        """현재 상태를 state.json 파일에 저장합니다. (봇을 꺼도 기록이 남도록)"""
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)

    # -------------------- 시세 / 잔고 조회 --------------------
    def get_price(self):
        """현재 시세(코인 1개 가격)를 업비트에서 가져옵니다."""
        return pyupbit.get_current_price(self.ticker)

    def get_krw_balance(self):
        """내가 가진 원화(KRW)가 얼마인지 돌려줍니다."""
        if self.dry_run:
            return self.state["krw_sim"]      # 연습 모드면 가상 잔고
        bal = self.upbit.get_balance("KRW")   # 실거래면 진짜 잔고 조회
        return float(bal) if bal else 0.0

    def get_coin_balance(self):
        """실거래에서 '지금 실제로 들고 있는 코인 수량'(주문가능 수량)을 업비트에서 조회합니다.
        매도할 때는 우리가 추정한 값이 아니라 '진짜 잔고'를 팔아야 거절당하지 않습니다."""
        bal = self.upbit.get_balance(self.ticker)  # 예: KRW-BTC -> BTC 잔고
        return float(bal) if bal else 0.0

    # -------------------- 주문 응답 점검(성공/실패) --------------------
    @staticmethod
    def _order_failed(resp):
        """업비트 주문 응답이 '실패'인지 알려줍니다(True=실패).
        pyupbit 는 주문이 실패해도 예외를 던지지 않고
          - None (네트워크/인증 문제) 또는
          - {'error': {...}} (업비트가 거절: 잔고부족·최소금액 미달 등)
        을 돌려줍니다. 그래서 반드시 직접 확인해야 합니다."""
        return resp is None or (isinstance(resp, dict) and "error" in resp)

    @staticmethod
    def _order_error_msg(resp):
        """실패한 주문 응답에서 사람이 읽을 수 있는 사유를 뽑아냅니다."""
        if isinstance(resp, dict) and isinstance(resp.get("error"), dict):
            e = resp["error"]
            return e.get("message") or e.get("name") or "알 수 없는 오류"
        return "응답 없음(네트워크/API 키/권한 확인 필요)"

    # -------------------- 매수 (사기) --------------------
    def buy(self, price, at_time=None):
        """
        코인을 삽니다. price = 현재 가격.
        at_time: 기록할 시각(백테스트/시뮬용). 안 주면 '지금'.
        돌려주는 값: (결과정보, 오류메시지) ― 성공하면 오류는 None 입니다.
        """
        cfg = self.cfg
        krw = self.get_krw_balance()  # 지금 가진 원화

        # 얼마어치 살지 정합니다: 비율 모드면 (가진 돈 × 비율), 아니면 정해진 금액.
        spend = krw * cfg["ORDER_RATIO"] if cfg["USE_RATIO"] else min(cfg["ORDER_KRW"], krw)

        # 업비트 최소 주문 금액은 5,000원입니다. 그보다 적으면 사지 않습니다.
        if spend < 5000:
            return None, "매수 가능 원화 부족(최소 5,000원)"

        if self.dry_run:
            # 연습 모드: 실제 주문 없이 '샀다고 가정'만 합니다.
            amount = (spend * (1 - FEE)) / price  # 수수료 떼고 살 수 있는 코인 수량
            self.state["krw_sim"] -= spend        # 가상 원화에서 쓴 만큼 뺍니다
        else:
            # 실거래: 업비트에 '시장가 매수' 주문을 넣습니다.
            before = self.get_coin_balance()                       # 주문 전 실제 보유 코인
            resp = self.upbit.buy_market_order(self.ticker, spend)  # 주문 결과를 '받아서'
            if self._order_failed(resp):                           # 실패면 상태를 건드리지 않고 끝냅니다
                return None, "업비트 매수 주문 실패: " + self._order_error_msg(resp)
            time.sleep(1.0)                                        # 체결·잔고 반영을 잠깐 기다림
            after = self.get_coin_balance()                        # 주문 후 실제 보유 코인
            amount = max(after - before, 0.0)                      # 이번에 '실제로' 산 수량
            if amount <= 0:                                        # 잔고 반영이 느릴 때만 근사치로 대체
                amount = (spend * (1 - FEE)) / price

        # 평균 매수가 다시 계산 (여러 번 나눠 살 수도 있으니 평균을 냅니다)
        new_amt = self.state["coin_amount"] + amount    # 새 보유 수량
        new_cost = self.state["invested"] + spend       # 새 투입 원화
        self.state["coin_amount"] = new_amt
        self.state["invested"] = new_cost
        self.state["entry_price"] = new_cost / new_amt if new_amt > 0 else 0.0
        self.state["holding"] = True            # 이제 코인을 들고 있는 상태
        self.state["buy_count"] += 1            # 매수 횟수 +1
        self.state["trade_count"] += 1          # 총 거래 +1
        # 거래 기록에 한 줄 추가
        self.state["trades"].append({
            "time": datetime.now().isoformat(timespec="seconds"),  # 거래 시각
            "side": "buy", "price": price, "krw": spend, "amount": amount,
        })
        self._save_state()  # 파일에 저장
        self._log_trade_db("buy", price, spend, amount, None, at_time=at_time)   # 데이터 창고에도 기록
        return {"side": "buy", "price": price, "krw": spend, "amount": amount}, None

    # -------------------- 매도 (팔기) --------------------
    def sell(self, price, at_time=None):
        """
        들고 있는 코인을 전부 팝니다. price = 현재 가격.
        at_time: 기록할 시각(백테스트/시뮬용). 안 주면 '지금'.
        돌려주는 값: (결과정보, 오류메시지).
        """
        # 들고 있는 코인이 없으면 팔 수 없습니다.
        if not self.state["holding"] or self.state["coin_amount"] <= 0:
            return None, "보유 수량 없음"

        if self.dry_run:
            amount = self.state["coin_amount"]     # 연습 모드: 우리가 기록해 둔 보유 수량
            proceeds = price * amount * (1 - FEE)  # 팔아서 받는 원화(수수료 제외)
            self.state["krw_sim"] += proceeds      # 가상 원화에 더합니다
        else:
            # 실거래: '우리 추정치'가 아니라 업비트에 실제로 있는 잔고를 팝니다.
            #  (추정치는 실제 체결량보다 조금 클 수 있어 '수량 부족'으로 거절당합니다.)
            amount = self.get_coin_balance()       # 진짜 보유 수량(주문가능)
            amount = int(amount * 1e8) / 1e8       # 소수점 8자리 아래는 버림(보유량 초과 방지)
            if amount <= 0:                        # 실제로 들고 있는 게 없으면 매도 불가
                return None, "업비트 보유 수량이 없어 매도할 수 없음(잔고 0)"
            resp = self.upbit.sell_market_order(self.ticker, amount)  # 주문 결과를 '받아서'
            if self._order_failed(resp):                              # 실패면 포지션을 그대로 두고 끝냅니다
                return None, "업비트 매도 주문 실패: " + self._order_error_msg(resp)
            proceeds = price * amount * (1 - FEE)

        cost = self.state["invested"]   # 이 코인을 사는 데 들어갔던 돈
        pnl = proceeds - cost           # 이번 매매 손익 = 받은 돈 - 들인 돈
        self.state["realized_pnl"] += pnl          # 누적 손익에 더하기
        if pnl > 0:
            self.state["win_count"] += 1           # 이익이면 '이긴 횟수' +1
        self.state["sell_count"] += 1
        self.state["trade_count"] += 1
        self.state["trades"].append({
            "time": datetime.now().isoformat(timespec="seconds"),
            "side": "sell", "price": price, "krw": proceeds, "amount": amount, "pnl": pnl,
        })
        # 다 팔았으니 포지션 초기화 (다시 현금 상태)
        self.state["holding"] = False
        self.state["coin_amount"] = 0.0
        self.state["invested"] = 0.0
        self.state["entry_price"] = 0.0
        self._save_state()
        self._log_trade_db("sell", price, proceeds, amount, pnl, at_time=at_time)  # 데이터 창고에도 기록
        return {"side": "sell", "price": price, "krw": proceeds, "amount": amount, "pnl": pnl}, None

    # -------------------- 손절 / 익절 확인 --------------------
    def check_risk(self, price):
        """
        들고 있는 동안, 미리 정한 손절/익절 선에 닿았는지 확인합니다.
        - 손실이 STOP_LOSS_PCT 이하  -> "stop_loss"   (손절하고 팔아라)
        - 이익이 TAKE_PROFIT_PCT 이상 -> "take_profit" (익절하고 팔아라)
        - 둘 다 아니면 None (아직 보유)
        """
        if not self.state["holding"] or self.state["entry_price"] <= 0:
            return None
        # 산 가격 대비 현재 몇 % 인지 계산
        change_pct = (price - self.state["entry_price"]) / self.state["entry_price"] * 100
        if self.cfg["STOP_LOSS_PCT"] < 0 and change_pct <= self.cfg["STOP_LOSS_PCT"]:
            return "stop_loss"
        if self.cfg["TAKE_PROFIT_PCT"] > 0 and change_pct >= self.cfg["TAKE_PROFIT_PCT"]:
            return "take_profit"
        return None

    # -------------------- 통계(성적표) 만들기 --------------------
    def stats(self, price=None):
        """
        지금까지의 성적표를 계산해서 돌려줍니다.
        매매 횟수, 실현 손익, 누적 수익률, 승률, 보유 중이면 평가손익 등.
        price 를 주면 '지금 들고 있는 코인의 평가손익'도 같이 계산합니다.
        """
        s = self.state
        # 지금까지 매수에 쓴 총 원화 (수익률 계산의 기준)
        invested_total = sum(t["krw"] for t in s["trades"] if t["side"] == "buy")
        # 누적 실현 수익률(%) = 번 돈 ÷ 투입한 돈
        roi = (s["realized_pnl"] / invested_total * 100) if invested_total > 0 else 0.0
        # 승률(%) = 이익 본 매도 ÷ 전체 매도
        win_rate = (s["win_count"] / s["sell_count"] * 100) if s["sell_count"] > 0 else 0.0

        unrealized = None  # 아직 안 팔아서 확정 안 된 '평가손익'
        if s["holding"] and price and s["entry_price"] > 0:
            unrealized = {
                "entry": s["entry_price"],  # 평균 매수가
                "now": price,               # 현재가
                "pct": (price - s["entry_price"]) / s["entry_price"] * 100,  # 몇 % 인지
                "amount": s["coin_amount"],
            }
        return {
            "trade_count": s["trade_count"],
            "buy_count": s["buy_count"],
            "sell_count": s["sell_count"],
            "realized_pnl": s["realized_pnl"],
            "roi": roi,
            "win_rate": win_rate,
            "holding": s["holding"],
            "unrealized": unrealized,
        }
