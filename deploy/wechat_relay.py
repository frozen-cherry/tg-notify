"""Forced SSH command on bridge host. Fixed recipient; credentials stay here.

Install as a root-owned file. Invoke as bridge owner with python3 -I.
No command-line/environment-controlled URL, recipient, attachments or shell.
"""
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request


def relay(req):
    if not isinstance(req, dict) or set(req) != {"text", "idempotency_key"}:
        return {"ok": False, "error": "invalid_request", "outcome": "not_sent"}
    text, key = req["text"], req["idempotency_key"]
    if not isinstance(text, str) or not text.strip() or len(text) > 20000:
        return {"ok": False, "error": "invalid_text", "outcome": "not_sent"}
    if not isinstance(key, str) or not re.fullmatch(r"tg-notify-[a-f0-9]{64}", key):
        return {"ok": False, "error": "invalid_key", "outcome": "not_sent"}
    config = {}
    for line in (Path.home() / ".config/wechat-grok-bridge/env").read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            config[name] = value
    allowed = set(config.get("WECHAT_ALLOWED_USERS", "").split(",")) - {""}
    # Fail closed if pairing becomes ambiguous. Never use _latest.
    if len(allowed) != 1 or not config.get("SEND_API_SECRET"):
        return {"ok": False, "error": "recipient_not_configured", "outcome": "not_sent"}
    body = json.dumps({"to": next(iter(allowed)), "text": text}, ensure_ascii=False).encode()
    request = urllib.request.Request("http://127.0.0.1:18887/send", data=body, headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + config["SEND_API_SECRET"],
        "Idempotency-Key": key})
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=25) as response:
            result = json.load(response)
        if result.get("ok") is not True:
            return {"ok": False, "error": "bridge_rejected", "outcome": "unknown"}
        return {"ok": True, "outcome": "accepted", "message_ids": result.get("message_ids", [])}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": "bridge_http_" + str(exc.code),
                "outcome": "not_sent" if exc.code in (400, 401, 403, 413, 429) else "unknown"}
    except Exception:
        return {"ok": False, "error": "bridge_unavailable", "outcome": "unknown"}


def main():
    os.umask(0o077)
    raw = sys.stdin.buffer.read(128 * 1024 + 1)
    try:
        if len(raw) > 128 * 1024:
            raise ValueError()
        result = relay(json.loads(raw))
    except Exception:
        result = {"ok": False, "error": "relay_error", "outcome": "not_sent"}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
