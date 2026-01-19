"""
命令监听客户端 v1.0
在脚本中 import 这个模块即可接收 TG 命令
"""

import os
from pathlib import Path
import requests
import time
from threading import Thread

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
DEFAULT_SERVER = os.getenv("NOTIFY_SERVER", f"http://localhost:{PORT}")


class CommandListener:
    """
    命令监听器
    
    使用示例:
        cmd = CommandListener(target="gold", server="http://your-server:8000")
        
        @cmd.on("stop")
        def handle_stop(args):
            print("收到停止命令")
            exit()
        
        @cmd.on("set_value")
        def handle_set_value(args):
            value = float(args[0])
            print(f"设置值为 {value}")
        
        cmd.start()  # 启动后台轮询
    """
    
    def __init__(self, target: str, server: str = None, poll_interval: float = 5):
        """
        Args:
            target: 脚本标识，如 "gold"、"monitor" 等
            server: 服务器地址，默认从环境变量读取
            poll_interval: 轮询间隔（秒）
        """
        self.target = target
        self.server = server or DEFAULT_SERVER
        self.poll_interval = poll_interval
        self.last_id = 0
        self.handlers = {}
        self._running = False
    
    def on(self, action: str):
        """
        装饰器，注册命令处理函数
        
        Args:
            action: 命令动作名，如 "stop"、"status" 等
        """
        def decorator(func):
            self.handlers[action] = func
            return func
        return decorator
    
    def register(self, action: str, handler):
        """
        直接注册命令处理函数（非装饰器方式）
        
        Args:
            action: 命令动作名
            handler: 处理函数，接收 args 列表
        """
        self.handlers[action] = handler
    
    def poll(self) -> int:
        """
        拉取并处理新命令
        
        Returns:
            int: 处理的命令数量
        """
        try:
            resp = requests.get(
                f"{self.server}/commands",
                params={"target": self.target, "after": self.last_id},
                timeout=10
            )
            data = resp.json()
            commands = data.get("commands", [])
            
            for cmd in commands:
                self.last_id = max(self.last_id, cmd["id"])
                handler = self.handlers.get(cmd["action"])
                if handler:
                    try:
                        handler(cmd["args"])
                    except Exception as e:
                        print(f"[CommandListener] Handler error for '{cmd['action']}': {e}")
                else:
                    print(f"[CommandListener] Unknown action: {cmd['action']}")
            
            return len(commands)
            
        except requests.exceptions.ConnectionError:
            print(f"[CommandListener] Cannot connect to {self.server}")
            return 0
        except Exception as e:
            print(f"[CommandListener] Error: {e}")
            return 0
    
    def start(self):
        """启动后台轮询线程"""
        if self._running:
            return
        
        self._running = True
        
        def loop():
            print(f"[CommandListener] 已启动，target={self.target}, server={self.server}")
            while self._running:
                self.poll()
                time.sleep(self.poll_interval)
        
        Thread(target=loop, daemon=True).start()
    
    def stop(self):
        """停止轮询"""
        self._running = False


# ========== 便捷函数 ==========

def create_listener(target: str, server: str = None, poll_interval: float = 5) -> CommandListener:
    """
    创建并返回一个 CommandListener 实例
    
    这是 CommandListener(target, server, poll_interval) 的快捷方式
    """
    return CommandListener(target, server, poll_interval)


# ========== 测试 ==========

if __name__ == "__main__":
    print("命令监听器测试...")
    print(f"默认服务器: {DEFAULT_SERVER}")
    
    # 创建监听器
    cmd = CommandListener(target="test")
    
    @cmd.on("hello")
    def handle_hello(args):
        print(f"收到 hello 命令，参数: {args}")
    
    @cmd.on("stop")
    def handle_stop(args):
        print("收到 stop 命令，退出...")
        cmd.stop()
        exit()
    
    # 启动轮询
    cmd.start()
    
    print("等待命令... (在 TG 发送 /test hello 或 /test stop)")
    
    # 保持运行
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("已退出")
