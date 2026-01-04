#!/usr/bin/env python3
"""
简单告警示例
演示如何在你的脚本中使用 notify_client 发送通知
"""

import sys
sys.path.append('..')

from notify_client import notify, notify_critical, call_now

# ========== 基础用法 ==========

# 1. 普通通知
notify("测试标题", "这是一条普通通知")

# 2. 指定频道和优先级
notify(
    title="价格告警",
    message="BTC 跌破 $90,000",
    channel="price",      # 可选: gold, wallet, price, system, alert, trade, info
    priority="normal"     # 可选: normal, high, critical
)

# 3. 高优先级通知（会加红点标记）
notify("大额转账", "检测到 100 ETH 转出", channel="wallet", priority="high")


# ========== 紧急告警 ==========

# 4. Critical 告警 - 5分钟未在 TG 确认会自动打电话
notify_critical("清算警告", "仓位即将被清算！")

# 5. 直接打电话（跳过 TG 确认）
# call_now("服务器崩溃，请立即处理")


# ========== 在你的监控脚本中使用 ==========

def your_monitor_logic():
    """示例：检测到异常时发送通知"""
    
    price = 89000  # 假设从 API 获取的价格
    
    if price < 90000:
        notify(
            title="BTC 跌破阈值",
            message=f"当前价格: ${price:,}",
            channel="price",
            priority="high"
        )
    
    # 如果是紧急情况
    if price < 80000:
        notify_critical(
            title="⚠️ BTC 暴跌",
            message=f"当前价格: ${price:,}\n请立即检查仓位！"
        )


if __name__ == "__main__":
    print("运行示例...")
    your_monitor_logic()
    print("完成")
