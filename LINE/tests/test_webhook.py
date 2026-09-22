"""Verify the HTTP contracts without contacting LINE or Talk API."""
import base64
import hashlib
import hmac
import json
import os
import socket
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

os.environ.update(LINE_CHANNEL_SECRET="test-secret", LINE_CHANNEL_ACCESS_TOKEN="test-token", TALK_API_KEY="test-talk-key")
import main
import requests


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.client = main.app.test_client()
        self.sent = []
        self.talk_status = 200
        self.talk_body = {"status": 0, "results": [{"reply": "こんにちは"}]}
        def send(adapter, request, **kwargs):
            self.sent.append(request)
            response = requests.Response()
            response.status_code = self.talk_status if "a3rt" in request.url else 200
            response._content = json.dumps(self.talk_body if "a3rt" in request.url else {}).encode()
            response.headers["Content-Type"] = "application/json"
            return response
        self.transport = patch.object(requests.adapters.HTTPAdapter, "send", send)
        self.transport.start()
        self.addCleanup(self.transport.stop)

    def post(self, events, signature=None):
        body = json.dumps({"destination": "test", "events": events}, ensure_ascii=False).encode()
        if signature is None:
            signature = base64.b64encode(hmac.new(b"test-secret", body, hashlib.sha256).digest()).decode()
        return self.client.post("/callback", data=body, headers={"X-Line-Signature": signature}, content_type="application/json")

    def message(self):
        return {"type": "message", "timestamp": 0, "replyToken": "reply-token", "source": {"type": "user", "userId": "test-user"}, "message": {"type": "text", "id": "1", "text": "やあ"}}

    def test_verified_message_replies_in_japanese(self):
        response = self.post([self.message()])
        self.assertEqual((response.status_code, response.text), (200, "OK"))
        talk, reply = self.sent
        self.assertEqual(talk.url, "https://api.a3rt.recruit.co.jp/talk/v1/smalltalk")
        self.assertEqual(parse_qs(talk.body), {"apikey": ["test-talk-key"], "query": ["やあ"]})
        self.assertEqual(reply.url, "https://api.line.me/v2/bot/message/reply")
        self.assertEqual(json.loads(reply.body), {"replyToken": "reply-token", "messages": [{"type": "text", "text": "こんにちは"}], "notificationDisabled": False})

    def test_invalid_signature_never_sends_messages(self):
        self.assertEqual(self.post([self.message()], "invalid").status_code, 400)
        self.assertEqual(self.sent, [])

    def test_tampered_message_is_rejected(self):
        body = json.dumps({"destination": "test", "events": []}).encode()
        signature = base64.b64encode(hmac.new(b"test-secret", body, hashlib.sha256).digest()).decode()
        self.assertEqual(self.post([self.message()], signature).status_code, 400)
        self.assertEqual(self.sent, [])

    def test_non_text_event_is_acknowledged_without_reply(self):
        event = self.message()
        event["message"] = {"type": "sticker", "id": "1", "packageId": "1", "stickerId": "1"}
        self.assertEqual(self.post([event]).status_code, 200)
        self.assertEqual(self.sent, [])

    def test_each_message_in_batch_receives_its_own_reply(self):
        first, second = self.message(), self.message()
        second["replyToken"] = "second-token"
        self.assertEqual(self.post([first, second]).status_code, 200)
        replies = [json.loads(r.body) for r in self.sent if r.url.startswith("https://api.line.me/")]
        self.assertEqual([r["replyToken"] for r in replies], ["reply-token", "second-token"])

    def test_missing_signature_is_rejected(self):
        self.assertEqual(self.client.post("/callback", json={"events": []}).status_code, 400)
        self.assertEqual(self.sent, [])

    def test_empty_verified_webhook_is_acknowledged(self):
        response = self.post([])
        self.assertEqual((response.status_code, response.text), (200, "OK"))
        self.assertEqual(self.sent, [])

    def test_callback_requires_post(self):
        self.assertEqual(self.client.get("/callback").status_code, 405)

    def test_talk_http_failure_preserves_fallback(self):
        self.talk_status = 503
        self.assertEqual(self.post([self.message()]).status_code, 200)
        self.assertEqual(json.loads(self.sent[-1].body)["messages"][0]["text"], "あれ、聞こえてますか？")

    def test_talk_service_error_preserves_reply(self):
        self.talk_body = {"status": 1030}
        self.assertEqual(self.post([self.message()]).status_code, 200)
        self.assertEqual(json.loads(self.sent[-1].body)["messages"][0]["text"], "ごめん、今ちょっと忙しい")


class ServerTests(unittest.TestCase):
    def test_gunicorn_serves_verified_webhook_and_rejects_unsigned_request(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        process = subprocess.Popen(
            [sys.executable, "-m", "gunicorn", "--chdir", "LINE", "--bind", f"127.0.0.1:{port}", "main:app"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            url = f"http://127.0.0.1:{port}/callback"
            for _ in range(80):
                try:
                    response = requests.post(url, json={"events": []}, timeout=1)
                    break
                except requests.ConnectionError:
                    if process.poll() is not None:
                        self.fail("Gunicorn exited before serving requests")
                    time.sleep(0.1)
            else:
                self.fail("Gunicorn did not become ready")
            self.assertEqual(response.status_code, 400)
            body = b'{"events": []}'
            signature = base64.b64encode(hmac.new(b"test-secret", body, hashlib.sha256).digest()).decode()
            response = requests.post(url, data=body, headers={"X-Line-Signature": signature, "Content-Type": "application/json"}, timeout=3)
            self.assertEqual((response.status_code, response.text), (200, "OK"))
        finally:
            process.terminate()
            process.wait(timeout=15)


if __name__ == "__main__":
    unittest.main()
