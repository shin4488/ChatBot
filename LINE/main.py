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
from argparse import ArgumentParser
from flask import Flask, request, abort
from linebot import (
    LineBotApi, WebhookHandler
)
from linebot.exceptions import (
    InvalidSignatureError
)
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
)
import requests

app = Flask(__name__)

# get channel_secret and channel_access_token from your environment variable
channel_secret = os.getenv('LINE_CHANNEL_SECRET', None)
channel_access_token = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', None)
if channel_secret is None:
    print('Specify LINE_CHANNEL_SECRET as environment variable.')
    sys.exit(1)
if channel_access_token is None:
    print('Specify LINE_CHANNEL_ACCESS_TOKEN as environment variable.')
    sys.exit(1)

line_bot_api = LineBotApi(channel_access_token)
handler = WebhookHandler(channel_secret)

@app.route("/callback", methods=['POST'])
def callback():
    # get X-Line-Signature header value
    signature = request.headers['X-Line-Signature']
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
    endpoint = "https://api.a3rt.recruit.co.jp/talk/v1/smalltalk"
    api_key = os.getenv('TALK_API_KEY', None)
    if api_key is None:
        print('Specify TALK_API_KEY as environment variable.')
        sys.exit(1)
    param = {"apikey":api_key, "query":words}
    response = requests.post(endpoint, data=param)
    errorMsg = "あれ、聞こえてますか？"
    if response.status_code == 200:
        status = switchResponseWord(response.json().get("status"))
        if status == "OK":
            return response.json().get("results")[0].get("reply")
        else:
            return status
    else:
        return errorMsg

def switchResponseWord(status_code):
    response_text = {
        0:"OK",
        1000:"故障かな？ごめんよ、ちょっと開発者呼んできて",
        1001:"故障かな？ごめんよ、ちょっと開発者呼んできて",
        1002:"故障かな？ごめんよ、ちょっと開発者呼んできて",
        1003:"故障かな？ごめんよ、ちょっと開発者呼んできて",
        1010:"Server errorだよ。ちょっと待ってね",
        1011:"Server errorだよ。 ちょっと待ってね",
        1030:"ごめん、今ちょっと忙しい",
        1400:"ごめん、ちょっとよく分かんない",
        1404:"ごめん、ちょっとよく分かんない",
        1405:"ごめん、ちょっとよく分かんない",
        1413:"ごめん、文章長すぎて読み切れない",
        1500:"Server error. ちょっと待ってね",
        2000:"ごめん、ちょっとよく分かんない"
    }
    return response_text.get(status_code)

@handler.add(MessageEvent, message=TextMessage)
def message_text(event):
    try:
        responsed_message = chat(event.message.text)
    except Exception as error:
        responsed_message = error
    finally:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=responsed_message)
        )

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)