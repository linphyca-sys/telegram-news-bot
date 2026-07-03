# -*- coding: utf-8 -*-
"""
텔레그램 chat_id 확인 도우미.
1) 텔레그램에서 봇에게 아무 메시지나 먼저 보낸 뒤
2) python get_chat_id.py 실행
"""
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")
token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
if not token:
    raise SystemExit(".env에 TELEGRAM_BOT_TOKEN을 먼저 설정하세요.")

resp = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15)
data = resp.json()
if not data.get("ok"):
    raise SystemExit(f"API 오류: {data}")

chats = {}
for upd in data.get("result", []):
    msg = upd.get("message") or upd.get("channel_post") or {}
    chat = msg.get("chat")
    if chat:
        chats[chat["id"]] = chat.get("title") or chat.get("username") or chat.get("first_name")

if not chats:
    print("수신된 메시지가 없습니다. 텔레그램에서 봇에게 메시지를 먼저 보낸 뒤 다시 실행하세요.")
else:
    print("발견된 chat_id:")
    for cid, name in chats.items():
        print(f"  {cid}  ({name})")
    print("\n이 값을 .env의 TELEGRAM_CHAT_ID에 넣으세요.")
