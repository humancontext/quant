"""
趋势跟踪策略 - EMA 交叉 + ADX 过滤

核心逻辑：
1. 短期 EMA 上穿长期 EMA + ADX > 25 → 做多
2. 短期 EMA 下穿长期 EMA + ADX > 25 → 做空
3. ADX < 20 → 震荡市，不交易

适合趋势明显的行情（BTC 大趋势）
"""
import pandas as pd
import numpy as np
from ta.trend import EMAIndicator, ADXIndicator


class TrendFollowingStrategy:
    """趋势跟踪策略"""
    
    def __init__(self, fast_period=12, slow_period=26, adx_period=14, 
                 adx_threshold=25, use_trailing_stop=True, trailing_stop_pct=0.03):
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        self.use_trailing_stop = use_trailing_stop
        self.trailing_stop_pct = trailing_stop_pct
        self.name = f"趋势跟踪 (EMA{fast_period}/{slow_period}+ADX)"
    
    def generate_signals(self, df):
        data = df.copy()
        
        # 计算 EMA
        ema_fast = EMAIndicator(close=data['close'], window=self.fast_period)
        ema_slow = EMAIndicator(close=data['close'], window=self.slow_period)
        data['ema_fast'] = ema_fast.ema_indicator()
        data['ema_slow'] = ema_slow.ema_indicator()
        
        # 计算 ADX（趋势强度）
        adx = ADXIndicator(high=data['high'], low=data['low'], close=data['close'], window=self.adx_period)
        data['adx'] = adx.adx()
        data['di_plus'] = adx.adx_pos()
        data['di_minus'] = adx.adx_neg()
        
        # EMA 交叉信号
        data['ema_cross'] = np.where(
            data['ema_fast'] > data['ema_slow'], 1,
            np.where(data['ema_fast'] < data['ema_slow'], -1, 0)
        )
        data['ema_cross_change'] = data['ema_cross'].diff()
        
        # 生成信号
        data['signal'] = 0
        
        # 金叉 + 趋势够强
        golden_cross = (data['ema_cross_change'] == 2) & (data['adx'] > self.adx_threshold)
        # 死叉 + 趋势够强
        death_cross = (data['ema_cross_change'] == -2) & (data['adx'] > self.adx_threshold)
        
        data.loc[golden_cross, 'signal'] = 1
        data.loc[death_cross, 'signal'] = -1
        
        # 计算持仓（持仓直到反向信号）
        data['position'] = 0
        current_pos = 0
        
        for i in range(len(data)):
            sig = data.iloc[i]['signal']
            
            if sig == 1:
                current_pos = 1
            elif sig == -1:
                current_pos = -1
            
            # 趋势太弱时平仓
            if data.iloc[i]['adx'] < 20:
                current_pos = 0
            
            data.iloc[i, data.columns.get_loc('position')] = current_pos
        
        # 移动止损
        if self.use_trailing_stop:
            data = self._apply_trailing_stop(data)
        
        return data
    
    def _apply_trailing_stop(self, data):
        """应用移动止损"""
        peak_price = 0
        trough_price = float('inf')
        
        for i in range(len(data)):
            pos = data.iloc[i]['position']
            price = data.iloc[i]['close']
            
            if pos == 1:  # 多头
                peak_price = max(peak_price, price)
                if price < peak_price * (1 - self.trailing_stop_pct):
                    data.iloc[i, data.columns.get_loc('position')] = 0
                    peak_price = 0
            elif pos == -1:  # 空头
                trough_price = min(trough_price, price)
                if price > trough_price * (1 + self.trailing_stop_pct):
                    data.iloc[i, data.columns.get_loc('position')] = 0
                    trough_price = float('inf')
        
        return data
    
    def get_latest_signal(self, df):
        data = self.generate_signals(df)
        latest = data.iloc[-1]
        
        signal_type = 'HOLD'
        if latest['signal'] == 1:
            signal_type = 'LONG'
        elif latest['signal'] == -1:
            signal_type = 'SHORT'
        
        return {
            'strategy': self.name,
            'signal': signal_type,
            'details': {
                'price': latest['close'],
                'ema_fast': latest['ema_fast'],
                'ema_slow': latest['ema_slow'],
                'adx': latest['adx'],
                'trend_strength': '强' if latest['adx'] > 25 else '弱' if latest['adx'] < 20 else '中',
                'position': latest['position'],
                'reason': self._get_reason(latest),
            },
            'timestamp': latest.name.isoformat() if hasattr(latest.name, 'isoformat') else str(latest.name),
        }
    
    def _get_reason(self, row):
        if row['ema_fast'] > row['ema_slow']:
            trend = "短期均线在长期均线上方（多头排列）"
        else:
            trend = "短期均线在长期均线下方（空头排列）"
        
        return f"{trend} | ADX={row['adx']:.1f}（趋势强度: {'强' if row['adx'] > 25 else '弱'}）"
    
    def backtest(self, df, initial_capital=10000, commission=0.001):
        data = self.generate_signals(df)
        
        data['strategy_returns'] = data['position'].shift(1) * data['returns']
        data['strategy_returns'] = data['strategy_returns'].fillna(0)
        
        trades = data['position'].diff().fillna(0) != 0
        data.loc[trades, 'strategy_returns'] -= commission
        
        data['cumulative_returns'] = (1 + data['returns']).cumprod()
        data['cumulative_strategy'] = (1 + data['strategy_returns']).cumprod()
        
        total_trades = trades.sum()
        winning_trades = (data.loc[trades, 'strategy_returns'] > 0).sum() if total_trades > 0 else 0
        
        cummax = data['cumulative_strategy'].cummax()
        drawdown = (data['cumulative_strategy'] - cummax) / cummax
        max_drawdown = drawdown.min()
        
        days = (data.index[-1] - data.index[0]).days
        annual_return = (data['cumulative_strategy'].iloc[-1] ** (365 / days)) - 1 if days > 0 else 0
        
        sharpe = data['strategy_returns'].mean() / data['strategy_returns'].std() * (252 ** 0.5) if data['strategy_returns'].std() > 0 else 0
        
        return {
            'strategy': self.name,
            'total_trades': total_trades,
            'win_rate': winning_trades / total_trades if total_trades > 0 else 0,
            'total_return': data['cumulative_strategy'].iloc[-1] - 1,
            'annual_return': annual_return,
            'max_drawdown': max_drawdown,
            'sharpe_ratio': sharpe,
            'initial_capital': initial_capital,
            'final_capital': initial_capital * data['cumulative_strategy'].iloc[-1],
            'data': data,
        }
    
    def print_report(self, results):
        print("\n" + "=" * 60)
        print(f"📊 回测报告 - {results['strategy']}")
        print("=" * 60)
        print(f"💰 初始资金: ${results['initial_capital']:,.2f}")
        print(f"💰 最终资金: ${results['final_capital']:,.2f}")
        print(f"📈 总收益: {results['total_return']:+.2%}")
        print(f"📈 年化收益: {results['annual_return']:+.2%}")
        print(f"📊 最大回撤: {results['max_drawdown']:.2%}")
        print(f"📐 夏普比率: {results['sharpe_ratio']:.2f}")
        print(f"🔄 总交易次数: {results['total_trades']}")
        print(f"✅ 胜率: {results['win_rate']:.1%}")
        print("=" * 60)
