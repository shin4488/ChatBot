# -*- coding: utf-8 -*-

#  Licensed under the Apache License, Version 2.0 (the "License"); you may
#  not use this file except in compliance with the License. You may obtain
#  a copy of the License at
#
#       https://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#  WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#  License for the specific language governing permissions and limitations
#  under the License.

import os
import sys
import time
from argparse import ArgumentParser
from collections import defaultdict, deque
from flask import Flask, request, abort
from google import genai
from google.genai import types
from linebot import (
    LineBotApi, WebhookHandler
)
from linebot.exceptions import (
    InvalidSignatureError
)
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
)

app = Flask(__name__)

# get channel_secret, channel_access_token and gemini_api_key from your environment variable
channel_secret = os.getenv('LINE_CHANNEL_SECRET', None)
channel_access_token = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', None)
gemini_api_key = os.getenv('GEMINI_API_KEY', None)
if channel_secret is None:
    print('Specify LINE_CHANNEL_SECRET as environment variable.')
    sys.exit(1)
if channel_access_token is None:
    print('Specify LINE_CHANNEL_ACCESS_TOKEN as environment variable.')
    sys.exit(1)
if gemini_api_key is None:
    print('Specify GEMINI_API_KEY as environment variable.')
    sys.exit(1)

line_bot_api = LineBotApi(channel_access_token)
handler = WebhookHandler(channel_secret)
# A single attempt with a timeout shorter than the gunicorn worker timeout:
# the SDK retries for up to a minute by default, and the worker would be
# killed before a reply is sent.
gemini = genai.Client(
    api_key=gemini_api_key,
    http_options=types.HttpOptions(
        timeout=20_000,
        retry_options=types.HttpRetryOptions(attempts=1),
    ),
)

GEMINI_MODEL = "gemini-3.5-flash-lite"
SYSTEM_INSTRUCTION = (
    "あなたはLINEの雑談ボットです。"
    "日本語で、友だちに話すようなくだけた口調で、1〜3文の短い返事をします。"
    "マークダウンや箇条書きは使いません。"
)
# Thinking tokens count toward the output limit.
MAX_OUTPUT_TOKENS = 400
MAX_MESSAGE_CHARS = 500
# Anyone who adds the bot can send messages, and every reply is billed.
MAX_MESSAGES_PER_SENDER = 20
RATE_WINDOW_SECONDS = 600

REPLY_BUSY = "ごめん、今ちょっと忙しい"
REPLY_BROKEN = "故障かな？ごめんよ、ちょっと開発者呼んできて"
REPLY_SERVER_ERROR = "Server errorだよ。ちょっと待ってね"
REPLY_NOT_UNDERSTOOD = "ごめん、ちょっとよく分かんない"
REPLY_TOO_LONG = "ごめん、文章長すぎて読み切れない"
REPLY_UNREACHABLE = "あれ、聞こえてますか？"

recent_messages = defaultdict(deque)

@app.route("/callback", methods=['POST'])
def callback():
    # get X-Line-Signature header value
    signature = request.headers.get('X-Line-Signature')
    if not signature:
        abort(400)
    # get request body as text
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)
    # handle webhook body
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

def chat(words):
    if len(words) > MAX_MESSAGE_CHARS:
        return REPLY_TOO_LONG
    try:
        interaction = gemini.interactions.create(
            model=GEMINI_MODEL,
            system_instruction=SYSTEM_INSTRUCTION,
            input=words,
            generation_config={
                "max_output_tokens": MAX_OUTPUT_TOKENS,
                "thinking_level": "minimal",
            },
        )
    except Exception as error:
        app.logger.warning("Gemini request failed: %s", error)
        # The SDK exposes no public exception classes for this call; the
        # HTTP status is absent when no response arrived.
        return error_reply(getattr(error, "status_code", None))
    # Empty when the reply was blocked or the output limit was spent on thinking.
    return (interaction.output_text or "").strip() or REPLY_NOT_UNDERSTOOD

def error_reply(status_code):
    if status_code is None:
        return REPLY_UNREACHABLE
    if status_code == 429:
        return REPLY_BUSY
    if status_code >= 500:
        return REPLY_SERVER_ERROR
    # An invalid API key, an exhausted prepaid balance or a rejected request.
    return REPLY_BROKEN

def over_rate_limit(sender_id):
    now = time.monotonic()
    sent = recent_messages[sender_id]
    while sent and now - sent[0] > RATE_WINDOW_SECONDS:
        sent.popleft()
    if len(sent) >= MAX_MESSAGES_PER_SENDER:
        return True
    sent.append(now)
    return False

@handler.add(MessageEvent, message=TextMessage)
def message_text(event):
    source = event.source
    # user_id is absent for group members who have not added the bot.
    sender_id = source.user_id or getattr(source, "group_id", None) or getattr(source, "room_id", None)
    if over_rate_limit(sender_id):
        reply = REPLY_BUSY
    else:
        reply = chat(event.message.text)
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)