# TG Notify - Telegram 通知 + 电话告警 + 命令下发

通用 Telegram 通知服务，支持多级告警和命令下发。当紧急告警未在指定时间内确认时，自动拨打电话。

## 功能

- 📨 **Telegram 通知** - 通过 HTTP API 发送 TG 消息
- 🔴 **多级优先级** - normal / high / critical
- 📢 **多频道/群组/话题** - 同一个 bot 可发到不同频道、群组、forum 话题
- 🆔 **一键查 ID** - 把 bot 拉进新群后发 `/id`，自动返回 chat_id / 话题 ID
- 📞 **电话告警** - Critical 告警未确认自动打电话（Twilio）
- 📥 **命令下发** - 在 TG 发送命令控制脚本
- 🔌 **易于集成** - import 即可使用的客户端 SDK

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

```bash
cp .env.example .env
# 编辑 .env 填写你的配置
```

必填项：

- `TG_BOT_TOKEN` - Telegram Bot Token
- `TG_CHAT_ID` - 你的 Chat ID
- `NOTIFY_API_KEY` - API 密钥（自定义）

电话告警（可选）：

- `TWILIO_SID` / `TWILIO_TOKEN` - Twilio 凭证
- `TWILIO_FROM` - Twilio 号码
- `PHONE_TO` - 接收电话的号码

### 3. 启动服务

```bash
python server.py
```

## 发送通知

```python
from notify_client import notify, notify_critical, call_now

# 普通通知
notify("标题", "内容", channel="price")

# 高优先级（TG 消息加红点）
notify("大额转账", "100 ETH 转出", channel="wallet", priority="high")

# 紧急告警（5分钟未确认自动打电话）
notify_critical("清算警告", "仓位即将被清算！")

# 直接打电话
call_now("服务器崩溃")
```

## 发到其它频道 / 群组 / 话题

默认不带 `chat_id` 的通知都发到 `.env` 里的 `TG_CHAT_ID`（老行为不变）。
要发到别的地方，传 `chat_id`（可选再传 `thread_id` 指定 forum 话题）：

```python
from notify_client import notify, notify_to

# 发到指定频道/群组（chat_id 传数字 ID，或 .env TG_CHATS 里配的别名）
notify_to("-1001234567890", "标题", "内容", channel="trade")
notify_to("research", "标题", "内容")          # 用别名（需在 .env TG_CHATS 配好）

# 发到群里的某个话题（forum topic）
notify("标题", "内容", chat_id="-1001234567890", thread_id=42)
```

### 怎么拿新频道/群组的 chat_id

1. 把 bot（如 `@banana_monitor_bot`）拉进目标频道/群组，给管理员权限
2. 在该群里（或某个话题里）发送 **`/id`**
3. bot 会回复 `chat_id`、类型、标题、`thread_id`（在话题里发就带），并附一段可
   直接复制进 `/notify` 请求体的 JSON

> 用 `/id` 命令而不是随便发消息：群里 bot 隐私模式默认开着，普通消息收不到，
> 但以 `/` 开头的命令一定能收到，最稳，不用去 BotFather 改设置。

拿到 ID 后，两种用法：直接把数字 ID 当 `chat_id` 传；或写进 `.env` 的 `TG_CHATS`
别名表（`{"research": -100...}`）后用别名传，重启服务生效。

### Channel 类型

| Channel  | Emoji | 用途      |
| -------- | ----- | --------- |
| `price`  | 💰    | 价格相关  |
| `wallet` | 👛    | 钱包/转账 |
| `trade`  | 📈    | 交易相关  |
| `alert`  | 🚨    | 通用告警  |
| `system` | ⚙️    | 系统通知  |
| `info`   | ℹ️    | 一般信息  |

## 接收命令

在 Telegram 发送 `/target action [args]` 格式的命令，脚本轮询接收。

```python
from command_listener import CommandListener

cmd = CommandListener(target="mybot")

@cmd.on("status")
def handle_status(args):
    notify("MyBot", "运行正常")

@cmd.on("set")
def handle_set(args):
    notify("MyBot", f"设置为: {args[0]}")

cmd.start()  # 启动后台轮询
```

TG 命令示例：

- `/mybot status` → 触发 handle_status
- `/mybot set 100` → 触发 handle_set，args=["100"]
- `/all ping` → 广播给所有脚本

## API

### POST /notify

发送通知到 Telegram

```bash
curl -X POST http://localhost:8000/notify \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"title": "标题", "message": "内容", "priority": "normal"}'
```

请求体字段：

| 字段        | 必填 | 说明                                              |
| ----------- | ---- | ------------------------------------------------- |
| `title`     | 是   | 标题                                              |
| `message`   | 是   | 内容                                              |
| `channel`   | 否   | 频道类型（emoji 标记），默认 `info`               |
| `priority`  | 否   | `normal` / `high` / `critical`，默认 `normal`     |
| `chat_id`   | 否   | 目标频道/群组 ID 或 `TG_CHATS` 别名；不填走默认频道 |
| `thread_id` | 否   | forum 话题 ID，发到群里的某个话题                 |

### GET /commands

拉取命令（脚本轮询调用）

```bash
curl "http://localhost:8000/commands?target=mybot&after=0"
```

### POST /call

直接拨打电话

```bash
curl -X POST "http://localhost:8000/call?message=紧急" \
  -H "X-API-Key: your-key"
```

### GET /health

健康检查（无需认证）

## 告警流程

```
监控脚本检测到异常
        ↓
发送 priority=critical 通知
        ↓
  TG 收到消息（带确认按钮）
        ↓
   ┌────┴────┐
   ↓         ↓
点击确认    未点击
   ↓         ↓
取消电话   5分钟后打电话
```

## 示例

查看 [examples/](./examples/) 目录获取更多使用示例。

详细集成指南请参考 [INTEGRATION.md](./INTEGRATION.md)

## License

MIT
