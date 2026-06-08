# =====================================================================
#  dashboard.py  ―  브라우저로 보는 웹 대시보드 (현황/차트 보기 + 일시정지·재개)
# ---------------------------------------------------------------------
#  실행:  python dashboard.py   (보통은 systemd 서비스로 24시간 띄움)
#  접속:  브라우저에서  http://<서버IP>/  → 아이디/비번 입력 후 현황 확인.
#
#  - 보여주는 것: 현재가 / 실현손익·수익률 / 승률 / 매매횟수 / 보유상태 /
#                가격·지표·매매 차트(report.png) / 최근 매매 기록
#  - 조작: ⏸ 일시정지 / ▶️ 재개  (control.json 으로 봇과 신호를 주고받음)
#  - 보안: 기본 인증(아이디/비번)으로 잠가둠. .env 의 DASH_USER / DASH_PASS 사용.
#          (HTTP라 통신 자체는 암호화 안 되니, 비번을 반드시 설정해야 시작됩니다.)
#  - 계정 2종:
#      관리자(DASH_USER/DASH_PASS)      = 모든 기능 (현황·DB + 일시정지/재개·설정 변경)
#      읽기 전용(DASH_VIEW_USER/PASS)   = '현황'·'DB' 보기만. 조작 버튼은 안 보이고 막힘. (선택)
# =====================================================================

import json
import math
import os
import threading
import time
from functools import wraps

from flask import Flask, request, Response, redirect, send_file, jsonify, g
from waitress import serve
from dotenv import load_dotenv

load_dotenv()                  # .env 읽기 (DASH_USER / DASH_PASS / DASH_PORT 등)
from config import CONFIG      # 거래 설정(TICKER, STATE_FILE, DB_FILE ...)

CONTROL_FILE = "control.json"  # bot.py 와 공유하는 일시정지/재개·설정 신호 파일
REPORT_PNG = "report.png"      # 차트 이미지 파일

# 버튼으로 바꿀 수 있는 설정 (값, 화면에 보일 라벨). bot.py 의 허용목록과 맞춰둠.
SELL_OPTS = [("legacy", "빠른청산"), ("trend", "추세추종"), ("meanrev", "되돌림"), ("split", "이유별")]
BUY_OPTS = [("all", "전체"), ("dip_only", "눌림목만")]
INTERVAL_OPTS = [("minute60", "1시간"), ("minute240", "4시간")]
INTERVAL_LABEL = {"minute60": "1시간봉", "minute240": "4시간봉"}
# (control.json 키, CONFIG 키, 허용값들)
SETTING_MAP = {
    "sell_mode": ("SELL_MODE", [v for v, _ in SELL_OPTS]),
    "buy_mode": ("BUY_MODE", [v for v, _ in BUY_OPTS]),
    "interval": ("INTERVAL", [v for v, _ in INTERVAL_OPTS]),
}

# 장세(레짐) 프리셋: 버튼 하나로 매수·매도 조합을 한 번에 설정.
#  (key, 화면 라벨, {바꿀 설정들}, 한 줄 설명)
REGIME_PRESETS = [
    ("up",    "📈 상승장", {"buy_mode": "all",      "sell_mode": "trend"},  "올라타서 끝까지"),
    ("range", "↔️ 횡보장", {"buy_mode": "all",      "sell_mode": "legacy"}, "싸면 사서 오르면 바로"),
    ("down",  "📉 하락장", {"buy_mode": "dip_only", "sell_mode": "legacy"}, "싼 것만 조금, 거의 현금"),
]

DASH_USER = os.getenv("DASH_USER", "")
DASH_PASS = os.getenv("DASH_PASS", "")
# 읽기 전용(뷰어) 계정: '현황'과 'DB' 보기만 가능. 일시정지/재개·설정 변경은 불가. (안 넣으면 비활성)
DASH_VIEW_USER = os.getenv("DASH_VIEW_USER", "")
DASH_VIEW_PASS = os.getenv("DASH_VIEW_PASS", "")
DASH_PORT = int(os.getenv("DASH_PORT", "80"))

app = Flask(__name__)
_chart_lock = threading.Lock()  # 차트 생성을 한 번에 하나만 (matplotlib 동시 사용 방지)

# ---------- 색상 (report.py 차트와 통일된 다크 테마) ----------
COL = {
    "bg": "#0f1320", "panel": "#141a2b", "card": "#192032", "edge": "#283250",
    "price": "#3ec6ff", "green": "#22d39a", "red": "#ff5470",
    "text": "#e7ebf5", "muted": "#8b93a7",
}


