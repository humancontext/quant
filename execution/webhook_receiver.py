"""
TradingView Webhook 接收器

接收 PineScript 策略发出的交易信号，自动执行：
  分析 → 风控 → 下单 → 记录

启动方式:
  python3 webhook_receiver.py

TradingView Alert Webhook URL:
  http://your-server:8888/webhook

Alert Message 格式:
  {
    "secret": "quant_lobster_2026",
    "symbol": "BTC/USDT",
    "action": "long",
    "price": "{{close}}",
    "stop_loss": "{{plot_0}}",
    "take_profit": "{{plot_1}}",
    "strategy": "ema_cross",
    "timeframe": "4h"
  }
"""
import json
import os
import sys
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config.settings import WEBHOOK, RISK
from execution.okx_trader import OKXTrader
from execution.risk_manager import RiskManager


# 全局交易器和风控
trader = None
risk_mgr = None
trade_log = []


class WebhookHandler(BaseHTTPRequestHandler):
    """处理 TradingView Webhook 请求"""

    def do_POST(self):
        if self.path != '/webhook':
            self._respond(404, {'error': 'Not found'})
            return

        try:
            # 读取请求体
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')

            # 解析 JSON
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                # 尝试解析 TradingView 的简单格式
                data = {'raw': body}

            print(f"\n{'='*60}")
            print(f"📡 收到 Webhook 信号 @ {datetime.now().strftime('%H:%M:%S')}")
            print(f"📦 数据: {json.dumps(data, ensure_ascii=False)}")

            # 验证密钥
            if data.get('secret') != WEBHOOK['secret']:
                print("❌ 密钥验证失败")
                self._respond(403, {'error': 'Invalid secret'})
                return

            # 处理信号
            result = process_signal(data)

            self._respond(200, result)

        except Exception as e:
            print(f"❌ Webhook 处理异常: {e}")
            self._respond(500, {'error': str(e)})

    def _respond(self, code, data):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def log_message(self, format, *args):
        # 静默默认日志
        pass


