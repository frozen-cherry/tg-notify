"""
Telegram 通知机器人服务端 v2.2
支持 HTTP API 通知 + Critical 级别电话告警 + 命令下发
v2.2: 支持多频道/群组/话题目标 + /id 命令查询频道 ID
"""

import os
import json
import asyncio
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header, Request, Query
from pydantic import BaseModel
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update, LinkPreviewOptions
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters
from twilio.rest import Client as TwilioClient
import uvicorn
import threading
import logging

# 加载 .env 文件
env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)

# ========== 配置 ==========
BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
CHAT_ID = os.getenv("TG_CHAT_ID")
API_KEY = os.getenv("NOTIFY_API_KEY")
PORT = int(os.getenv("NOTIFY_PORT", 8000))

# 频道别名表：把好记的名字映射到真实 chat_id
# .env 里配置 TG_CHATS='{"research": -1001234567890, "signal": -100...}'
try:
    CHAT_ALIASES = json.loads(os.getenv("TG_CHATS", "{}"))
    if not isinstance(CHAT_ALIASES, dict):
        CHAT_ALIASES = {}
except Exception:
    CHAT_ALIASES = {}

# Twilio 配置
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_TOKEN = os.getenv("TWILIO_TOKEN")
TWILIO_FROM = os.getenv("TWILIO_FROM")  # 你的 Twilio 号码
PHONE_TO = os.getenv("PHONE_TO")  # 你的手机号（香港号码）

# 告警配置
CALL_DELAY_SECONDS = int(os.getenv("CALL_DELAY_SECONDS", 300))  # 默认 5 分钟

# TradingView Webhook Secret
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "tv_" + os.urandom(8).hex())

# 日志配置
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Channel emoji 映射
CHANNEL_EMOJI = {
    "gold": "🥇",
    "wallet": "👛",
    "price": "💰",
    "system": "⚙️",
    "alert": "🚨",
    "trade": "📈",
    "info": "ℹ️",
}

# 存储待确认的 critical 告警
# {alert_id: {"message": str, "time": float, "confirmed": bool, "chat_id": str, "thread_id": int|None}}
pending_alerts = {}

# ========== 命令系统 ==========
# 命令存储: {target: [cmd1, cmd2, ...]}
commands_store = defaultdict(list)
command_id_counter = 0
commands_lock = threading.Lock()

app = FastAPI(title="TG Notify Server v2.2")
bot = Bot(token=BOT_TOKEN)
twilio_client = None

if TWILIO_SID and TWILIO_TOKEN:
    twilio_client = TwilioClient(TWILIO_SID, TWILIO_TOKEN)
    logger.info("✓ Twilio 已配置")
else:
    logger.warning("⚠ Twilio 未配置，critical 告警将不会打电话")


def resolve_target(chat_id=None):
    """把请求里的 chat_id 解析成真实的 Telegram chat_id。

    - None / 空 → 默认频道 CHAT_ID（保持老行为不变）
    - 命中别名表 → 返回别名对应的真实 id
    - 其它 → 原样返回（当作真实 chat_id，如 -100... 或 @channelusername）
    """
    if chat_id is None or chat_id == "":
        return CHAT_ID
    key = str(chat_id)
    if key in CHAT_ALIASES:
        return str(CHAT_ALIASES[key])
    return key


class NotifyRequest(BaseModel):
    channel: str = "info"
    title: str
    message: str
    priority: str = "normal"  # normal / high / critical
    chat_id: str | None = None   # 目标频道/群组 ID 或别名，不填走默认频道
    thread_id: int | None = None  # 话题（forum topic）ID，可选
    no_preview: bool = False      # True = 不展开链接缩略图（区块浏览器那种预览没用）


def format_message(req: NotifyRequest, alert_id: str = None) -> str:
    """格式化消息"""
    emoji = CHANNEL_EMOJI.get(req.channel, "📢")
    timestamp = datetime.now().strftime("%H:%M:%S")

    priority_mark = ""
    if req.priority == "high":
        priority_mark = "🔴 "
    elif req.priority == "critical":
        priority_mark = "🚨🚨🚨 CRITICAL 🚨🚨🚨\n"

    text = f"""{priority_mark}{emoji} <b>{req.title}</b>

{req.message}

<code>[{req.channel}] {timestamp}</code>"""

    if req.priority == "critical":
        delay_min = CALL_DELAY_SECONDS // 60
        text += f"\n\n⏰ <b>{delay_min} 分钟内未确认将自动拨打电话</b>"

    return text


