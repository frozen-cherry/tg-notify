# TG Notify 集成指南

## 快速开始

### 1. 复制文件

将以下文件复制到你的项目中：

- `notify_client.py` - 发送通知
- `command_listener.py` - 接收命令

### 2. 配置

在项目根目录创建 `.env` 文件：

```env
NOTIFY_SERVER=http://your-server:8000
NOTIFY_API_KEY=your-api-key
```

## 发送通知

```python
from notify_client import notify

# 基本用法
notify("标题", "消息内容")

# 指定频道和优先级
notify("价格告警", "BTC 突破 100k", channel="price", priority="high")

# 紧急告警（未确认会打电话）
notify("系统崩溃", "API 无响应", priority="critical")
```

**可用频道**: `gold`, `wallet`, `price`, `system`, `alert`, `trade`, `info`

**优先级**: `normal`, `high`, `critical`

## 接收命令

```python
from command_listener import CommandListener

cmd = CommandListener(target="mybot")  # target 是脚本标识

@cmd.on("status")
def handle_status(args):
    notify("MyBot", "运行正常")

@cmd.on("set")
def handle_set(args):
    value = args[0] if args else "空"
    notify("MyBot", f"设置为: {value}")

cmd.start()  # 启动后台轮询

# 主循环
while True:
    do_your_work()
```

在 Telegram 发送 `/mybot status` 或 `/mybot set 123` 即可触发。

## 完整示例

参考 `examples/command_demo.py`
