"""Offline routing regression tests: all external sends are mocked."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.update(TG_BOT_TOKEN="123456:test-token", TG_CHAT_ID="test-chat", NOTIFY_API_KEY="test-key",
                  TWILIO_SID="", TWILIO_TOKEN="")
import server
import notify_client
import wechat_transport
from fastapi.testclient import TestClient


class Routing(unittest.TestCase):
    def setUp(self):
        self.bot = patch.object(server, "bot", MagicMock(send_message=AsyncMock()))
        self.tg = self.bot.start()
        self.wxpatch = patch.object(wechat_transport, "send", AsyncMock(return_value={"ok": True, "outcome": "accepted", "message_ids": ["wx-id"]}))
        self.wx = self.wxpatch.start()
        self.client = TestClient(server.app)
        self.addCleanup(self.bot.stop)
        self.addCleanup(self.wxpatch.stop)
        server.pending_alerts.clear()

    def send(self, **fields):
        return self.client.post("/notify", headers={"X-API-Key": "test-key"},
                                json={"title": "测试", "message": "price < 100", **fields})

    def test_legacy_exact_response_and_options(self):
        response = self.send(chat_id="group", thread_id=42, no_preview=True,
                             buttons=[{"text": "停止", "target": "watch", "action": "stop"}])
        self.assertEqual(response.json(), {"status": "ok", "message": "Notification sent"})
        kwargs = self.tg.send_message.call_args.kwargs
        self.assertEqual(kwargs["chat_id"], "group")
        self.assertEqual(kwargs["message_thread_id"], 42)
        self.assertTrue(kwargs["link_preview_options"].is_disabled)
        self.assertIsNotNone(kwargs["reply_markup"])
        self.wx.assert_not_called()

    def test_no_explicit_wechat_never_calls_wechat(self):
        for fields in ({}, {"destinations": None}, {"destinations": ["telegram"]}, {"channel": "wechat"}):
            self.assertEqual(self.send(**fields).status_code, 200)
        self.wx.assert_not_called()

    def test_invalid_routing_sends_nothing(self):
        for dest in ([], ["wecaht"], "wechat", ["telegram", "wechat", "wechat"]):
            self.assertEqual(self.send(destinations=dest).status_code, 422)
        self.wx.assert_not_called()
        self.tg.send_message.assert_not_called()

    def test_unauthorized_sends_nothing(self):
        response = self.client.post("/notify", json={"title": "x", "message": "x", "destinations": ["wechat"]})
        self.assertEqual(response.status_code, 401)
        self.wx.assert_not_called()

    def test_wechat_only_and_stable_body(self):
        fields = {"destinations": ["wechat"], "request_id": "watch:123", "title": "<b>告警</b>"}
        self.assertEqual(self.send(**fields).status_code, 200)
        before = self.wx.call_args
        self.send(**fields)
        self.assertEqual(before, self.wx.call_args)
        self.assertIn("price < 100", before.args[0])
        self.assertNotIn("<b>", before.args[0])
        self.tg.send_message.assert_not_called()

    def test_duplicates_do_not_duplicate_sends(self):
        self.send(destinations=["wechat", "wechat"])
        self.wx.assert_awaited_once()

    def test_critical_legacy_still_schedules_phone(self):
        sleeper = AsyncMock()
        with patch.object(server, "delayed_call_check", sleeper):
            result = self.send(priority="critical").json()
        self.assertEqual(result["message"], "Critical notification sent, phone call scheduled")
        self.assertIn(result["alert_id"], server.pending_alerts)
        sleeper.assert_called_once()
        self.wx.assert_not_called()

    def test_wechat_only_rejects_buttons_and_critical_before_send(self):
        self.assertEqual(self.send(destinations=["wechat"], priority="critical").status_code, 422)
        self.assertEqual(self.send(destinations=["wechat"], buttons=[{"text": "stop", "target": "x", "action": "stop"}]).status_code, 422)
        self.wx.assert_not_called()
        self.tg.send_message.assert_not_called()

    def test_dual_critical_retains_tg_ack(self):
        with patch.object(server, "delayed_call_check", AsyncMock()) as phone:
            result = self.send(destinations=["telegram", "wechat"], priority="critical")
        self.assertEqual(result.status_code, 200)
        self.assertIn("alert_id", result.json()["results"]["telegram"])
        self.assertIn("Telegram", self.wx.call_args.args[0])
        phone.assert_called_once()

    def test_dual_partial_failure_independent(self):
        self.wx.return_value = {"ok": False, "error": "bridge_http_409", "outcome": "unknown"}
        response = self.send(destinations=["telegram", "wechat"])
        self.assertEqual(response.status_code, 207)
        self.assertTrue(response.json()["results"]["telegram"]["ok"])
        self.tg.send_message.assert_awaited_once()

    def test_telegram_failure_does_not_block_wechat_or_leak(self):
        self.tg.send_message.side_effect = RuntimeError("private-token")
        response = self.send(destinations=["telegram", "wechat"])
        self.assertEqual(response.status_code, 207)
        self.assertNotIn("private-token", response.text)
        self.wx.assert_awaited_once()

    def test_all_failed_returns_502(self):
        self.wx.return_value = {"ok": False, "error": "wechat_not_configured", "outcome": "not_sent"}
        self.assertEqual(self.send(destinations=["wechat"]).status_code, 502)

    def test_bad_button_or_oversize_prevents_all_sends(self):
        self.assertEqual(self.send(destinations=["telegram", "wechat"], buttons=[{"text": "x", "target": "x|y", "action": "stop"}]).status_code, 400)
        self.assertEqual(self.send(destinations=["telegram", "wechat"], message="x" * 20001).status_code, 422)
        self.wx.assert_not_called()
        self.tg.send_message.assert_not_called()

    def test_legacy_failure_response_unchanged(self):
        self.tg.send_message.side_effect = RuntimeError("fake upstream failure")
        response = self.send()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "fake upstream failure")
        self.wx.assert_not_called()

    def test_tradingview_remains_telegram_only(self):
        response = self.client.post("/webhook/" + server.WEBHOOK_SECRET,
            json={"title": "TV", "message": "x", "destinations": ["wechat"]})
        self.assertEqual(response.status_code, 200)
        self.wx.assert_not_called()


class Client(unittest.TestCase):
    @patch.object(notify_client.requests, "post")
    def test_legacy_payload_unchanged_and_new_opt_in(self, post):
        post.return_value.status_code = 200
        self.assertTrue(notify_client.notify("a", "b"))
        self.assertEqual(post.call_args.kwargs["json"], {"title": "a", "message": "b", "channel": "info", "priority": "normal"})
        self.assertEqual(post.call_args.kwargs["timeout"], 10)
        notify_client.notify("a", "b", destinations=["wechat"], request_id="job:1")
        self.assertEqual(post.call_args.kwargs["json"]["destinations"], ["wechat"])
        self.assertEqual(post.call_args.kwargs["timeout"], 50)
        post.return_value.status_code = 207
        self.assertFalse(notify_client.notify("a", "b", destinations=["telegram", "wechat"]))


class Transport(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_is_unknown_and_no_retry(self):
        proc = MagicMock(returncode=None)
        proc.communicate = AsyncMock(return_value=(b"", b""))
        env = {"WECHAT_SSH_TARGET": "user@host", "WECHAT_SSH_KEY": "/key", "WECHAT_SSH_KNOWN_HOSTS": "/hosts"}
        async def timeout(awaitable, **kwargs):
            await awaitable
            raise asyncio.TimeoutError()
        with patch.dict(os.environ, env), patch.object(asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)) as spawn, patch.object(asyncio, "wait_for", timeout):
            result = await wechat_transport.send("a", "id")
            self.assertEqual(result["outcome"], "unknown")
            proc.kill.assert_called_once()
            spawn.assert_awaited_once()

    async def test_no_configuration_no_process(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(asyncio, "create_subprocess_exec") as spawn:
            self.assertEqual((await wechat_transport.send("a", "id"))["outcome"], "not_sent")
            spawn.assert_not_called()

    async def test_ssh_argv_and_no_retries(self):
        proc = MagicMock(returncode=0)
        proc.communicate = AsyncMock(return_value=(b'{"ok":true,"message_ids":["1"]}', b""))
        env = {"WECHAT_SSH_TARGET": "user@host", "WECHAT_SSH_KEY": "/key", "WECHAT_SSH_KNOWN_HOSTS": "/hosts"}
        with patch.dict(os.environ, env), patch.object(asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)) as spawn:
            self.assertTrue((await wechat_transport.send("a", "id"))["ok"])
            spawn.assert_awaited_once()
            self.assertIn("StrictHostKeyChecking=yes", spawn.call_args.args)
            self.assertNotIn("shell", spawn.call_args.kwargs)
            body = json.loads(proc.communicate.call_args.args[0])
            self.assertEqual(len(body["idempotency_key"]), 74)


class Relay(unittest.TestCase):
    def test_rejects_arbitrary_recipient_and_invalid_key(self):
        path = Path(__file__).resolve().parents[1] / "deploy/wechat_relay.py"
        spec = importlib.util.spec_from_file_location("relay", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for req in ({"text": "x", "idempotency_key": "bad"}, {"text": "x", "idempotency_key": "tg-notify-" + "a" * 64, "to": "someone"}):
            self.assertFalse(module.relay(req)["ok"])


if __name__ == "__main__":
    unittest.main()