def make_phone_call(message: str) -> bool:
    """拨打电话"""
    if not twilio_client or not TWILIO_FROM or not PHONE_TO:
        logger.error("Twilio 未正确配置，无法拨打电话")
        return False

    try:
        # TwiML: 语音播报两遍
        twiml = f'''<Response>
            <Say language="zh-CN">注意，紧急告警：{message}</Say>
            <Pause length="2"/>
            <Say language="zh-CN">重复一遍：{message}</Say>
            <Pause length="1"/>
            <Say language="zh-CN">请立即处理</Say>
        </Response>'''

        call = twilio_client.calls.create(
            to=PHONE_TO,
            from_=TWILIO_FROM,
            twiml=twiml
        )
        logger.info(f"📞 电话已拨出: {call.sid}")
        return True
    except Exception as e:
        logger.error(f"拨打电话失败: {e}")
        return False


async def delayed_call_check(alert_id: str, message: str, chat_id: str, thread_id=None):
    """延迟检查是否需要打电话"""
    await asyncio.sleep(CALL_DELAY_SECONDS)

    alert = pending_alerts.get(alert_id)
    if alert and not alert.get("confirmed"):
        logger.info(f"告警 {alert_id} 未确认，准备拨打电话...")

        # 发送即将打电话的通知（发到该告警所在的频道）
        try:
            await bot.send_message(
                chat_id=chat_id,
                text="📞 <b>即将拨打电话...</b>\n\n未在规定时间内确认告警",
                parse_mode="HTML",
                message_thread_id=thread_id
            )
        except:
            pass

        # 打电话
        make_phone_call(message)

        # 清理
        pending_alerts.pop(alert_id, None)


async def handle_callback(update: Update, context):
    """处理 Telegram 按钮回调"""
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("ack_"):
        alert_id = data[4:]

        if alert_id in pending_alerts:
            pending_alerts[alert_id]["confirmed"] = True
            logger.info(f"✓ 告警 {alert_id} 已确认")

            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text("✅ <b>已确认收到，取消电话告警</b>", parse_mode="HTML")

            # 清理
            pending_alerts.pop(alert_id, None)
        else:
            await query.message.reply_text("⚠️ 该告警已过期或已处理")


