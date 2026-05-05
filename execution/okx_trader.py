"""
OKX 交易执行模块

支持：市价单、限价单、止损止盈、仓位查询、余额查询
模拟盘/实盘切换
"""
import ccxt
import json
import time
import os
import sys
from datetime import datetime
from typing import Optional, Dict, Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config.settings import (
    OKX_API_KEY, OKX_SECRET, OKX_PASSPHRASE,
    OKX_DEMO_MODE, PROXY, SYMBOLS
)


class OKXTrader:
    """OKX 交易执行器"""

    def __init__(self, demo_mode=None):
        self.demo_mode = demo_mode if demo_mode is not None else OKX_DEMO_MODE
        self.exchange = self._init_exchange()
        self._log(f"交易器初始化 | 模式: {'模拟盘' if self.demo_mode else '实盘'}")

    def _init_exchange(self):
        """初始化 OKX 交易所连接"""
        config = {
            'apiKey': OKX_API_KEY,
            'secret': OKX_SECRET,
            'password': OKX_PASSPHRASE,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'swap',  # 合约
            },
        }

        # 代理
        if PROXY:
            config['proxies'] = {
                'http': PROXY,
                'https': PROXY,
            }
            config['aiohttp_proxy'] = PROXY

        exchange = ccxt.okx(config)

        # 模拟盘设置
        if self.demo_mode:
            exchange.set_sandbox_mode(True)
            # OKX 模拟盘需要设置 demo 标志
            exchange.options['demo'] = True

        return exchange

    def _log(self, msg):
        """日志输出"""
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{ts}] {msg}")

    # ============ 账户信息 ============

    def get_balance(self) -> Dict:
        """查询账户余额"""
        try:
            balance = self.exchange.fetch_balance()
            usdt = balance.get('USDT', {})
            result = {
                'total': usdt.get('total', 0),
                'free': usdt.get('free', 0),
                'used': usdt.get('used', 0),
                'all': balance,
            }
            self._log(f"💰 余额: 总计 {result['total']:.2f} USDT | 可用 {result['free']:.2f} USDT | 占用 {result['used']:.2f} USDT")
            return result
        except Exception as e:
            self._log(f"❌ 查询余额失败: {e}")
            return {'total': 0, 'free': 0, 'used': 0, 'error': str(e)}

    def get_positions(self) -> list:
        """查询当前持仓"""
        try:
            positions = self.exchange.fetch_positions()
            active = []
            for pos in positions:
                if float(pos.get('contracts', 0)) > 0:
                    active.append({
                        'symbol': pos['symbol'],
                        'side': pos['side'],
                        'contracts': float(pos['contracts']),
                        'entry_price': float(pos.get('entryPrice', 0)),
                        'unrealized_pnl': float(pos.get('unrealizedPnl', 0)),
                        'leverage': pos.get('leverage', 1),
                        'liquidation_price': pos.get('liquidationPrice', None),
                    })
            if active:
                self._log(f"📊 当前持仓: {len(active)} 个")
                for p in active:
                    pnl_sign = '+' if p['unrealized_pnl'] >= 0 else ''
                    self._log(f"   {p['symbol']} {p['side']} | 数量: {p['contracts']} | 入场: ${p['entry_price']:.2f} | 浮盈: {pnl_sign}{p['unrealized_pnl']:.4f}")
            else:
                self._log("📊 当前无持仓")
            return active
        except Exception as e:
            self._log(f"❌ 查询持仓失败: {e}")
            return []

    # ============ 交易执行 ============

    def set_leverage(self, symbol: str, leverage: int):
        """设置杠杆"""
        try:
            # ccxt unified 的 set_leverage
            self.exchange.set_leverage(leverage, symbol)
            self._log(f"🔧 {symbol} 杠杆设置为 {leverage}x")
            return True
        except Exception as e:
            self._log(f"⚠️ 设置杠杆失败（可能已设置）: {e}")
            return False

    def market_order(self, symbol: str, side: str, amount: float,
                     params: Dict = None) -> Optional[Dict]:
        """
        市价单

        Args:
            symbol: 交易对 'BTC/USDT'
            side: 'buy' or 'sell'
            amount: 下单数量（币）
            params: 额外参数
        """
        try:
            # OKX 合约需要 swap 后缀
            okx_symbol = symbol if '/' in symbol else f"{symbol}/USDT:USDT"

            self._log(f"📤 市价{'买入' if side == 'buy' else '卖出'}: {okx_symbol} | 数量: {amount}")

            order = self.exchange.create_order(
                symbol=okx_symbol,
                type='market',
                side=side,
                amount=amount,
                params=params or {},
            )

            self._log(f"✅ 下单成功 | ID: {order['id']} | 成交价: {order.get('average', 'N/A')} | 状态: {order['status']}")
            return self._format_order(order)

        except Exception as e:
            self._log(f"❌ 下单失败: {e}")
            return None

    def limit_order(self, symbol: str, side: str, amount: float, price: float,
                    params: Dict = None) -> Optional[Dict]:
        """限价单"""
        try:
            okx_symbol = symbol if '/' in symbol else f"{symbol}/USDT:USDT"

            self._log(f"📤 限价{'买入' if side == 'buy' else '卖出'}: {okx_symbol} | 数量: {amount} | 价格: ${price:.2f}")

            order = self.exchange.create_order(
                symbol=okx_symbol,
                type='limit',
                side=side,
                amount=amount,
                price=price,
                params=params or {},
            )

            self._log(f"✅ 限价单已挂 | ID: {order['id']} | 状态: {order['status']}")
            return self._format_order(order)

        except Exception as e:
            self._log(f"❌ 限价单失败: {e}")
            return None

    def open_position(self, symbol: str, direction: str, amount: float,
                      leverage: int = 2, stop_loss: float = None,
                      take_profit: float = None) -> Optional[Dict]:
        """
        开仓（带止损止盈）

        Args:
            symbol: 'BTC/USDT'
            direction: 'long' or 'short'
            amount: 开仓数量（USDT 价值）
            leverage: 杠杆
            stop_loss: 止损价
            take_profit: 止盈价
        """
        try:
            # 设置杠杆
            self.set_leverage(symbol, leverage)

            # 计算实际下单量
            # 先获取当前价格来计算合约张数
            ticker = self.exchange.fetch_ticker(symbol)
            current_price = ticker['last']

            # amount 是 USDT 价值，转为币数量
            coin_amount = (amount * leverage) / current_price

            # 精度处理
            symbol_info = SYMBOLS.get(symbol, {})
            size_increment = symbol_info.get('size_increment', 0.00001)
            coin_amount = round(coin_amount / size_increment) * size_increment
            min_size = symbol_info.get('min_size', 0.00001)
            if coin_amount < min_size:
                self._log(f"⚠️ 下单量 {coin_amount} 小于最小 {min_size}，已调整")
                coin_amount = min_size

            side = 'buy' if direction == 'long' else 'sell'

            # OKX 合约参数
            params = {
                'tdMode': 'isolated',  # 逐仓模式
            }

            # 止损止盈（通过附加订单）
            if stop_loss:
                sl_side = 'sell' if direction == 'long' else 'buy'
                params['slTriggerPx'] = str(stop_loss)
                params['slOrdPx'] = '-1'  # 市价触发

            if take_profit:
                params['tpTriggerPx'] = str(take_profit)
                params['tpOrdPx'] = '-1'  # 市价触发

            self._log(f"🚀 开{direction}仓: {symbol} | ~{coin_amount:.6f} 币 | ≈${amount:.2f} USDT | {leverage}x")
            if stop_loss:
                self._log(f"   🛑 止损: ${stop_loss:.2f}")
            if take_profit:
                self._log(f"   🎯 止盈: ${take_profit:.2f}")

            order = self.market_order(symbol, side, coin_amount, params)
            return order

        except Exception as e:
            self._log(f"❌ 开仓失败: {e}")
            return None

    def close_position(self, symbol: str, direction: str, amount: float = None) -> Optional[Dict]:
        """
        平仓

        Args:
            symbol: 'BTC/USDT'
            direction: 'long' or 'short'
            amount: 平仓数量（None = 全部平仓）
        """
        try:
            side = 'sell' if direction == 'long' else 'buy'

            if amount is None:
                # 查询当前持仓量
                positions = self.get_positions()
                for pos in positions:
                    if pos['symbol'] == symbol or pos['symbol'] == f"{symbol.replace('/', '')}:USDT":
                        amount = pos['contracts']
                        break

            if amount is None or amount == 0:
                self._log("⚠️ 无持仓可平")
                return None

            params = {
                'tdMode': 'isolated',
                'reduceOnly': True,  # 只减仓
            }

            self._log(f"🔒 平{direction}仓: {symbol} | 数量: {amount}")

            # 用市价单平仓
            okx_symbol = symbol if '/' in symbol else f"{symbol}/USDT:USDT"
            order = self.exchange.create_order(
                symbol=okx_symbol,
                type='market',
                side=side,
                amount=amount,
                params=params,
            )

            self._log(f"✅ 平仓成功 | ID: {order['id']}")
            return self._format_order(order)

        except Exception as e:
            self._log(f"❌ 平仓失败: {e}")
            return None

    # ============ 订单管理 ============

    def get_open_orders(self, symbol: str = None) -> list:
        """查询挂单"""
        try:
            if symbol:
                orders = self.exchange.fetch_open_orders(symbol)
            else:
                orders = self.exchange.fetch_open_orders()
            self._log(f"📋 当前挂单: {len(orders)} 个")
            return [self._format_order(o) for o in orders]
        except Exception as e:
            self._log(f"❌ 查询挂单失败: {e}")
            return []

    def cancel_order(self, order_id: str, symbol: str) -> bool:
        """取消订单"""
        try:
            self.exchange.cancel_order(order_id, symbol)
            self._log(f"🗑️ 已取消订单: {order_id}")
            return True
        except Exception as e:
            self._log(f"❌ 取消订单失败: {e}")
            return False

    def cancel_all_orders(self, symbol: str = None) -> bool:
        """取消所有挂单"""
        try:
            if symbol:
                self.exchange.cancel_all_orders(symbol)
            else:
                for sym in SYMBOLS:
                    try:
                        self.exchange.cancel_all_orders(sym)
                    except:
                        pass
            self._log("🗑️ 已取消所有挂单")
            return True
        except Exception as e:
            self._log(f"❌ 取消挂单失败: {e}")
            return False

    # ============ 辅助方法 ============

    def get_market_price(self, symbol: str) -> Optional[float]:
        """获取最新价格"""
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return ticker['last']
        except Exception as e:
            self._log(f"❌ 获取价格失败: {e}")
            return None

    def calculate_position_size(self, symbol: str, entry_price: float,
                                 stop_loss: float, risk_pct: float = 0.02,
                                 total_capital: float = 200) -> Dict:
        """
        根据风控规则计算仓位大小

        Args:
            symbol: 交易对
            entry_price: 入场价
            stop_loss: 止损价
            risk_pct: 单笔风险占比
            total_capital: 总资金

        Returns:
            仓位计算结果
        """
        risk_amount = total_capital * risk_pct  # 愿意亏损的金额
        price_risk = abs(entry_price - stop_loss)  # 每币风险

        if price_risk == 0:
            return {'error': '止损价与入场价相同'}

        coin_amount = risk_amount / price_risk  # 可开仓数量
        position_value = coin_amount * entry_price  # 仓位价值

        return {
            'symbol': symbol,
            'entry_price': entry_price,
            'stop_loss': stop_loss,
            'risk_amount_usdt': risk_amount,
            'risk_per_coin': price_risk,
            'coin_amount': round(coin_amount, 6),
            'position_value_usdt': round(position_value, 2),
            'leverage_needed': round(position_value / total_capital, 1) if total_capital > 0 else 0,
            'risk_pct': risk_pct,
        }

    def _format_order(self, order) -> Dict:
        """格式化订单信息"""
        return {
            'id': order.get('id', ''),
            'symbol': order.get('symbol', ''),
            'type': order.get('type', ''),
            'side': order.get('side', ''),
            'price': order.get('price'),
            'average': order.get('average'),
            'amount': order.get('amount', 0),
            'filled': order.get('filled', 0),
            'remaining': order.get('remaining', 0),
            'cost': order.get('cost', 0),
            'status': order.get('status', ''),
            'timestamp': order.get('timestamp'),
            'datetime': order.get('datetime', ''),
            'fee': order.get('fee', {}),
        }

    def test_connection(self) -> bool:
        """测试交易所连接"""
        try:
            # 测试服务器时间
            server_time = self.exchange.fetch_time()
            self._log(f"✅ OKX 连接正常 | 服务器时间: {datetime.fromtimestamp(server_time/1000)}")

            # 测试余额
            balance = self.get_balance()

            return True
        except Exception as e:
            self._log(f"❌ OKX 连接失败: {e}")
            return False


if __name__ == '__main__':
    print("=" * 60)
    print("🦞 OKX 交易执行器 - 连接测试")
    print("=" * 60)

    trader = OKXTrader()

    if trader.test_connection():
        print("\n✅ 连接测试通过！")
        trader.get_positions()
        trader.get_open_orders()

        # 获取价格
        for sym in ['BTC/USDT', 'ETH/USDT']:
            price = trader.get_market_price(sym)
            if price:
                print(f"   {sym}: ${price:,.2f}")
    else:
        print("\n❌ 连接测试失败，请检查 API 密钥和网络")
        print("   需要设置环境变量:")
        print("   export OKX_API_KEY='your_key'")
        print("   export OKX_SECRET='your_secret'")
        print("   export OKX_PASSPHRASE='your_passphrase'")