# ---------- 비밀번호(HTTP 기본 인증) + 권한(관리자 / 읽기 전용) ----------
def _role(u, p):
    """아이디/비번을 확인해 '권한'을 돌려줍니다.
       'admin'  = 관리자 (모든 조작 가능)
       'viewer' = 읽기 전용 (현황·DB 보기만, 조작 불가)
       None     = 로그인 실패
    관리자와 읽기전용 비번이 같으면 관리자로 인정합니다(관리자 우선)."""
    if DASH_USER and DASH_PASS and u == DASH_USER and p == DASH_PASS:
        return "admin"
    if DASH_VIEW_USER and DASH_VIEW_PASS and u == DASH_VIEW_USER and p == DASH_VIEW_PASS:
        return "viewer"
    return None


def _auth_role():
    """지금 요청의 로그인 정보를 보고 권한을 돌려줍니다. (로그인 안 했으면 None)"""
    a = request.authorization
    return _role(a.username, a.password) if a else None


def _need_login():
    """로그인 창을 띄우는 401 응답."""
    return Response("로그인이 필요합니다.", 401,
                    {"WWW-Authenticate": 'Basic realm="btc-trading-bot"'})


def require_auth(fn):
    """로그인(관리자 또는 읽기 전용)만 통과하면 볼 수 있는 화면.
    통과하면 g.role 에 권한('admin'/'viewer')을 담아 둡니다(화면에서 버튼 표시 여부에 사용)."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        role = _auth_role()
        if not role:
            return _need_login()
        g.role = role
        return fn(*args, **kwargs)
    return wrapper


def require_admin(fn):
    """'조작'(일시정지/재개·장세·설정 변경)은 관리자만. 읽기 전용 계정은 403으로 막습니다."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        role = _auth_role()
        if not role:
            return _need_login()
        if role != "admin":
            return Response("권한이 없습니다. 읽기 전용 계정은 조작할 수 없어요.", 403)
        g.role = role
        return fn(*args, **kwargs)
    return wrapper


