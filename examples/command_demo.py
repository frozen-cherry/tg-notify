"""
命令系统演示脚本
展示如何同时使用 notify_client 发送通知 和 command_listener 接收命令
"""

import time
import sys
import os

# 添加父目录到 path，以便导入模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from notify_client import notify
from command_listener import CommandListener

# ========== 配置 ==========
TARGET = "demo"  # 脚本标识，在 TG 发送 /demo xxx 来控制这个脚本
MONITOR_VALUE = 100  # 模拟的监控值

# ========== 初始化命令监听器 ==========
cmd = CommandListener(target=TARGET)


# ========== 命令处理 ==========

@cmd.on("status")
def handle_status(args):
    """处理 /demo status 命令"""
    notify(
        title="Demo Status",
        message=f"✅ 脚本运行中\n📊 当前值: {MONITOR_VALUE}",
        channel="system"
    )


@cmd.on("set_value")
def handle_set_value(args):
    """处理 /demo set_value <value> 命令"""
    global MONITOR_VALUE
    
    if not args:
        notify("Demo", "❌ 缺少参数，用法: /demo set_value <value>", channel="system")
        return
    
    try:
        MONITOR_VALUE = float(args[0])
        notify(
            title="Demo Value Updated",
            message=f"✅ 值已更新为: {MONITOR_VALUE}",
            channel="system"
        )
    except ValueError:
        notify("Demo", f"❌ 无效的数值: {args[0]}", channel="system")


@cmd.on("alert")
def handle_alert(args):
    """处理 /demo alert <message> 命令 - 发送测试告警"""
    message = ' '.join(args) if args else "这是一条测试告警"
    notify(
        title="Demo Alert",
        message=message,
        channel="alert",
        priority="high"
    )


@cmd.on("stop")
def handle_stop(args):
    """处理 /demo stop 命令"""
    notify("Demo", "🛑 收到停止命令，正在退出...", channel="system")
    cmd.stop()
    print("已退出")
    exit(0)


@cmd.on("ping")
def handle_ping(args):
    """处理 /demo ping 或 /all ping 命令"""
    notify("Demo", "🏓 Pong!", channel="system")


# ========== 主程序 ==========

def main():
    print("=" * 50)
    print("  命令系统演示脚本")
    print("=" * 50)
    print(f"  Target: {TARGET}")
    print(f"  初始值: {MONITOR_VALUE}")
    print("-" * 50)
    print("  可用命令:")
    print("    /demo status      - 查看状态")
    print("    /demo set_value N - 设置值")
    print("    /demo alert MSG   - 发送测试告警")
    print("    /demo ping        - 测试连通性")
    print("    /demo stop        - 停止脚本")
    print("    /all ping         - 广播 ping (所有脚本)")
    print("=" * 50)
    
    # 启动命令监听
    cmd.start()
    
    # 发送启动通知
    notify(
        title="Demo Started",
        message=f"🚀 脚本已启动\ntarget: {TARGET}\n初始值: {MONITOR_VALUE}",
        channel="system"
    )
    
    # 模拟主监控循环
    print("\n脚本运行中，按 Ctrl+C 退出...")
    
    try:
        loop_count = 0
        while True:
            # 模拟监控逻辑
            loop_count += 1
            
            # 每 30 秒打印一次状态
            if loop_count % 6 == 0:
                print(f"[{time.strftime('%H:%M:%S')}] 运行中... 当前值: {MONITOR_VALUE}")
            
            time.sleep(5)
            
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，退出...")
        notify("Demo", "🛑 脚本被手动停止", channel="system")


if __name__ == "__main__":
    main()
