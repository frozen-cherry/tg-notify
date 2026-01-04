#!/usr/bin/env python3
"""
价格涨幅监控器 - 使用 Binance WebSocket 实时监控币种涨幅
监控所有 USDT 永续合约的价格变化

功能：
- 实时订阅所有交易对价格
- 本地计算指定时间窗口的涨跌幅
- 可配置告警条件和优先级
"""

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import aiohttp
import websockets

from notify_client import notify

# ========== 告警配置（可自定义） ==========
ALERT_RULES = [
    # 格式: (时间窗口分钟, 涨幅阈值%, 优先级, 描述)
    # 优先级: "normal", "high", "critical"
    
    # 15分钟涨幅规则
    {"window_minutes": 15, "threshold": 50, "priority": "normal", "name": "15分钟涨幅50%+"},
    {"window_minutes": 15, "threshold": 100, "priority": "critical", "name": "15分钟涨幅100%+"},
    
    # 可以添加更多规则，例如：
    # {"window_minutes": 5, "threshold": 30, "priority": "normal", "name": "5分钟涨幅30%+"},
    # {"window_minutes": 30, "threshold": 80, "priority": "high", "name": "30分钟涨幅80%+"},
]

# ========== 系统配置 ==========
SAMPLE_INTERVAL = 60  # 价格采样间隔（秒），建议 60 秒
MAX_HISTORY_MINUTES = 60  # 最大保存历史（分钟），应大于等于最大时间窗口
ALERT_COOLDOWN = 900  # 同规则同币种告警冷却时间（秒）

# Binance WebSocket
WS_URL = "wss://fstream.binance.com/ws/!miniTicker@arr"

# ========== 数据结构 ==========
@dataclass
class PricePoint:
    timestamp: float
    price: float


