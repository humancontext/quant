"""
风控模块

功能：
1. 仓位管理 - 根据 Kelly 准则 + 固定比例计算仓位
2. 日内亏损限制
3. 最大回撤保护
4. 盈亏比校验
5. 交易日志
"""
import json
import os
import sys
from datetime import datetime, date
from typing import Dict, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config.settings import RISK, SYMBOLS


class RiskManager:
    """风控管理器"""

    def __init__(self, state_file=None):
        self.total_capital = RISK['total_capital_usdt']
        self.max_single_risk = RISK['max_single_risk_pct']
        self.max_daily_loss = RISK['max_daily_loss_pct']
        self.max_drawdown = RISK['max_drawdown_pct']
        self.max_positions = RISK['max_open_positions']
        self.min_rr_ratio = RISK['min_rr_ratio']

        # 状态文件
        if state_file is None:
            state_file = os.path.join(os.path.dirname(__file__), '..', 'logs', 'risk_state.json')
        self.state_file = state_file
        self.state = self._load_state()

    def _load_state(self) -> Dict:
        """加载风控状态"""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            except:
                pass

        return {
            'daily_pnl': {},
            'peak_capital': self.total_capital,
            'current_capital': self.total_capital,
            'total_trades': 0,
            'total_pnl': 0,
            'last_reset_date': str(date.today()),
        }

    def _save_state(self):
        """保存风控状态"""
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

    def _reset_daily_if_needed(self):
        """每天重置日盈亏"""
        today = str(date.today())
        if self.state.get('last_reset_date') != today:
            self.state['daily_pnl'] = {}
            self.state['last_reset_date'] = today
            self._save_state()

    # ============ 风控检查 ============

    def check_can_trade(self, symbol: str, direction: str,
                        entry_price: float, stop_loss: float,
                        take_profit: float = None) -> Tuple[bool, str]:
        """
        综合风控检查

        Returns:
            (允许交易, 原因说明)
        """
        self._reset_daily_if_needed()

        # 1. 日内亏损检查
        today = str(date.today())
        daily_pnl = self.state.get('daily_pnl', {}).get(today, 0)
        max_daily = self.total_capital * self.max_daily_loss
        if daily_pnl <= -max_daily:
            return False, f"❌ 日亏损已达上限: ${abs(daily_pnl):.2f} / ${max_daily:.2f}"

        # 2. 最大回撤检查
        current = self.state.get('current_capital', self.total_capital)
        peak = self.state.get('peak_capital', self.total_capital)
        drawdown = (peak - current) / peak if peak > 0 else 0
        if drawdown >= self.max_drawdown:
            return False, f"❌ 回撤已达上限: {drawdown:.1%} / {self.max_drawdown:.1%}"

        # 3. 盈亏比检查
        if stop_loss and entry_price:
            risk_per_coin = abs(entry_price - stop_loss)
            if risk_per_coin == 0:
                return False, "❌ 止损价与入场价相同"

            if take_profit:
                reward_per_coin = abs(take_profit - entry_price)
                rr_ratio = reward_per_coin / risk_per_coin
                if rr_ratio < self.min_rr_ratio:
                    return False, f"❌ 盈亏比不足: {rr_ratio:.2f} < {self.min_rr_ratio}"

        # 4. 持仓数量检查
        open_positions = self.state.get('open_positions', 0)
        if open_positions >= self.max_positions:
            return False, f"❌ 持仓数已达上限: {open_positions} / {self.max_positions}"

        return True, "✅ 风控通过"

    def calculate_position(self, symbol: str, entry_price: float,
                           stop_loss: float, confidence: float = 0.7) -> Dict:
        """
        计算建议仓位

        Args:
            symbol: 交易对
            entry_price: 入场价
            stop_loss: 止损价
            confidence: 信号置信度 0-1

        Returns:
            仓位建议
        """
        # 单笔风险金额
        risk_amount = self.total_capital * self.max_single_risk

        # 根据置信度调整（置信度高可以稍微加大，但不超过上限）
        adjusted_risk = risk_amount * (0.5 + 0.5 * confidence)

        # 每币风险
        price_risk = abs(entry_price - stop_loss)
        if price_risk == 0:
            return {'error': '止损价无效'}

        # 基础仓位
        base_coins = adjusted_risk / price_risk
        position_value = base_coins * entry_price

        # 杠杆需求
        leverage_needed = position_value / self.total_capital if self.total_capital > 0 else 0

        # 不超过单标的最大仓位
        symbol_config = SYMBOLS.get(symbol, {})
        max_position_value = self.total_capital * symbol_config.get('max_position_pct', 0.5)
        if position_value > max_position_value:
            position_value = max_position_value
            base_coins = position_value / entry_price

        # 精度处理
        size_increment = symbol_config.get('size_increment', 0.00001)
        base_coins = round(base_coins / size_increment) * size_increment

        return {
            'symbol': symbol,
            'direction': 'long' if entry_price > stop_loss else 'short',
            'entry_price': entry_price,
            'stop_loss': stop_loss,
            'coin_amount': base_coins,
            'position_value_usdt': round(base_coins * entry_price, 2),
            'risk_amount_usdt': round(adjusted_risk, 2),
            'leverage_needed': round(leverage_needed, 1),
            'confidence': confidence,
        }

    # ============ 状态更新 ============

    def record_trade(self, symbol: str, direction: str, side: str,
                     amount: float, price: float, pnl: float = 0):
        """记录交易"""
        self._reset_daily_if_needed()
        today = str(date.today())

        # 更新日盈亏
        if today not in self.state.get('daily_pnl', {}):
            self.state['daily_pnl'] = {today: 0}
        self.state['daily_pnl'][today] = self.state['daily_pnl'].get(today, 0) + pnl

        # 更新总盈亏
        self.state['total_pnl'] = self.state.get('total_pnl', 0) + pnl
        self.state['total_trades'] = self.state.get('total_trades', 0) + 1

        # 更新资金
        self.state['current_capital'] = self.total_capital + self.state['total_pnl']
        if self.state['current_capital'] > self.state.get('peak_capital', self.total_capital):
            self.state['peak_capital'] = self.state['current_capital']

        # 更新持仓数
        if side in ['buy', 'open_long', 'open_short']:
            self.state['open_positions'] = self.state.get('open_positions', 0) + 1
        elif side in ['sell', 'close']:
            self.state['open_positions'] = max(0, self.state.get('open_positions', 0) - 1)

        self._save_state()

        # 日志
        pnl_str = f"PnL: {pnl:+.2f}" if pnl != 0 else ""
        print(f"📝 交易记录: {symbol} {direction} {side} | {amount} @ ${price:.2f} {pnl_str}")

    def get_status(self) -> Dict:
        """获取风控状态摘要"""
        self._reset_daily_if_needed()
        today = str(date.today())
        current = self.state.get('current_capital', self.total_capital)
        peak = self.state.get('peak_capital', self.total_capital)
        drawdown = (peak - current) / peak if peak > 0 else 0

        return {
            'initial_capital': self.total_capital,
            'current_capital': round(current, 2),
            'total_pnl': round(self.state.get('total_pnl', 0), 2),
            'total_trades': self.state.get('total_trades', 0),
            'daily_pnl': round(self.state.get('daily_pnl', {}).get(today, 0), 2),
            'drawdown': f"{drawdown:.1%}",
            'open_positions': self.state.get('open_positions', 0),
            'max_positions': self.max_positions,
        }


