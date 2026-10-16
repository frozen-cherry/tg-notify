# 微信通知接入（v2.4）

## 调用约定

只有 `POST /notify` 的 `destinations` 明确包含 `wechat`，才会发送微信。
不传、传 `null`、或只传 `["telegram"]` 均沿用原 Telegram 路径和响应；
`[]`、拼错名称、非列表返回 422，不发送。`channel` 仍是业务分类，不能用 `channel="wechat"` 选择微信。
TradingView `/webhook`、`/test`、旧客户端和便捷函数默认仍只走 Telegram。
服务端配置微信凭据不会改变任何旧调用的路由，不设置全局“默认双发”或失败后自动转微信。

```python
from notify_client import notify

# 不改旧脚本：仍然只发 Telegram
notify("价格提醒", "触发条件", channel="price")

# 明确选择微信；request_id 应由业务事件生成并持久保存
notify("监控提醒", "发现新事件", channel="alert", destinations=["wechat"],
       request_id="my-monitor:event-123")

# 明确双发
notify("监控提醒", "发现新事件", destinations=["telegram", "wechat"],
       request_id="my-monitor:event-124")
```

HTTP JSON：

```json
{
  "title": "监控提醒",
  "message": "发现新事件",
  "channel": "alert",
  "priority": "normal",
  "destinations": ["wechat"],
  "request_id": "my-monitor:event-123"
}
```

仍使用原 `X-API-Key` 鉴权。`request_id` 为 1–128 个 ASCII 字母、数字或 `_.:-`；
不传时服务器生成并返回。不要在请求里指定微信收件人、桥接密钥或 URL。
微信收件人固定为桥接白名单中唯一的已配对用户；白名单不是恰好一人时拒绝发送，绝不猜 `_latest`。

## 回执与重试

显式含微信的调用返回 `request_id` 和逐渠道 `results`：全成功 200/`ok`，部分成功 207/`partial`，全失败 502/`error`。
每项包含 `ok`；微信成功的 `outcome=accepted` 表示微信服务端接受，`message_ids` 是其回执，不能当手机已读证明。
失败的 `outcome=unknown` 表示可能已发出，不可换 ID 盲重发。
`/health` 的 `wechat_configured` 只表示三项配置齐全，不代表会话有效或微信已送达。

`notify()` 仍返回 bool：仅 HTTP 200 为 True，207 也为 False。
需要判断部分成功、保存回执或处理重试时，直接用 requests 调 `/notify` 并读取 JSON；含微信请求客户端超时应至少 50 秒。
服务端 SSH 最长 40 秒、桥接请求最长 25 秒；不自动重试，不自动切换通知渠道。

微信幂等依赖 `request_id`：同一事件、完全相同的正文和优先级，重试使用相同 ID；桥接返回先前回执，不重复发送。
相同 ID 换正文会得到 `bridge_http_409`，发送结果不明的旧记录也返回 409，需先查桥接日志。
此 ID **不对 Telegram 去重**。双发失败时禁止整包自动重试，否则可能重发已成功的 Telegram/重复安排电话。
普通通知确认只需补发微信时，可缩小到 `["wechat"]` 并保留 ID、标题、正文、优先级和分类；
带按钮或 critical 的双发不可这样缩小（格式和能力不同），应人工核查，不自动重放。
需无歧义自动重试的监控宜把 Telegram、微信作为两笔独立投递管理。

## 能力边界

- 第一版微信只发文字（渲染后最多 20000 字符），将常用 Telegram HTML 格式转换为纯文字。
- `chat_id`、`thread_id`、`no_preview` 仅影响 Telegram。微信使用现有 ClawBot 私聊，不创建新 bot 或群。
- 微信单渠道搭配 `critical` 或 `buttons` 返回 422，任何渠道均不发送。需要电话升级/按钮时显式双发。
- 双发的 critical 由 Telegram 原有确认按钮和电话任务负责；微信正文提示到 Telegram 确认，微信回复不能取消电话。
- 用户回复微信仍进入现有 Grok 入站流程，不会路由到发通知的 Codex/脚本。无需经过 Grok 生成通知。
- 微信登录和历史 `context_token` 必须有效；长期不互动能否继续发送取决于微信服务端，不承诺无限期主动推送。

## 运维安装

`wechat_transport.py` 在通知服务器通过 SSH 调用桥接机上的 `deploy/wechat_relay.py`。
不新增监听端口或常驻进程，不依赖临时公网隧道，不复制微信 bot token/发送 API 密钥到通知服务器。

1. 将 relay 安装到桥接机 root 拥有的固定路径，以现有桥接用户运行 `python3 -I /path/wechat_relay.py`。
2. 通知机生成专用 Ed25519 密钥，私钥 0600；从已验证管理通道取得桥接机主机公钥，固定到独立 known_hosts。
3. 在桥接用户 `authorized_keys` 添加该公钥，设置 `restrict,from="NOTIFY_SERVER_IP",command="/usr/bin/python3 -I /path/wechat_relay.py"`。
   该 key 无 shell、PTY、端口转发权限，输入只能是文本和幂等键；relay 忽略 SSH 原始命令。
4. 通知服务 `.env` 配置以下三项，重启现有通知服务一次：

```env
WECHAT_SSH_TARGET=bridge-user@bridge-host
WECHAT_SSH_KEY=/secure/path/wechat_ed25519
WECHAT_SSH_KNOWN_HOSTS=/secure/path/wechat_known_hosts
```

relay 从当前用户 `~/.config/wechat-grok-bridge/env` 读 `SEND_API_SECRET` 和 `WECHAT_ALLOWED_USERS`，
只调用 `http://127.0.0.1:18887/send`。换桥接端口/配置目录需审核 relay 源码，不接受调用方覆盖。
取消集成可移除这三项配置并重启；废弃凭据还需删除对应 authorized_keys 条目、私钥和 relay。
微信现有 receiver、send-api、tunnel 均不需要为此重启。

## 后续 agent 开发要求

- 不扩大微信路由范围；将“未明确指定微信就不发微信”作为回归不变量。
- 修改前读本文件与 INTEGRATION.md，核对生产版本；配置/私钥不进仓库和日志。
- 测试：`python3 -m unittest discover -s tests -v`（模拟所有外部通知，无真实消息或电话）。
- 独立目录验证后部署，避免启动第二个 Telegram 轮询进程；部署前确认无待确认 critical 告警。
- 原服务电话任务与命令队列在内存中，重启会丢失；应选择无待处理命令的时段，勿为此次扩展另起轮询器抢消息。
- 备份原代码与 `.env`，预先安装辅助文件，最后替换入口，单次短重启、健康检查；失败立即回滚。
- 微信真人送达测试需明确授权；未经授权只跑离线测试及不会发送消息的校验。
