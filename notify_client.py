"""
通知客户端工具 v2.0
在任何监控脚本中 import 这个模块即可发送通知
"""

import os
from pathlib import Path
import requests

# 尝试加载 .env 文件
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass

# ========== 配置 ==========
PORT = os.getenv("NOTIFY_PORT", "8000")
NOTIFY_SERVER = os.getenv("NOTIFY_SERVER", f"http://localhost:{PORT}")
API_KEY = os.getenv("NOTIFY_API_KEY", "")


def notify(
    title: str,
    message: str,
    channel: str = "info",
    priority: str = "normal"
) -> bool:
    """
    发送通知到 TG Bot
    
    Args:
        title: 通知标题
        message: 通知内容
        channel: 频道/来源 (gold, wallet, price, system, alert, trade, info)
        priority: 优先级
            - "normal": 普通通知
            - "high": 高优先级（加红点标记）
            - "critical": 紧急告警（N分钟未确认会打电话）
    
    Returns:
        bool: 是否发送成功
    """
    try:
        resp = requests.post(
            f"{NOTIFY_SERVER}/notify",
            json={
                "channel": channel,
                "title": title,
                "message": message,
                "priority": priority
            },
            headers={"X-API-Key": API_KEY},
            timeout=10
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"[Notify Error] {e}")
        return False


def notify_critical(title: str, message: str, channel: str = "alert") -> bool:
    """
    发送紧急告警（未确认会打电话）
    
    这是 notify(..., priority="critical") 的快捷方式
    """
    return notify(title, message, channel=channel, priority="critical")


def call_now(message: str = "紧急告警，请查看") -> bool:
    """
    立即拨打电话（跳过 TG 确认）
    
    Args:
        message: 电话中播报的内容
    
    Returns:
        bool: 是否成功
    """
    try:
        resp = requests.post(
            f"{NOTIFY_SERVER}/call",
            params={"message": message},
            headers={"X-API-Key": API_KEY},
            timeout=30
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"[Call Error] {e}")
        return False


# ========== 便捷函数 ==========

def alert(title: str, message: str):
    """发送告警通知"""
    return notify(title, message, channel="alert", priority="high")


def trade_alert(title: str, message: str):
    """发送交易相关通知"""
    return notify(title, message, channel="trade")


def wallet_alert(title: str, message: str):
    """发送钱包相关通知"""
    return notify(title, message, channel="wallet")


def price_alert(title: str, message: str):
    """发送价格相关通知"""
    return notify(title, message, channel="price")


# ========== 测试 ==========

if __name__ == "__main__":
    print("测试通知客户端...")
    print(f"服务器: {NOTIFY_SERVER}")
    print(f"API Key: {API_KEY[:10]}..." if API_KEY else "API Key: 未配置")
    
    # 测试普通通知
    result = notify("测试标题", "这是一条测试消息", channel="system")
    print(f"普通通知: {'✓ 成功' if result else '✗ 失败'}")