# ---------- 데이터 읽기 ----------
def read_state():
    """봇이 저장한 state.json(보유상태·손익·매매기록)을 읽어옵니다. (없으면 빈 값)"""
    try:
        with open(CONFIG["STATE_FILE"], "r", encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return {}


def read_control():
    """control.json 전체를 딕셔너리로 읽어옵니다. (없거나 깨졌으면 빈 딕셔너리)"""
    try:
        with open(CONTROL_FILE, "r", encoding="utf-8") as fp:
            d = json.load(fp)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def write_control(updates):
    """control.json 의 '일부 키만' 갱신해 저장합니다(merge). 다른 키는 보존.
    임시파일에 쓴 뒤 교체해서, 봇이 읽는 도중에도 깨진 파일을 보지 않게 합니다."""
    d = read_control()
    d.update(updates)
    tmp = CONTROL_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(d, fp, ensure_ascii=False)
    os.replace(tmp, CONTROL_FILE)


def read_paused():
    """control.json 을 읽어 '일시정지 중인지' 돌려줍니다. (없으면 False)"""
    return bool(read_control().get("paused", False))


def write_paused(value):
    """일시정지/재개 상태만 갱신합니다. (모드·봉 설정은 보존)"""
    write_control({"paused": bool(value)})


def effective(ckey):
    """지금 '실제로 적용 중인' 설정값을 돌려줍니다.
    control.json 에 (대시보드로 바꾼) 값이 있으면 그걸, 없으면 .env(CONFIG) 기본값을."""
    cfg_key, valid = SETTING_MAP[ckey]
    v = read_control().get(ckey)
    if isinstance(v, str) and v in valid:
        return v
    return CONFIG.get(cfg_key)


def current_price(ticker):
    """업비트에서 해당 종목의 현재가를 가져옵니다. (공개 시세라 키 불필요)"""
    try:
        import pyupbit
        return pyupbit.get_current_price(ticker)
    except Exception:
        return None


def coins_list():
    """대시보드가 보여줄 종목 목록. (봇과 동일하게 TICKERS 사용)"""
    return CONFIG.get("TICKERS") or [CONFIG["TICKER"]]


def read_state_for(coin):
    """해당 종목의 상태파일(state_<코인>.json)을 읽어옵니다. (없으면 빈 값)"""
    try:
        with open(f"state_{coin}.json", "r", encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return {}


def compute_stats(st, price):
    """state 로부터 누적 수익률·승률·평가손익(%)·평가손익(원)을 계산합니다. (trader.stats 와 동일 공식)
       roi         : 누적 실현 수익률(%)
       win_rate    : 승률(%)
       unreal      : 평가손익률(%)   ― (현재가 - 평균매수가) / 평균매수가 × 100
       unreal_krw  : 평가손익 금액(원) ― (현재가 - 평균매수가) × 보유수량
                     = '지금 현재가로 팔면' 늘어나거나(+) 줄어드는(-) 원화. 화면의 %와 같은 기준."""
    trades = st.get("trades", [])
    invested = sum(t.get("krw", 0) for t in trades if t.get("side") == "buy")
    roi = (st.get("realized_pnl", 0.0) / invested * 100) if invested > 0 else 0.0
    sells = st.get("sell_count", 0)
    win_rate = (st.get("win_count", 0) / sells * 100) if sells > 0 else 0.0
    unreal = unreal_krw = None
    if st.get("holding") and price and st.get("entry_price", 0) > 0:
        entry = st["entry_price"]
        unreal = (price - entry) / entry * 100
        unreal_krw = (price - entry) * st.get("coin_amount", 0.0)
    return roi, win_rate, unreal, unreal_krw


def ensure_chart():
    """차트(report.png)가 없거나 60초보다 오래됐으면 새로 만든다. (한 번에 하나만)"""
    try:
        if os.path.exists(REPORT_PNG) and (time.time() - os.path.getmtime(REPORT_PNG) < 60):
            return
        with _chart_lock:
            # 자물쇠를 잡는 사이 다른 요청이 이미 만들었을 수 있으니 다시 확인
            if os.path.exists(REPORT_PNG) and (time.time() - os.path.getmtime(REPORT_PNG) < 60):
                return
            import db
            from report import make_chart
            db.init_db(CONFIG["DB_FILE"])
            make_chart(CONFIG, REPORT_PNG)
    except Exception as e:
        print("차트 생성 실패:", e)


# ---------- 화면(HTML) 만들기 ----------
def _won(x):
    """숫자를 '1,234,000' 처럼 콤마 찍어 돌려줍니다."""
    try:
        return f"{float(x):,.0f}"
    except Exception:
        return "-"


def _nav(active):
    """상단 탭(현황 / DB) 만들기."""
    def tab(href, label, key):
        return f'<a href="{href}" class="{"active" if key == active else ""}">{label}</a>'
    return '<div class="nav">' + tab("/", "📊 현황", "home") + tab("/db", "🗄 DB", "db") + "</div>"


def _num(x, dec=0):
    """숫자를 콤마 + 소수자리수로. NaN/None 은 '-'."""
    try:
        if x is None:
            return "-"
        xf = float(x)
        if xf != xf:   # NaN 검사
            return "-"
        return f"{xf:,.{dec}f}"
    except Exception:
        return "-"


def _table_html(headers, rows):
    """헤더/행 목록으로 HTML 표를 만든다. (행 없으면 '데이터 없음')"""
    if not rows:
        return '<div class="empty">데이터 없음</div>'
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="tbl-wrap"><table class="db"><tr>{head}</tr>{body}</table></div>'


def _mode_buttons(field, options, active):
    """설정 버튼 한 줄을 만든다(각 버튼 = 작은 form). 지금 적용 중인 값이면 강조 표시.
    버튼은 .ctrl 이 아닌 .modes 안에 있어 일반 POST→새로고침으로 동작(활성표시 즉시 갱신)."""
    out = ""
    for val, label in options:
        cls = "mbtn active" if val == active else "mbtn"
        out += (f'<form method="post" action="/set/{field}/{val}">'
                f'<button class="{cls}">{label}</button></form>')
    return out


def _regime_buttons(eff_buy, eff_sell):
    """장세 버튼 한 줄. 지금 매수·매도 조합과 일치하는 장세를 강조."""
    out = ""
    for key, label, s, _desc in REGIME_PRESETS:
        active = (s["buy_mode"] == eff_buy and s["sell_mode"] == eff_sell)
        cls = "mbtn active" if active else "mbtn"
        out += (f'<form method="post" action="/preset/{key}">'
                f'<button class="{cls}">{label}</button></form>')
    return out


def _current_regime_name(eff_buy, eff_sell):
    """지금 설정이 어느 장세 프리셋과 같은지 라벨을 돌려준다. 없으면 None."""
    for _key, label, s, _desc in REGIME_PRESETS:
        if s["buy_mode"] == eff_buy and s["sell_mode"] == eff_sell:
            return label
    return None


CSS = """
<style>
  * { box-sizing: border-box; }
  body { margin:0; background:#0f1320; color:#e7ebf5;
         font-family: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace; }
  .wrap { max-width:960px; margin:0 auto; padding:22px 16px 60px; }
  .top { display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:18px; }
  .top h1 { font-size:18px; letter-spacing:1.5px; margin:0; }
  .badge { font-size:12px; padding:3px 10px; border-radius:999px; border:1px solid #283250; }
  .b-live { color:#ff5470; border-color:#ff5470; }
  .b-dry  { color:#8b93a7; }
  .b-run  { color:#22d39a; border-color:#22d39a; }
  .b-pause{ color:#ffb454; border-color:#ffb454; }
  .upd { margin-left:auto; color:#8b93a7; font-size:12px; }
  .grid { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin-bottom:14px; }
  .card { background:#192032; border:1px solid #283250; border-radius:12px; padding:14px 16px; }
  .card .k { color:#8b93a7; font-size:12px; margin-bottom:8px; }
  .card .v { font-size:20px; font-weight:700; }
  .hold { background:#141a2b; border:1px solid #283250; border-radius:12px;
          padding:14px 16px; margin-bottom:14px; }
  .ctrl { display:flex; gap:10px; margin-bottom:18px; }
  .ctrl form { flex:1; margin:0; }
  .ctrl button { width:100%; padding:13px; border-radius:12px; border:1px solid #283250;
                 background:#192032; color:#e7ebf5; font-size:15px; font-family:inherit; cursor:pointer; }
  .ctrl button.on-pause { border-color:#ffb454; color:#ffb454; }
  .ctrl button.on-resume{ border-color:#22d39a; color:#22d39a; }
  .ctrl button:disabled { opacity:0.35; cursor:default; }
  .modes { display:flex; flex-direction:column; gap:8px; margin-bottom:18px; }
  .mrow { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
  .mrow .mlabel { width:44px; flex:0 0 auto; color:#8b93a7; font-size:13px; }
  .mrow form { flex:1 1 0; min-width:84px; margin:0; }
  .mbtn { width:100%; padding:9px 6px; border-radius:10px; border:1px solid #283250;
          background:#192032; color:#8b93a7; font-size:13px; font-family:inherit; cursor:pointer; }
  .mbtn:hover { border-color:#3a4560; }
  .mbtn.active { color:#e7ebf5; border-color:#3ec6ff; background:#1b2942; font-weight:700; }
  .adv { margin:2px 0 14px; }
  .adv > summary { color:#8b93a7; font-size:13px; cursor:pointer; padding:6px 0; list-style:none; }
  .adv > summary::before { content:"▸ "; }
  .adv[open] > summary::before { content:"▾ "; }
  .adv > summary:hover { color:#e7ebf5; }
  img.chart { width:100%; border-radius:12px; border:1px solid #283250; display:block; margin-bottom:18px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th,td { text-align:right; padding:8px 10px; border-bottom:1px solid #222a40; }
  th:first-child, td:first-child { text-align:left; }
  th { color:#8b93a7; font-weight:600; }
  .green { color:#22d39a; } .red { color:#ff5470; } .muted { color:#8b93a7; }
  .sub { font-size:11px; margin-top:2px; opacity:.92; }   /* 평가손익 % 아래 '원' 금액 줄 */
  .b-view { color:#3ec6ff; border-color:#3ec6ff; }        /* 읽기 전용 계정 배지 */
  .empty { color:#8b93a7; padding:18px 4px; }
  .nav { display:flex; gap:8px; margin-bottom:16px; }
  .nav a { padding:8px 16px; border-radius:10px; border:1px solid #283250; color:#8b93a7;
           text-decoration:none; font-size:14px; }
  .nav a.active { color:#e7ebf5; border-color:#3ec6ff; }
  h2.sec { font-size:14px; color:#8b93a7; margin:22px 0 8px; font-weight:600; letter-spacing:.5px; }
  .tbl-wrap { overflow-x:auto; border:1px solid #283250; border-radius:12px; }
  table.db { font-size:12.5px; }
  table.db th { position:sticky; top:0; background:#141a2b; }
  @media (max-width:640px){ .grid{ grid-template-columns:repeat(2,1fr); } }
</style>
"""


# ---------- 화면 자동 갱신 스크립트 (페이지 새로고침 없이 '데이터만' 바꿔 깜빡임 제거) ----------
#  - 5초마다 /api/state 에서 숫자·상태·매매기록만 받아 화면을 갱신합니다.
#  - 차트(이미지)는 60초마다, '다 받은 뒤에만' 바꿔치기해서 깜빡이지 않습니다.
#  - ⏸/▶️ 버튼도 새로고침 없이 동작합니다.
DASH_JS = """
<script>
(function(){
  function fmt(x, dec){
    dec = dec || 0;
    if (x === null || x === undefined || (typeof x === 'number' && !isFinite(x))) return '-';
    return Number(x).toLocaleString('ko-KR', {minimumFractionDigits:dec, maximumFractionDigits:dec});
  }
  function signed(x, dec){
    if (x === null || x === undefined || (typeof x === 'number' && !isFinite(x))) return '-';
    return (x >= 0 ? '+' : '') + fmt(x, dec);
  }
  function setText(id, t){ var el=document.getElementById(id); if(el) el.textContent=t; }

  function applyState(s){
    setText('upd-time', s.updated);

    var badge=document.getElementById('badge-state');
    if(badge){
      badge.className='badge '+(s.paused?'b-pause':'b-run');
      badge.textContent=s.paused?'⏸ 일시정지':'▶️ 매매중';
    }
    var bp=document.getElementById('btn-pause'); if(bp) bp.disabled=!!s.paused;
    var br=document.getElementById('btn-resume'); if(br) br.disabled=!s.paused;

    // 종목별 현황 표
    var cEl=document.getElementById('coins');
    if(cEl){
      var coins = s.coins || [];
      if(coins.length){
        var rows='';
        for(var i=0;i<coins.length;i++){
          var c=coins[i];
          var price = c.price ? fmt(c.price)+'원' : '-';
          var hold = (c.holding && c.entry_price>0) ? ('평균 '+fmt(c.entry_price)) : '<span class="muted">현금</span>';
          var unreal = '<span class="muted">-</span>';
          if(c.holding && c.unreal!==null && c.unreal!==undefined){
            var uc = (c.unreal>=0?'green':'red');
            var pctTxt = '<span class="'+uc+'">'+signed(c.unreal,2)+'%</span>';
            // 평가손익 금액(원): 지금 현재가로 팔면 늘어나/줄어드는 원화. %와 같은 기준.
            var krwTxt = (c.unreal_krw!==null && c.unreal_krw!==undefined)
                       ? '<div class="sub '+uc+'">'+signed(c.unreal_krw)+'원</div>' : '';
            unreal = pctTxt + krwTxt;
          }
          var pnl='<span class="'+(c.pnl>=0?'green':'red')+'">'+signed(c.pnl)+'</span>';
          var roi='<span class="'+(c.roi>=0?'green':'red')+'">'+signed(c.roi,2)+'%</span>';
          rows+='<tr><td><b>'+c.coin+'</b></td><td>'+price+'</td><td>'+hold+'</td><td>'+unreal
               +'</td><td>'+pnl+'</td><td>'+roi+'</td><td>'+Math.round(c.win_rate||0)+'%</td><td>'+(c.trade_count||0)+'</td></tr>';
        }
        cEl.innerHTML='<div class="tbl-wrap"><table class="db"><tr><th>종목</th><th>현재가</th><th>보유</th>'
                     +'<th>평가손익</th><th>실현손익</th><th>수익률</th><th>승률</th><th>매매</th></tr>'+rows+'</table></div>';
      } else {
        cEl.innerHTML='<div class="empty">데이터 없음</div>';
      }
    }

    // 최근 매매 (전 종목 합쳐서)
    var tEl=document.getElementById('trades');
    if(tEl){
      var trd = s.trades || [];
      if(trd.length){
        var rows='';
        for(var j=0;j<trd.length;j++){
          var t=trd[j];
          var side = t.side==='buy' ? '<span class="green">매수</span>' : '<span class="red">매도</span>';
          var pl='<span class="muted">-</span>';
          if(t.side==='sell' && t.pnl!==null && t.pnl!==undefined)
            pl='<span class="'+(t.pnl>=0?'green':'red')+'">'+signed(t.pnl)+'</span>';
          rows+='<tr><td>'+t.time+'</td><td>'+t.coin+'</td><td>'+side+'</td><td>'+fmt(t.price)+'</td><td>'+fmt(t.krw)+'</td><td>'+pl+'</td></tr>';
        }
        tEl.innerHTML='<div class="tbl-wrap"><table class="db"><tr><th>시각</th><th>종목</th><th>구분</th>'
                     +'<th>가격</th><th>금액(원)</th><th>손익(원)</th></tr>'+rows+'</table></div>';
      } else {
        tEl.innerHTML='<div class="empty">아직 매매 기록이 없어요. 신호가 뜨면 여기에 쌓입니다.</div>';
      }
    }
  }

  function poll(){
    fetch('/api/state', {cache:'no-store'})
      .then(function(r){ return r.ok ? r.json() : null; })
      .then(function(s){ if(s) applyState(s); })
      .catch(function(){ /* 네트워크 일시 오류는 무시하고 다음 주기에 재시도 */ });
  }

  function refreshChart(){
    var img=document.getElementById('chart');
    if(!img) return;
    var url='/chart.png?t='+Date.now();
    var next=new Image();
    next.onload=function(){ img.src=url; };   // 다 받은 뒤에만 교체 → 깜빡임 없음
    next.src=url;
  }

  function send(action){
    fetch('/'+action, {method:'POST'}).then(poll).catch(function(){});
  }
  // ⏸/▶️ 폼 제출을 가로채 새로고침 없이 처리 (자바스크립트 꺼져 있으면 기존 폼 동작이 그대로 작동)
  var forms=document.querySelectorAll('.ctrl form');
  for(var i=0;i<forms.length;i++){
    forms[i].addEventListener('submit', function(e){
      e.preventDefault();
      var action=this.getAttribute('action').replace('/','');
      send(action);
    });
  }

  poll();
  setInterval(poll, 5000);            // 숫자·상태·기록: 5초마다
  setInterval(refreshChart, 60000);   // 차트: 60초마다 (서버 생성 주기와 맞춤)
})();
</script>
"""


@app.route("/")
@require_auth
def index():
    paused = read_paused()
    live = not CONFIG["DRY_RUN"]
    coins = coins_list()
    primary = CONFIG["TICKER"] if CONFIG["TICKER"] in coins else coins[0]

    mode_badge = ('<span class="badge b-live">실거래</span>' if live
                  else '<span class="badge b-dry">모의(DRY_RUN)</span>')
    state_badge = ('<span class="badge b-pause" id="badge-state">⏸ 일시정지</span>' if paused
                   else '<span class="badge b-run" id="badge-state">▶️ 매매중</span>')
    # 권한 확인: 관리자(admin)면 조작 버튼을 보여주고, 읽기 전용(viewer)이면 숨깁니다.
    is_admin = (getattr(g, "role", "admin") == "admin")
    view_badge = '' if is_admin else '<span class="badge b-view">👁 읽기전용</span>'

    # 지금 '실제로 적용 중인' 설정(대시보드로 바꾼 값 우선, 없으면 .env 기본값)
    eff_sell, eff_buy, eff_itv = effective("sell_mode"), effective("buy_mode"), effective("interval")
    itv_disp = INTERVAL_LABEL.get(eff_itv, eff_itv)
    regime_btns = _regime_buttons(eff_buy, eff_sell)
    regime_name = _current_regime_name(eff_buy, eff_sell)
    regime_now = f"장세 <b>{regime_name}</b> · " if regime_name else "장세 <b>사용자설정</b> · "
    sell_btns = _mode_buttons("sell_mode", SELL_OPTS, eff_sell)
    buy_btns = _mode_buttons("buy_mode", BUY_OPTS, eff_buy)
    itv_btns = _mode_buttons("interval", INTERVAL_OPTS, eff_itv)
    env_itv_disp = INTERVAL_LABEL.get(CONFIG['INTERVAL'], CONFIG['INTERVAL'])

    # 조작 영역(일시정지/재개 · 장세 · 세부 설정)은 '관리자'에게만 보여줍니다.
    # 읽기 전용 계정에는 지금 적용 중인 설정 안내문만 보여 줍니다(버튼 없음).
    if is_admin:
        controls_html = f"""
      <div class="ctrl">
        <form method="post" action="/pause"><button id="btn-pause" class="on-pause" {'disabled' if paused else ''}>⏸ 일시정지</button></form>
        <form method="post" action="/resume"><button id="btn-resume" class="on-resume" {'disabled' if not paused else ''}>▶️ 재개</button></form>
      </div>

      <h2 class="sec">장세 (버튼 누르면 즉시 적용)</h2>
      <div class="modes">
        <div class="mrow"><span class="mlabel">장세</span>{regime_btns}</div>
        <div class="mrow"><span class="mlabel">봉</span>{itv_btns}</div>
      </div>
      <div class="muted" style="font-size:12px; margin:-6px 0 10px;">
        📈 상승장 = 올라타서 끝까지 &nbsp;·&nbsp; ↔️ 횡보장 = 싸면 사서 오르면 바로 &nbsp;·&nbsp; 📉 하락장 = 싼 것만 조금, 거의 현금
      </div>

      <details class="adv">
        <summary>세부 설정 (매수·매도 직접 고르기)</summary>
        <div class="modes" style="margin-top:8px;">
          <div class="mrow"><span class="mlabel">매도</span>{sell_btns}</div>
          <div class="mrow"><span class="mlabel">매수</span>{buy_btns}</div>
        </div>
      </details>

      <div class="muted" style="font-size:12px; margin:6px 0 18px;">
        지금 적용: {regime_now}봉 <b>{itv_disp}</b>
        &nbsp;|&nbsp; 차트·DB는 .env 봉({env_itv_disp}) 기준
      </div>
"""
    else:
        controls_html = f"""
      <div class="muted" style="font-size:12px; margin:2px 0 18px;">
        👁 읽기 전용 계정입니다. 현황·DB 보기만 가능해요.
        &nbsp;|&nbsp; 지금 적용: {regime_now}봉 <b>{itv_disp}</b>
      </div>
"""

    body = f"""
    <div class="wrap">
      <div class="top">
        <h1>BTC TRADING BOT</h1>
        {mode_badge}{state_badge}{view_badge}
        <span class="upd">{', '.join(coins)} · {itv_disp} · 갱신 <span id="upd-time">{time.strftime('%H:%M:%S')}</span></span>
      </div>

      {_nav('home')}
      {controls_html}
      <h2 class="sec">종목별 현황</h2>
      <div id="coins"><div class="empty">불러오는 중…</div></div>

      <img class="chart" id="chart" src="/chart.png?t={int(time.time())}" alt="차트 로딩 중...">
      <div class="muted" style="font-size:12px; margin:-10px 0 18px;">📈 차트는 {primary} 기준</div>

      <h2 class="sec">최근 매매 (전 종목)</h2>
      <div id="trades"><div class="empty">불러오는 중…</div></div>
    </div>
    """

    html = ("<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>BTC Bot</title>" + CSS + "</head><body>" + body + DASH_JS + "</body></html>")
    return html


@app.route("/api/state")
@require_auth
def api_state():
    """모든 종목의 현황(숫자·보유상태)과 합친 최근 매매를 JSON 으로 돌려줍니다.
    대시보드가 페이지 새로고침 없이 이걸 주기적으로 받아 갱신합니다(깜빡임 방지)."""
    def safe(x):
        # JSON 은 NaN/무한대를 표현하지 못하므로 그런 값은 null 로 바꿉니다.
        if isinstance(x, float) and not math.isfinite(x):
            return None
        return x

    coins_data = []
    all_trades = []
    for coin in coins_list():
        st = read_state_for(coin)
        price = current_price(coin)
        roi, win_rate, unreal, unreal_krw = compute_stats(st, price)
        coins_data.append({
            "coin": coin,
            "price": safe(price),
            "pnl": safe(st.get("realized_pnl", 0.0)),
            "roi": safe(roi),
            "win_rate": safe(win_rate),
            "trade_count": st.get("trade_count", 0),
            "holding": bool(st.get("holding")),
            "entry_price": safe(st.get("entry_price", 0.0)),
            "unreal": safe(unreal),
            "unreal_krw": safe(unreal_krw),
        })
        for t in st.get("trades", [])[-15:]:
            all_trades.append({
                "coin": coin,
                "time": str(t.get("time", "")).replace("T", " "),
                "side": t.get("side"),
                "price": safe(t.get("price")),
                "krw": safe(t.get("krw")),
                "pnl": safe(t.get("pnl")),
            })

    # 전 종목 매매를 시간 역순으로 합쳐 최근 15개만
    all_trades.sort(key=lambda x: x["time"], reverse=True)
    all_trades = all_trades[:15]

    return jsonify({
        "updated": time.strftime("%H:%M:%S"),
        "paused": bool(read_paused()),
        "live": (not CONFIG["DRY_RUN"]),
        "coins": coins_data,
        "trades": all_trades,
    })


@app.route("/chart.png")
@require_auth
def chart_png():
    ensure_chart()
    if os.path.exists(REPORT_PNG):
        return send_file(REPORT_PNG, mimetype="image/png")
    return Response("차트 준비 중 (데이터가 더 쌓이면 표시됩니다)", 503)


@app.route("/pause", methods=["POST"])
@require_admin
def pause():
    write_paused(True)
    return redirect("/")


@app.route("/resume", methods=["POST"])
@require_admin
def resume():
    write_paused(False)
    return redirect("/")


@app.route("/set/<field>/<value>", methods=["POST"])
@require_admin
def set_setting(field, value):
    """대시보드 설정 버튼이 호출합니다. 허용된 (field, value)만 control.json 에 기록 →
    봇이 다음 루프에서 읽어 즉시 반영합니다. 허용 외 값은 무시(봇 보호)."""
    field = (field or "").strip().lower()
    value = (value or "").strip()
    if field in SETTING_MAP and value in SETTING_MAP[field][1]:
        write_control({field: value})
    return redirect("/")


@app.route("/preset/<name>", methods=["POST"])
@require_admin
def set_preset(name):
    """장세 버튼이 호출. 그 장세에 맞는 매수·매도 조합을 한 번에 control.json 에 기록."""
    name = (name or "").strip().lower()
    for key, _label, settings, _desc in REGIME_PRESETS:
        if key == name:
            write_control(settings)
            break
    return redirect("/")


@app.route("/db")
@require_auth
def db_view():
    import db
    market, tf, dbf = CONFIG["TICKER"], CONFIG["INTERVAL"], CONFIG["DB_FILE"]

    try:
        stats = db.get_stats(dbf, market, tf)
    except Exception:
        stats = {"candle_count": 0, "first": None, "last": None}

    # --- 매매 기록 (최신순, 최대 200) ---
    try:
        tdf = db.get_trades(dbf, limit=200)
    except Exception:
        tdf = None
    trows = []
    if tdf is not None and len(tdf):
        for _, row in tdf.iterrows():
            side_html = ('<span class="green">매수</span>' if row.get("side") == "buy"
                         else '<span class="red">매도</span>')
            pnl = row.get("pnl")
            if pnl is None or (isinstance(pnl, float) and pnl != pnl):
                pnl_html = '<span class="muted">-</span>'
            else:
                pnl_html = f'<span class="{"green" if pnl >= 0 else "red"}">{pnl:+,.0f}</span>'
            trows.append([str(row.get("ts", "")).replace("T", " "), side_html,
                          _num(row.get("price")), _num(row.get("krw")),
                          _num(row.get("amount"), 8), pnl_html, row.get("mode", "")])
    trades_tbl = _table_html(["시각", "구분", "가격", "금액(원)", "수량", "손익(원)", "모드"], trows)

    # --- 최근 지표 30 ---
    try:
        idf = db.get_recent_indicators(dbf, market, tf, limit=30)
    except Exception:
        idf = None
    irows = []
    if idf is not None and len(idf):
        for ts, row in idf.iloc[::-1].iterrows():
            irows.append([str(ts), _num(row.get("close")), _num(row.get("ma_short")),
                          _num(row.get("ma_long")), _num(row.get("rsi"), 1),
                          _num(row.get("bb_lower")), _num(row.get("bb_upper"))])
    ind_tbl = _table_html(["시각", "종가", "MA단기", "MA장기", "RSI", "BB하단", "BB상단"], irows)

    # --- 최근 원본 시세 30 ---
    try:
        cdf = db.get_recent_candles(dbf, market, tf, limit=30)
    except Exception:
        cdf = None
    crows = []
    if cdf is not None and len(cdf):
        for ts, row in cdf.iloc[::-1].iterrows():
            crows.append([str(ts), _num(row["open"]), _num(row["high"]),
                          _num(row["low"]), _num(row["close"]), _num(row["volume"], 3)])
    candles_tbl = _table_html(["시각", "시가", "고가", "저가", "종가", "거래량"], crows)

    rng = f'{str(stats["first"])[:16]} ~ {str(stats["last"])[:16]}' if stats["last"] else "-"
    body = f"""
    <div class="wrap">
      <div class="top"><h1>BTC TRADING BOT</h1>
        <span class="upd">{market} · {tf} · 갱신 {time.strftime('%H:%M:%S')}</span></div>
      {_nav('db')}
      <div class="hold">창고 현황 · 캔들 <b>{stats['candle_count']:,}</b>개 · 기간 {rng}</div>
      <h2 class="sec">매매 기록 (trades)</h2>
      {trades_tbl}
      <h2 class="sec">최근 지표 30 (indicators)</h2>
      {ind_tbl}
      <h2 class="sec">최근 원본 시세 30 (raw_candles)</h2>
      {candles_tbl}
    </div>
    """
    html = ("<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<meta http-equiv='refresh' content='30'>"
            "<title>BTC Bot · DB</title>" + CSS + "</head><body>" + body + "</body></html>")
    return html


if __name__ == "__main__":
    # 비밀번호가 없으면 시작하지 않습니다 (실수로 인터넷에 무방비 공개되는 것 방지).
    if not DASH_USER or not DASH_PASS:
        raise SystemExit(
            "[보안 중단] .env 에 DASH_USER / DASH_PASS 가 없습니다.\n"
            "비밀번호 없이는 대시보드를 열 수 없어요. .env 에 아래를 추가하세요:\n"
            "    DASH_USER=admin\n    DASH_PASS=원하는_강한_비밀번호\n"
        )
    print(f"대시보드 시작 → http://0.0.0.0:{DASH_PORT}  (관리자 아이디: {DASH_USER})")
    if DASH_VIEW_USER and DASH_VIEW_PASS:
        print(f"  · 읽기 전용 계정 활성화 (아이디: {DASH_VIEW_USER}) — 현황·DB 보기만 가능")
    else:
        print("  · 읽기 전용 계정 없음 (.env 에 DASH_VIEW_USER / DASH_VIEW_PASS 추가하면 생성)")
    serve(app, host="0.0.0.0", port=DASH_PORT)
