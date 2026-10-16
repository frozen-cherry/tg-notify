"""Explicit opt-in WeChat transport. No model calls, shell, or automatic retries."""
import asyncio
import hashlib
import html
import json
import os
import re


def configured():
    return all(os.getenv(k) for k in ("WECHAT_SSH_TARGET", "WECHAT_SSH_KEY", "WECHAT_SSH_KNOWN_HOSTS"))


def plain_text(value):
    # Telegram formatting only; preserve comparisons such as price < 100.
    value = re.sub(r'<a\s+href=[\'\"]([^\'\"]+)[\'\"][^>]*>(.*?)</a>', r'\2 (\1)', value, flags=re.I | re.S)
    return html.unescape(re.sub(r'</?(?:b|strong|i|em|u|ins|s|strike|del|code|pre|blockquote|span|tg-spoiler)(?:\s[^>]*)?>', '', value, flags=re.I))


def render(req):
    text = f"【{req.priority} · {req.channel}】{plain_text(req.title)}\n\n{plain_text(req.message)}"
    if req.priority == "critical":
        text += "\n\n请到 Telegram 确认告警；微信回复不能取消电话。"
    elif req.buttons:
        text += "\n\n操作按钮请到 Telegram 使用。"
    return text


async def send(text, request_id):
    if not configured():
        return {"ok": False, "error": "wechat_not_configured", "outcome": "not_sent"}
    target = os.environ["WECHAT_SSH_TARGET"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+", target):
        return {"ok": False, "error": "invalid_ssh_target", "outcome": "not_sent"}
    key = "tg-notify-" + hashlib.sha256(request_id.encode()).hexdigest()
    payload = json.dumps({"text": text, "idempotency_key": key}, ensure_ascii=False).encode()
    args = ["ssh", "-T", "-F", "/dev/null", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8", "-o", "ConnectionAttempts=1",
            "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2",
            "-o", "UserKnownHostsFile=" + os.environ["WECHAT_SSH_KNOWN_HOSTS"],
            "-i", os.environ["WECHAT_SSH_KEY"], target, "notify"]
    try:
        proc = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.PIPE,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except OSError:
        return {"ok": False, "error": "ssh_unavailable", "outcome": "not_sent"}
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(payload), timeout=40)
    except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
        if proc.returncode is None:
            proc.kill()
        await proc.communicate()
        if isinstance(exc, asyncio.CancelledError):
            raise
        return {"ok": False, "error": "wechat_transport_timeout", "outcome": "unknown"}
    if proc.returncode:
        return {"ok": False, "error": "wechat_transport_failed", "outcome": "unknown"}
    try:
        result = json.loads(stdout)
        if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
            raise ValueError()
        return result
    except (ValueError, UnicodeDecodeError):
        return {"ok": False, "error": "invalid_relay_response", "outcome": "unknown"}