@app.post("/notify")
async def notify(req: NotifyRequest, x_api_key: str = Header(None)):
    """接收通知并转发到 Telegram"""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key")

    target_chat = resolve_target(req.chat_id)
    thread_id = req.thread_id
    alert_id = f"{int(time.time() * 1000)}"
    text = format_message(req, alert_id)

    try:
        if req.priority == "critical":
            # Critical: 带确认按钮 + 延迟打电话
            keyboard = [[InlineKeyboardButton("✅ 已收到，取消电话", callback_data=f"ack_{alert_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            msg = await bot.send_message(
                chat_id=target_chat,
                text=text,
                parse_mode="HTML",
                reply_markup=reply_markup,
                message_thread_id=thread_id,
                link_preview_options=LinkPreviewOptions(is_disabled=True) if req.no_preview else None
            )

            # 记录待确认告警
            pending_alerts[alert_id] = {
                "message": f"{req.title}: {req.message}",
                "time": time.time(),
                "confirmed": False,
                "chat_id": target_chat,
                "thread_id": thread_id,
            }

            # 启动延迟检查任务
            asyncio.create_task(delayed_call_check(alert_id, f"{req.title}: {req.message}", target_chat, thread_id))

            logger.info(f"🚨 Critical 告警已发送: {alert_id} → {target_chat}")
            return {"status": "ok", "alert_id": alert_id, "message": "Critical notification sent, phone call scheduled"}

        else:
            # Normal/High: 普通发送
            await bot.send_message(
                chat_id=target_chat,
                text=text,
                parse_mode="HTML",
                message_thread_id=thread_id,
                link_preview_options=LinkPreviewOptions(is_disabled=True) if req.no_preview else None
            )
            return {"status": "ok", "message": "Notification sent"}

    except Exception as e:
        logger.error(f"发送失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/call")
async def direct_call(x_api_key: str = Header(None), message: str = "紧急告警，请查看"):
    """直接拨打电话（跳过 TG 确认）"""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key")

    success = make_phone_call(message)
    if success:
        return {"status": "ok", "message": "Phone call initiated"}
    else:
        raise HTTPException(status_code=500, detail="Failed to make call")


@app.get("/health")
async def health():
    """健康检查"""
    return {
        "status": "healthy",
        "twilio_configured": twilio_client is not None,
        "pending_alerts": len(pending_alerts),
        "chat_aliases": list(CHAT_ALIASES.keys()),
    }


@app.get("/test")
async def test_notification():
    """测试接口（无需认证）- 仅用于快速测试"""
    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text="🧪 <b>测试消息</b>\n\n服务运行正常！",
            parse_mode="HTML"
        )
        return {"status": "ok", "message": "Test notification sent"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ========== TradingView Webhook ==========


# ========== 命令系统端点 ==========

async def handle_id_command(update: Update, context):
    """/id —— 返回当前频道/群组/话题的 ID，方便定位新频道。

    把 bot 拉进新群/频道后，在里面（或某个话题里）发 /id，
    bot 会回 chat_id / 类型 / 标题 / thread_id，并附一段可直接复制到
    /notify 请求体的 JSON。命令一定能穿透群隐私模式，最稳。
    """
    chat = update.effective_chat
    msg = update.effective_message
    if chat is None or msg is None:
        return

    thread_id = getattr(msg, "message_thread_id", None)
    is_topic = getattr(msg, "is_topic_message", False)

    lines = [
        "🆔 <b>频道/群组信息</b>",
        f"chat_id: <code>{chat.id}</code>",
        f"类型: <code>{chat.type}</code>",
    ]
    if getattr(chat, "title", None):
        lines.append(f"标题: {chat.title}")
    if thread_id is not None:
        lines.append(f"thread_id(话题): <code>{thread_id}</code>")
        lines.append(f"is_topic: <code>{bool(is_topic)}</code>")

    body = {"chat_id": str(chat.id)}
    if thread_id is not None:
        body["thread_id"] = thread_id
    lines.append("")
    lines.append("发送到这里的示例 body：")
    lines.append(f"<code>{json.dumps(body, ensure_ascii=False)}</code>")

    logger.info(f"🆔 /id 查询: chat={chat.id} type={chat.type} thread={thread_id}")
    await msg.reply_text("\n".join(lines), parse_mode="HTML")


async def handle_tg_command(update: Update, context):
    """处理 TG 发送的命令: /target action [args...]"""
    global command_id_counter

    text = update.message.text
    parts = text.split()

    if len(parts) < 1:
        return

    target = parts[0][1:]  # 去掉 /
    action = parts[1] if len(parts) > 1 else ""
    args = parts[2:] if len(parts) > 2 else []

    if not target or not action:
        await update.message.reply_text("❌ 格式: /target action [args...]")
        return

    # 存储命令
    with commands_lock:
        command_id_counter += 1
        cmd = {
            "id": command_id_counter,
            "target": target,
            "action": action,
            "args": args,
            "ts": int(time.time())
        }
        commands_store[target].append(cmd)
        logger.info(f"📥 收到命令: {target} {action} {args}")

    # 回复确认
    args_str = ' '.join(args) if args else ''
    await update.message.reply_text(f"✓ 命令已发送: {target} {action} {args_str}")


@app.get("/commands")
async def get_commands(
    target: str = Query(..., description="脚本标识"),
    after: int = Query(0, description="只返回 id 大于此值的命令")
):
    """拉取命令（脚本轮询调用）"""
    result = []

    with commands_lock:
        # 返回匹配 target 的命令 + target=all 的命令
        for t in [target, "all"]:
            for cmd in commands_store.get(t, []):
                if cmd["id"] > after:
                    result.append({
                        "id": cmd["id"],
                        "action": cmd["action"],
                        "args": cmd["args"],
                        "ts": cmd["ts"]
                    })

    # 按 id 排序
    result.sort(key=lambda x: x["id"])

    return {"commands": result}


def cleanup_old_commands():
    """定期清理超过 1 小时的旧命令"""
    while True:
        time.sleep(300)  # 每 5 分钟清理一次
        cutoff = int(time.time()) - 3600  # 1 小时前
        with commands_lock:
            for target in list(commands_store.keys()):
                commands_store[target] = [c for c in commands_store[target] if c["ts"] > cutoff]
                if not commands_store[target]:
                    del commands_store[target]
        logger.debug("🧹 命令清理完成")



@app.post("/webhook/{secret}")
async def tradingview_webhook(secret: str, request: Request):
    """
    TradingView Webhook 端点
    兼容 JSON 和纯文本格式
    """
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    # 读取原始 body
    body = await request.body()
    body_str = body.decode('utf-8').strip()

    # 尝试解析 JSON，失败则当纯文本
    title = "TradingView Alert"
    message = body_str
    channel = "trade"
    priority = "normal"

    try:
        data = json.loads(body_str)
        if isinstance(data, dict):
            title = data.get("title", title)
            message = data.get("message", body_str)
            channel = data.get("channel", channel)
            priority = data.get("priority", priority)
    except json.JSONDecodeError:
        # 不是 JSON，直接用原始文本作为 message
        pass

    # 格式化消息
    emoji = CHANNEL_EMOJI.get(channel, "📈")
    timestamp = datetime.now().strftime("%H:%M:%S")
    priority_mark = "🔴 " if priority == "high" else ""

    text = f"""{priority_mark}{emoji} <b>{title}</b>

{message}

<code>[{channel}] {timestamp}</code>"""

    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=text,
            parse_mode="HTML"
        )
        logger.info(f"📈 TradingView webhook: {title}")
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Webhook 发送失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Telegram Bot 轮询处理（用于接收按钮回调 + 命令）
def run_telegram_polling():
    """在单独线程运行 Telegram 轮询"""
    async def start_polling():
        application = Application.builder().token(BOT_TOKEN).build()

        # /id 查询频道 ID（必须在通用命令处理之前，否则会被当成命令派发）
        application.add_handler(CommandHandler("id", handle_id_command))
        # 命令处理（必须在 CallbackQueryHandler 之前）
        application.add_handler(MessageHandler(filters.COMMAND, handle_tg_command))
        application.add_handler(CallbackQueryHandler(handle_callback))

        logger.info("🤖 Telegram Bot 轮询启动...")
        await application.initialize()
        await application.start()
        await application.updater.start_polling(drop_pending_updates=True)

        # 保持运行
        while True:
            await asyncio.sleep(1)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start_polling())