def process_signal(data: dict) -> dict:
    """
    处理交易信号的核心逻辑

    Signal → Risk Check → Execute → Log
    """
    global trader, risk_mgr, trade_log

    symbol = data.get('symbol', 'BTC/USDT')
    action = data.get('action', '').lower()  # long / short / close_long / close_short
    price = float(data.get('price', 0))
    stop_loss = float(data.get('stop_loss', 0)) if data.get('stop_loss') else None
    take_profit = float(data.get('take_profit', 0)) if data.get('take_profit') else None
    strategy = data.get('strategy', 'unknown')
    confidence = float(data.get('confidence', 0.7))

    print(f"\n🔍 解析信号:")
    print(f"   标的: {symbol} | 动作: {action} | 策略: {strategy}")
    if price:
        print(f"   价格: ${price:,.2f}")
    if stop_loss:
        print(f"   止损: ${stop_loss:,.2f}")
    if take_profit:
        print(f"   止盈: ${take_profit:,.2f}")

    # ============ 平仓信号 ============
    if action in ('close_long', 'close_short', 'close'):
        direction = 'long' if action in ('close_long', 'close') else 'short'
        print(f"🔒 执行平仓: {symbol} {direction}")
        result = trader.close_position(symbol, direction)
        if result:
            risk_mgr.record_trade(symbol, direction, 'close', 0, price)
            trade_log.append({
                'time': datetime.now().isoformat(),
                'symbol': symbol,
                'action': action,
                'result': 'success',
            })
            return {'status': 'closed', 'order': result}
        else:
            return {'status': 'close_failed', 'error': '平仓失败'}

    # ============ 开仓信号 ============
    direction = 'long' if action == 'long' else 'short'

    # 1. 获取实时价格（如果 webhook 没带价格）
    if not price:
        price = trader.get_market_price(symbol)
        if not price:
            return {'status': 'error', 'error': '无法获取价格'}
        print(f"   实时价格: ${price:,.2f}")

    # 2. 风控检查
    can_trade, reason = risk_mgr.check_can_trade(
        symbol, direction, price, stop_loss or 0, take_profit
    )
    print(f"\n🛡️ 风控检查: {reason}")

    if not can_trade:
        trade_log.append({
            'time': datetime.now().isoformat(),
            'symbol': symbol,
            'action': action,
            'result': 'rejected',
            'reason': reason,
        })
        return {'status': 'rejected', 'reason': reason}

    # 3. 计算仓位
    if stop_loss:
        position = risk_mgr.calculate_position(symbol, price, stop_loss, confidence)
        print(f"\n📐 建议仓位:")
        print(f"   数量: {position['coin_amount']} {symbol.split('/')[0]}")
        print(f"   价值: ${position['position_value_usdt']:.2f} USDT")
        print(f"   杠杆: {position['leverage_needed']}x")
        amount_usdt = position['position_value_usdt']
    else:
        # 没有止损价，使用固定仓位（1% 风险）
        amount_usdt = RISK['total_capital_usdt'] * 0.01 / RISK['max_single_risk_pct']
        amount_usdt = min(amount_usdt, RISK['total_capital_usdt'] * 0.3)

    # 4. 执行下单
    print(f"\n🚀 执行下单...")
    result = trader.open_position(
        symbol=symbol,
        direction=direction,
        amount=amount_usdt,
        leverage=RISK['default_leverage'],
        stop_loss=stop_loss,
        take_profit=take_profit,
    )

    if result:
        risk_mgr.record_trade(symbol, direction, f'open_{direction}', amount_usdt, price)
        trade_log.append({
            'time': datetime.now().isoformat(),
            'symbol': symbol,
            'action': action,
            'price': price,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'amount_usdt': amount_usdt,
            'result': 'success',
            'order_id': result.get('id'),
        })

        # 保存交易日志
        _save_trade_log(trade_log)

        print(f"✅ 交易执行成功!")
        print(f"{'='*60}\n")
        return {'status': 'executed', 'order': result}
    else:
        trade_log.append({
            'time': datetime.now().isoformat(),
            'symbol': symbol,
            'action': action,
            'result': 'failed',
        })
        return {'status': 'execution_failed', 'error': '下单失败'}


def _save_trade_log(log):
    """保存交易日志到文件"""
    log_dir = os.path.join(os.path.dirname(__file__), '..', 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, 'trades.json')
    with open(log_file, 'w') as f:
        json.dump(log[-100:], f, ensure_ascii=False, indent=2)  # 最近100条


def run_server():
    """启动 Webhook 服务器"""
    global trader, risk_mgr

    print("=" * 60)
    print("🦞 TradingView Webhook 交易服务器")
    print("=" * 60)

    # 初始化交易器和风控
    trader = OKXTrader()
    risk_mgr = RiskManager()

    # 测试连接
    if not trader.test_connection():
        print("\n❌ OKX 连接失败，请检查 API 配置")
        return

    # 显示风控状态
    status = risk_mgr.get_status()
    print(f"\n💰 资金: ${status['current_capital']:.2f} USDT")
    print(f"📊 今日盈亏: ${status['daily_pnl']:+.2f}")
    print(f"🛡️ 最大持仓: {status['max_positions']} | 最大回撤: {RISK['max_drawdown_pct']:.0%}")

    # 启动服务器
    server = HTTPServer((WEBHOOK['host'], WEBHOOK['port']), WebhookHandler)
    print(f"\n🚀 Webhook 服务器已启动: http://{WEBHOOK['host']}:{WEBHOOK['port']}/webhook")
    print(f"🔑 密钥: {WEBHOOK['secret']}")
    print(f"📡 等待 TradingView 信号...\n")
    print("停止: Ctrl+C\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\n⏹️ 服务器已停止")
        server.server_close()


if __name__ == '__main__':
    run_server()
