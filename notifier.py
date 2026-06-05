# =====================================================================
#  notifier.py  ―  텔레그램으로 '알림 보내기'와 '명령 받기'를 담당
# ---------------------------------------------------------------------
#  - 봇이 매수/매도 했을 때, 또는 정기적으로 텔레그램 메시지를 보냅니다.
#  - 내가 텔레그램에서 /status, /pause 같은 명령(또는 버튼)을 보내면 알아듣고 처리합니다.
#  - 화면 입력창 아래에 '버튼 키보드'를 띄워서, 타이핑 없이 딸깍으로 조작할 수 있습니다.
#  - 무거운 라이브러리 없이, 텔레그램이 제공하는 기본 통신(Bot API)만 사용합니다.
# =====================================================================

import json        # 버튼 키보드 설정을 텔레그램이 알아듣는 형식(JSON)으로 바꿀 때 사용
import threading   # threading: 두 가지 일을 '동시에' 하기 위한 도구
                   #            (매매 루프와 '명령 듣기'를 동시에 돌리는 데 씁니다)
import time        # 시간 관련 (잠깐 쉬기 등)
import requests    # requests: 인터넷으로 데이터를 주고받는 도구 (텔레그램 서버와 통신)


# 화면 입력창 아래에 뜨는 '버튼 키보드' 구성입니다. (2줄 × 2개)
#  - 버튼을 누르면 그 버튼의 '글자'가 메시지로 전송되고, 아래 _handle_command 가 알아듣습니다.
#  - resize_keyboard: 버튼을 아담한 크기로 / is_persistent: 항상 보이게 유지
REPLY_KEYBOARD = json.dumps({
    "keyboard": [["📊 차트", "📈 상태"], ["⏸ 정지", "▶️ 재개"]],
    "resize_keyboard": True,
    "is_persistent": True,
})