class PriceSurgeMonitor:
    def __init__(self):
        # 价格历史: {symbol: deque of PricePoint}
        self.price_history: dict[str, deque] = {}
        # 最新价格: {symbol: price}
        self.latest_prices: dict[str, float] = {}
        # 告警冷却: {(symbol, rule_name): last_alert_time}
        self.alert_cooldowns: dict[tuple, float] = {}
        # 上次采样时间
        self.last_sample_time = 0
        # 运行状态
        self.running = False
        
    def _get_or_create_history(self, symbol: str) -> deque:
        """获取或创建币种的价格历史队列"""
        if symbol not in self.price_history:
            self.price_history[symbol] = deque(maxlen=MAX_HISTORY_MINUTES)
        return self.price_history[symbol]
    
    def _sample_prices(self):
        """采样当前所有币种的价格到历史记录"""
        now = time.time()
        point_time = now
        
        sampled_count = 0
        for symbol, price in self.latest_prices.items():
            history = self._get_or_create_history(symbol)
            history.append(PricePoint(timestamp=point_time, price=price))
            sampled_count += 1
        
        self.last_sample_time = now
        return sampled_count
    
    def _calculate_change(self, symbol: str, window_minutes: int) -> Optional[float]:
        """计算指定时间窗口内的涨跌幅"""
        if symbol not in self.price_history:
            return None
        
        history = self.price_history[symbol]
        if len(history) < window_minutes:
            return None
        
        current_price = self.latest_prices.get(symbol)
        if not current_price:
            return None
        
        # 获取 window_minutes 分钟前的价格
        old_point = history[-window_minutes]
        old_price = old_point.price
        
        if old_price <= 0:
            return None
        
        change_pct = (current_price - old_price) / old_price * 100
        return change_pct
    
    def _can_alert(self, symbol: str, rule_name: str) -> bool:
        """检查是否可以发送告警（防止刷屏）"""
        key = (symbol, rule_name)
        now = time.time()
        last_time = self.alert_cooldowns.get(key, 0)
        
        if now - last_time >= ALERT_COOLDOWN:
            self.alert_cooldowns[key] = now
            return True
        return False
    
    def _check_alerts(self):
        """检查所有规则，触发告警"""
        now_str = datetime.now().strftime("%H:%M:%S")
        alerts_sent = 0
        
        # 按阈值从高到低排序，避免同一币种同时间窗口重复告警
        sorted_rules = sorted(ALERT_RULES, key=lambda r: -r["threshold"])
        
        # 记录已告警的 (symbol, window) 组合
        alerted = set()
        
        for symbol in list(self.latest_prices.keys()):
            for rule in sorted_rules:
                window = rule["window_minutes"]
                threshold = rule["threshold"]
                priority = rule["priority"]
                rule_name = rule["name"]
                
                # 跳过已处理的更高阈值
                if (symbol, window) in alerted:
                    continue
                
                change = self._calculate_change(symbol, window)
                if change is None:
                    continue
                
                # 只关注涨幅（正数）
                if change < threshold:
                    continue
                
                # 标记已处理
                alerted.add((symbol, window))
                
                # 检查冷却
                if not self._can_alert(symbol, rule_name):
                    continue
                
                # 发送告警
                current_price = self.latest_prices[symbol]
                
                if priority == "critical":
                    emoji = "🚨🚨🚨"
                    level_text = "超级暴涨"
                elif priority == "high":
                    emoji = "⚠️"
                    level_text = "大幅上涨"
                else:
                    emoji = "🚀"
                    level_text = "快速拉升"
                
                title = f"{emoji} {symbol} {level_text}！"
                
                # 收集其他时间窗口的涨幅
                changes_info = []
                for w in [5, 15, 30, 60]:
                    c = self._calculate_change(symbol, w)
                    if c is not None:
                        marker = " ⬅️" if w == window else ""
                        changes_info.append(f"  • {w}分钟: {c:+.2f}%{marker}")
                
                message = (
                    f"当前价格: ${current_price:.6g}\n"
                    f"\n"
                    f"📊 涨幅情况:\n"
                    f"{chr(10).join(changes_info)}\n"
                    f"\n"
                    f"📋 触发规则: {rule_name}\n"
                    f"⏰ 检测时间: {now_str}"
                )
                
                print(f"[ALERT] {symbol}: {change:.2f}% ({rule_name}), priority={priority}")
                
                success = notify(
                    title=title,
                    message=message,
                    channel="price",
                    priority=priority
                )
                
                if success:
                    alerts_sent += 1
                    print(f"[OK] 通知已发送: {symbol}")
                else:
                    print(f"[FAIL] 通知发送失败: {symbol}")
        
        return alerts_sent
    
    async def _handle_message(self, data: list):
        """处理 WebSocket 消息"""
        for ticker in data:
            symbol = ticker.get("s", "")
            if not symbol.endswith("USDT"):
                continue
            
            price = float(ticker.get("c", 0))
            if price > 0:
                self.latest_prices[symbol] = price
    
    def _get_top_gainers(self, window_minutes: int, top_n: int = 3) -> list[tuple[str, float]]:
        """获取涨幅最高的币种"""
        gainers = []
        for symbol in self.latest_prices.keys():
            change = self._calculate_change(symbol, window_minutes)
            if change is not None:
                gainers.append((symbol, change))
        
        # 按涨幅排序
        gainers.sort(key=lambda x: x[1], reverse=True)
        return gainers[:top_n]
    
    async def _sample_loop(self):
        """定时采样循环"""
        while self.running:
            await asyncio.sleep(SAMPLE_INTERVAL)
            
            now_str = datetime.now().strftime("%H:%M:%S")
            sampled = self._sample_prices()
            
            # 检查告警
            alerts = self._check_alerts()
            
            # 状态输出
            history_len = 0
            if self.price_history:
                history_len = len(next(iter(self.price_history.values())))
            
            print(f"\n[{now_str}] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print(f"  📊 采样: {sampled} 个币种 | 历史: {history_len}/{MAX_HISTORY_MINUTES} 分钟")
            
            # 显示 15 分钟涨幅 Top 3
            if history_len >= 15:
                top_gainers = self._get_top_gainers(15, 3)
                if top_gainers:
                    print(f"  🔥 15分钟涨幅 Top 3:")
                    for symbol, change in top_gainers:
                        status = "⚠️" if change >= 30 else ""
                        print(f"      {symbol}: {change:+.2f}% {status}")
                
                # 告警状态
                if alerts > 0:
                    print(f"  🚨 已发送 {alerts} 条告警")
                else:
                    print(f"  ✅ 暂无币种触发告警阈值 (需 ≥{ALERT_RULES[0]['threshold']}%)")
            else:
                remaining = 15 - history_len
                print(f"  ⏳ 等待历史数据积累中... (还需 {remaining} 分钟)")
    
    async def _websocket_loop(self):
        """WebSocket 连接循环"""
        while self.running:
            try:
                print(f"[WS] 正在连接 Binance WebSocket...")
                async with websockets.connect(WS_URL, ping_interval=20) as ws:
                    print(f"[WS] 连接成功！")
                    
                    async for message in ws:
                        if not self.running:
                            break
                        
                        try:
                            data = json.loads(message)
                            await self._handle_message(data)
                        except json.JSONDecodeError:
                            pass
                        
            except Exception as e:
                print(f"[WS] 连接断开: {e}")
                if self.running:
                    print(f"[WS] 5秒后重连...")
                    await asyncio.sleep(5)
    
    async def run(self):
        """启动监控"""
        print("=" * 60)
        print("🔍 价格涨幅监控器启动 (WebSocket 版本)")
        print(f"   采样间隔: {SAMPLE_INTERVAL}s")
        print(f"   历史深度: {MAX_HISTORY_MINUTES} 分钟")
        print(f"   告警冷却: {ALERT_COOLDOWN}s")
        print()
        print("📋 告警规则:")
        for rule in ALERT_RULES:
            print(f"   • {rule['name']}: {rule['window_minutes']}分钟涨幅>={rule['threshold']}% -> {rule['priority']}")
        print("=" * 60)
        print()
        
        # 发送启动通知
        rules_text = "\n".join([f"• {r['name']}" for r in ALERT_RULES])
        notify(
            title="🟢 价格涨幅监控器已启动",
            message=(
                f"模式: WebSocket 实时监控\n"
                f"采样间隔: {SAMPLE_INTERVAL}s\n"
                f"\n"
                f"📋 告警规则:\n{rules_text}"
            ),
            channel="price",
            priority="normal"
        )
        
        self.running = True
        
        try:
            # 并行运行 WebSocket 和采样循环
            await asyncio.gather(
                self._websocket_loop(),
                self._sample_loop()
            )
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
            print("\n[INFO] 监控已停止")
            notify(
                title="🔴 价格涨幅监控器已停止",
                message="监控程序已手动停止",
                channel="price",
                priority="normal"
            )


def main():
    monitor = PriceSurgeMonitor()
    try:
        asyncio.run(monitor.run())
    except KeyboardInterrupt:
        print("\n[INFO] 收到退出信号")


if __name__ == "__main__":
    main()