if __name__ == '__main__':
    print("=" * 60)
    print("🛡️ 风控系统状态")
    print("=" * 60)

    rm = RiskManager()
    status = rm.get_status()

    print(f"💰 初始资金: ${status['initial_capital']:.2f} USDT")
    print(f"💰 当前资金: ${status['current_capital']:.2f} USDT")
    print(f"📊 总盈亏: ${status['total_pnl']:+.2f} USDT")
    print(f"📅 今日盈亏: ${status['daily_pnl']:+.2f} USDT")
    print(f"📉 最大回撤: {status['drawdown']}")
    print(f"🔄 总交易次数: {status['total_trades']}")
    print(f"📊 持仓数: {status['open_positions']} / {status['max_positions']}")

    # 测试风控检查
    print("\n--- 风控检查测试 ---")
    can, reason = rm.check_can_trade('BTC/USDT', 'long', 94000, 93500, 95500)
    print(f"BTC 做多测试: {reason}")

    if can:
        pos = rm.calculate_position('BTC/USDT', 94000, 93500, confidence=0.7)
        print(f"建议仓位: {pos['coin_amount']} BTC ≈ ${pos['position_value_usdt']}")
        print(f"风险金额: ${pos['risk_amount_usdt']} | 需要杠杆: {pos['leverage_needed']}x")
