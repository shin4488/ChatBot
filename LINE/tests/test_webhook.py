"""Verify the HTTP contracts without contacting LINE or the Gemini API."""
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

os.environ.update(LINE_CHANNEL_SECRET="test-secret", LINE_CHANNEL_ACCESS_TOKEN="test-token", GEMINI_API_KEY="test-gemini-key")
import httpx2
import main
import requests


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.client = main.app.test_client()
        main.recent_messages.clear()
        self.sent = []
        def send(adapter, request, **kwargs):
            self.sent.append(request)
            response = requests.Response()
            response.status_code = 200
            response._content = b"{}"
            response.headers["Content-Type"] = "application/json"
            return response
        self.transport = patch.object(requests.adapters.HTTPAdapter, "send", send)
        self.transport.start()
        self.addCleanup(self.transport.stop)

        self.generated = []
        self.gemini_status = 200
        self.gemini_body = {"id": "interaction-1", "status": "completed", "steps": [
            {"type": "thought"},
            {"type": "model_output", "content": [{"type": "text", "text": "こんにちは"}]},
        ]}
        self.gemini_unreachable = False
        def generate(transport, request):
            self.generated.append(request)
            if self.gemini_unreachable:
                raise httpx2.ConnectError("unreachable", request=request)
            return httpx2.Response(self.gemini_status, json=self.gemini_body, request=request)
        self.gemini_transport = patch.object(httpx2.HTTPTransport, "handle_request", generate)
        self.gemini_transport.start()
        self.addCleanup(self.gemini_transport.stop)

    def post(self, events, signature=None):
        body = json.dumps({"destination": "test", "events": events}, ensure_ascii=False).encode()
        if signature is None:
            signature = base64.b64encode(hmac.new(b"test-secret", body, hashlib.sha256).digest()).decode()
        return self.client.post("/callback", data=body, headers={"X-Line-Signature": signature}, content_type="application/json")

    def message(self, text="やあ"):
        return {"type": "message", "timestamp": 0, "replyToken": "reply-token", "source": {"type": "user", "userId": "test-user"}, "message": {"type": "text", "id": "1", "text": text}}

    def reply_text(self):
        return json.loads(self.sent[-1].body)["messages"][0]["text"]

    def test_verified_message_replies_with_generated_text(self):
        response = self.post([self.message()])
        self.assertEqual((response.status_code, response.text), (200, "OK"))
        (generate,), (reply,) = self.generated, self.sent
        self.assertEqual((generate.url.host, generate.url.path.rsplit("/", 1)[-1]), ("generativelanguage.googleapis.com", "interactions"))
        self.assertEqual(generate.headers["x-goog-api-key"], "test-gemini-key")
        self.assertEqual(json.loads(generate.content), {
            "model": "gemini-3.5-flash-lite",
            "system_instruction": main.SYSTEM_INSTRUCTION,
            "input": "やあ",
            "generation_config": {"max_output_tokens": 400, "thinking_level": "minimal"},
        })
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

    def test_rate_limited_api_replies_busy_without_retrying(self):
        self.gemini_status, self.gemini_body = 429, {"error": {"code": "rate_limit_exceeded", "message": "slow down"}}
        self.assertEqual(self.post([self.message()]).status_code, 200)
        self.assertEqual(self.reply_text(), "ごめん、今ちょっと忙しい")
        self.assertEqual(len(self.generated), 1)

    def test_api_server_error_replies_with_server_error_without_retrying(self):
        self.gemini_status, self.gemini_body = 503, {"error": {"code": "service_unavailable", "message": "overloaded"}}
        self.assertEqual(self.post([self.message()]).status_code, 200)
        self.assertEqual(self.reply_text(), "Server errorだよ。ちょっと待ってね")
        self.assertEqual(len(self.generated), 1)

    def test_exhausted_balance_or_rejected_key_asks_for_the_developer(self):
        for status, code in ((402, "payment_required"), (401, "authentication")):
            with self.subTest(status=status):
                self.gemini_status, self.gemini_body = status, {"error": {"code": code, "message": "rejected"}}
                self.assertEqual(self.post([self.message()]).status_code, 200)
                self.assertEqual(self.reply_text(), "故障かな？ごめんよ、ちょっと開発者呼んできて")

    def test_unreachable_api_preserves_fallback(self):
        self.gemini_unreachable = True
        self.assertEqual(self.post([self.message()]).status_code, 200)
        self.assertEqual(self.reply_text(), "あれ、聞こえてますか？")

    def test_response_without_text_replies_not_understood(self):
        self.gemini_body = {"id": "interaction-1", "status": "incomplete", "steps": [{"type": "thought"}]}
        self.assertEqual(self.post([self.message()]).status_code, 200)
        self.assertEqual(self.reply_text(), "ごめん、ちょっとよく分かんない")

    def test_message_at_length_limit_is_generated_and_longer_one_is_not(self):
        self.assertEqual(self.post([self.message("あ" * 500)]).status_code, 200)
        self.assertEqual((len(self.generated), self.reply_text()), (1, "こんにちは"))
        self.assertEqual(self.post([self.message("あ" * 501)]).status_code, 200)
        self.assertEqual((len(self.generated), self.reply_text()), (1, "ごめん、文章長すぎて読み切れない"))

    def test_sender_over_message_limit_gets_busy_reply_without_generation(self):
        for _ in range(20):
            self.assertEqual(self.post([self.message()]).status_code, 200)
        self.assertEqual((len(self.generated), self.reply_text()), (20, "こんにちは"))
        self.assertEqual(self.post([self.message()]).status_code, 200)
        self.assertEqual((len(self.generated), self.reply_text()), (20, "ごめん、今ちょっと忙しい"))
        other = self.message()
        other["source"]["userId"] = "other-user"
        self.assertEqual(self.post([other]).status_code, 200)
        self.assertEqual((len(self.generated), self.reply_text()), (21, "こんにちは"))

    def test_message_limit_is_restored_after_the_window(self):
        with patch.object(main.time, "monotonic", return_value=1000.0):
            for _ in range(21):
                self.post([self.message()])
        self.assertEqual(self.reply_text(), "ごめん、今ちょっと忙しい")
        with patch.object(main.time, "monotonic", return_value=1000.0 + 601):
            self.post([self.message()])
        self.assertEqual(self.reply_text(), "こんにちは")


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