if __name__ == "__main__":
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ 错误: 请先配置 .env 文件")
        print("   复制 .env.example 为 .env 并填写配置")
        exit(1)

    print("=" * 60)
    print("  TG Notify Server v2.2 - 多频道 + 电话告警 + 命令下发")
    print("=" * 60)
    print(f"  Bot Token: {BOT_TOKEN[:20]}...")
    print(f"  默认 Chat ID: {CHAT_ID}")
    print(f"  频道别名: {list(CHAT_ALIASES.keys()) or '（无）'}")
    print(f"  端口: {PORT}")
    print(f"  Twilio: {'✓ 已配置' if twilio_client else '✗ 未配置'}")
    print(f"  电话延迟: {CALL_DELAY_SECONDS} 秒")
    print("-" * 60)
    print(f"  📡 TradingView Webhook:")
    print(f"     http://81.92.219.140/webhook/{WEBHOOK_SECRET}")
    print(f"  📥 命令拉取:")
    print(f"     GET /commands?target=<target>&after=0")
    print(f"  🆔 查询频道 ID: 把 bot 拉进群/频道后在里面发 /id")
    print("=" * 60)

    # 启动命令清理线程
    cleanup_thread = threading.Thread(target=cleanup_old_commands, daemon=True)
    cleanup_thread.start()

    # 启动 Telegram 轮询线程（用于接收按钮回调 + 命令）
    telegram_thread = threading.Thread(target=run_telegram_polling, daemon=True)
    telegram_thread.start()

    # 启动 FastAPI
    uvicorn.run(app, host="0.0.0.0", port=PORT)
