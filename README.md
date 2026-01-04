# TG Notify - Telegram 通知 + 电话告警

通用 Telegram 通知服务，支持多级告警。当紧急告警未在指定时间内确认时，自动拨打电话。

## 功能

- 📨 **Telegram 通知** - 通过 HTTP API 发送 TG 消息
- 🔴 **多级优先级** - normal / high / critical
- 📞 **电话告警** - Critical 告警未确认自动打电话（Twilio）
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

## 在你的脚本中使用

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

### Channel 类型

| Channel | Emoji | 用途 |
|---------|-------|------|
| `price` | 💰 | 价格相关 |
| `wallet` | 👛 | 钱包/转账 |
| `trade` | 📈 | 交易相关 |
| `alert` | 🚨 | 通用告警 |
| `system` | ⚙️ | 系统通知 |
| `info` | ℹ️ | 一般信息 |

## API

### POST /notify

发送通知到 Telegram

```bash
curl -X POST http://localhost:8000/notify \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"title": "标题", "message": "内容", "priority": "normal"}'
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

## License

MIT