class TelegramNotifier:
    """텔레그램 알림/명령을 담당하는 '비서'입니다. (class = 기능 묶음 설계도)"""

    def __init__(self, token, chat_id):
        """비서를 만들 때 한 번 실행. token=봇 열쇠, chat_id=내 채팅 번호."""
        self.token = token
        self.chat_id = str(chat_id)                          # 숫자를 글자로 바꿔 보관
        self.base = f"https://api.telegram.org/bot{token}"   # 텔레그램 서버 주소
        self.enabled = bool(token and chat_id)               # 토큰/번호가 둘 다 있어야 작동
        self._offset = None        # 이미 읽은 메시지를 다시 안 읽기 위한 표시
        self._stop_flag = False    # 명령 듣기를 멈출지 여부

        # 아래 5개는 '특정 명령이 오면 실행할 함수'를 담아둘 자리입니다.
        # 실제 함수는 bot.py 에서 채워 넣습니다. (지금은 비어 있음)
        self.on_status = None
        self.on_pause = None
        self.on_resume = None
        self.on_stop = None
        self.on_report = None      # 📊 차트 버튼 / /report 명령이 왔을 때 실행할 함수

    def send(self, text, with_buttons=False):
        """
        텔레그램으로 메시지(text)를 보냅니다.
        with_buttons=True 면 화면 아래 '버튼 키보드'도 같이 띄웁니다.
        (버튼은 한 번 띄우면 계속 남아있어서, 보통 봇 시작할 때 한 번만 True 로 보내면 됩니다.)
        """
        if not self.enabled:
            print("[텔레그램 미설정] " + text)  # 설정이 없으면 그냥 화면에 출력
            return
        try:
            data = {"chat_id": self.chat_id, "text": text}
            if with_buttons:
                data["reply_markup"] = REPLY_KEYBOARD   # 버튼 키보드 붙이기
            # 텔레그램 서버에 "이 채팅으로 이 글을 보내줘" 라고 요청합니다.
            requests.post(self.base + "/sendMessage", data=data, timeout=10)
        except Exception as e:
            print("텔레그램 전송 실패:", e)  # 인터넷 문제 등으로 실패해도 봇은 멈추지 않습니다

    def send_photo(self, photo_path, caption=None):
        """
        이미지 파일(photo_path)을 텔레그램으로 전송합니다. (차트 사진 보내기용)
        caption: 사진 아래 붙일 설명 글(선택).
        """
        if not self.enabled:
            print(f"[텔레그램 미설정] (사진: {photo_path}) " + (caption or ""))
            return
        try:
            # 사진 파일을 열어서 'sendPhoto' 로 업로드합니다.
            with open(photo_path, "rb") as f:
                requests.post(
                    self.base + "/sendPhoto",
                    data={"chat_id": self.chat_id, "caption": caption or ""},
                    files={"photo": f},
                    timeout=30,   # 사진은 글보다 오래 걸릴 수 있어 넉넉히
                )
        except Exception as e:
            print("텔레그램 사진 전송 실패:", e)

    # -------------------- 명령 듣기 (백그라운드) --------------------
    def start_command_listener(self):
        """
        '명령 듣기'를 별도로 동시에 시작합니다.
        이렇게 해두면 매매를 돌리는 와중에도 내 명령(/status 등)을 받을 수 있습니다.
        """
        if not self.enabled:
            return
        # daemon=True : 메인 프로그램이 끝나면 이 듣기도 같이 끝난다는 뜻입니다.
        t = threading.Thread(target=self._poll_loop, daemon=True)
        t.start()

    def stop(self):
        """명령 듣기를 멈추라는 신호."""
        self._stop_flag = True

    def _poll_loop(self):
        """
        텔레그램 서버에 '새 메시지 왔어?' 라고 계속 물어보는 반복문입니다.
        새 메시지가 오면 명령인지 확인해서 처리합니다.
        """
        while not self._stop_flag:       # 멈추라는 신호가 오기 전까지 계속 반복
            try:
                params = {"timeout": 30}             # 최대 30초 기다리며 메시지 확인
                if self._offset is not None:
                    params["offset"] = self._offset  # 이미 본 메시지는 건너뛰기
                r = requests.get(self.base + "/getUpdates", params=params, timeout=40)
                for upd in r.json().get("result", []):   # 새로 온 메시지들을 하나씩
                    self._offset = upd["update_id"] + 1  # 다음엔 이 다음 것부터 보도록 표시
                    msg = upd.get("message") or upd.get("channel_post")
                    if not msg:
                        continue
                    # 다른 사람이 보낸 건 무시하고, '내 채팅'에서 온 것만 처리 (보안)
                    if str(msg.get("chat", {}).get("id")) != self.chat_id:
                        continue
                    # 받은 글(또는 누른 버튼 글자)을 정리해서 명령 처리로 넘깁니다.
                    self._handle_command((msg.get("text") or "").strip().lower())
            except Exception:
                time.sleep(5)  # 오류가 나도 5초 쉬고 계속 시도

    def _handle_command(self, text):
        """
        받은 글(또는 누른 버튼)이 어떤 명령인지 보고, 거기에 맞는 함수를 실행합니다.
        - 슬래시 명령(/status 등)과 버튼 글자(📈 상태 등)를 둘 다 알아듣습니다.
        - 버튼은 누르면 그 글자가 메시지로 오므로, 글자에 든 '핵심 단어'로 구분합니다.
        """
        if text in ("/status", "/stats") or "상태" in text:
            cb = self.on_status
        elif text in ("/report", "/chart") or "차트" in text or "리포트" in text:
            cb = self.on_report
        elif text == "/pause" or "정지" in text:
            cb = self.on_pause
        elif text == "/resume" or "재개" in text:
            cb = self.on_resume
        elif text == "/stop":
            cb = self.on_stop
        else:
            cb = None
        if cb:        # 연결된 함수가 있을 때만 실행합니다.
            cb()
