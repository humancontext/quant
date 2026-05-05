"""
均值回归策略 - Bollinger Bands + RSI 组合

核心逻辑：
1. 价格触及布林带下轨 + RSI 超卖 → 做多
2. 价格触及布林带上轨 + RSI 超买 → 做空
3. 价格回归中轨 → 平仓

适用于震荡市，趋势市需要配合趋势过滤器
"""
import pandas as pd
import numpy as np
from ta.volatility import BollingerBands
from ta.momentum import RSIIndicator


class MeanReversionStrategy:
    """均值回归策略"""
    
    def __init__(self, bb_window=20, bb_std=2.0, rsi_window=14, 
                 rsi_oversold=30, rsi_overbought=70, trend_filter=True):
        """
        Args:
            bb_window: 布林带周期
            bb_std: 布林带标准差倍数
            rsi_window: RSI 周期
            rsi_oversold: RSI 超卖阈值
            rsi_overbought: RSI 超买阈值
            trend_filter: 是否启用趋势过滤（SMA 200）
        """
        self.bb_window = bb_window
        self.bb_std = bb_std
        self.rsi_window = rsi_window
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.trend_filter = trend_filter
        self.name = "均值回归 (BB+RSI)"
    
    def generate_signals(self, df):
        """
        生成交易信号
        
        Returns:
            DataFrame with columns: signal (1=买入, -1=卖出, 0=持有), 
                                    position (1=持仓, -1=空仓, 0=空)
        """
        data = df.copy()
        
        # 计算布林带
        bb = BollingerBands(close=data['close'], window=self.bb_window, window_dev=self.bb_std)
        data['bb_upper'] = bb.bollinger_hband()
        data['bb_middle'] = bb.bollinger_mavg()
        data['bb_lower'] = bb.bollinger_lband()
        data['bb_width'] = (data['bb_upper'] - data['bb_lower']) / data['bb_middle']
        
        # 计算 RSI
        rsi = RSIIndicator(close=data['close'], window=self.rsi_window)
        data['rsi'] = rsi.rsi()
        
        # 趋势过滤：SMA 200
        if self.trend_filter:
            data['sma_200'] = data['close'].rolling(200).mean()
            data['trend'] = np.where(data['close'] > data['sma_200'], 1, -1)
        
        # 生成信号
        data['signal'] = 0
        
        # 做多条件：价格低于下轨 + RSI 超卖
        long_condition = (
            (data['close'] <= data['bb_lower']) & 
            (data['rsi'] <= self.rsi_oversold)
        )
        
        # 做空条件：价格高于上轨 + RSI 超买
        short_condition = (
            (data['close'] >= data['bb_upper']) & 
            (data['rsi'] >= self.rsi_overbought)
        )
        
        # 平仓条件：价格回归中轨
        close_long = data['close'] >= data['bb_middle']
        close_short = data['close'] <= data['bb_middle']
        
        # 应用趋势过滤
        if self.trend_filter:
            long_condition = long_condition & (data['trend'] == 1)  # 上升趋势中只做多
            short_condition = short_condition & (data['trend'] == -1)  # 下降趋势中只做空
        
        data.loc[long_condition, 'signal'] = 1
        data.loc[short_condition, 'signal'] = -1
        
        # 计算持仓状态
        data['position'] = 0
        current_pos = 0
        
        for i in range(len(data)):
            sig = data.iloc[i]['signal']
            
            if sig == 1 and current_pos != 1:
                current_pos = 1  # 开多
            elif sig == -1 and current_pos != -1:
                current_pos = -1  # 开空
            elif current_pos == 1 and close_long.iloc[i]:
                current_pos = 0  # 平多
            elif current_pos == -1 and close_short.iloc[i]:
                current_pos = 0  # 平空
            
            data.iloc[i, data.columns.get_loc('position')] = current_pos
        
        return data
    
    def get_latest_signal(self, df):
        """获取最新交易信号"""
        data = self.generate_signals(df)
        latest = data.iloc[-1]
        prev = data.iloc[-2]
        
        signal_type = 'HOLD'
        details = {}
        
        if latest['signal'] == 1 and prev['signal'] != 1:
            signal_type = 'LONG'
            details = {
                'price': latest['close'],
                'bb_lower': latest['bb_lower'],
                'rsi': latest['rsi'],
                'reason': f"价格 ${latest['close']:.2f} 触及布林带下轨 ${latest['bb_lower']:.2f}，RSI={latest['rsi']:.1f}（超卖）"
            }
        elif latest['signal'] == -1 and prev['signal'] != -1:
            signal_type = 'SHORT'
            details = {
                'price': latest['close'],
                'bb_upper': latest['bb_upper'],
                'rsi': latest['rsi'],
                'reason': f"价格 ${latest['close']:.2f} 触及布林带上轨 ${latest['bb_upper']:.2f}，RSI={latest['rsi']:.1f}（超买）"
            }
        else:
            details = {
                'price': latest['close'],
                'bb_middle': latest['bb_middle'],
                'rsi': latest['rsi'],
                'bb_width': latest['bb_width'],
                'position': latest['position'],
                'reason': f"持有观望 | 价格 ${latest['close']:.2f} | RSI={latest['rsi']:.1f} | BB宽度={latest['bb_width']:.4f}"
            }
        
        return {
            'strategy': self.name,
            'signal': signal_type,
            'details': details,
            'timestamp': latest.name.isoformat() if hasattr(latest.name, 'isoformat') else str(latest.name),
        }
    
    def backtest(self, df, initial_capital=10000, commission=0.001):
        """
        简单回测
        
        Returns:
            dict with performance metrics
        """
        data = self.generate_signals(df)
        
        # 计算策略收益
        data['strategy_returns'] = data['position'].shift(1) * data['returns']
        data['strategy_returns'] = data['strategy_returns'].fillna(0)
        
        # 扣除手续费（每次开仓/平仓）
        trades = data['position'].diff().fillna(0) != 0
        data.loc[trades, 'strategy_returns'] -= commission
        
        # 累计收益
        data['cumulative_returns'] = (1 + data['returns']).cumprod()
        data['cumulative_strategy'] = (1 + data['strategy_returns']).cumprod()
        
        # 计算绩效指标
        total_trades = trades.sum()
        winning_trades = (data.loc[trades, 'strategy_returns'] > 0).sum() if total_trades > 0 else 0
        
        # 最大回撤
        cummax = data['cumulative_strategy'].cummax()
        drawdown = (data['cumulative_strategy'] - cummax) / cummax
        max_drawdown = drawdown.min()
        
        # 年化收益
        days = (data.index[-1] - data.index[0]).days
        if days > 0:
            annual_return = (data['cumulative_strategy'].iloc[-1] ** (365 / days)) - 1
        else:
            annual_return = 0
        
        # 夏普比率
        sharpe = data['strategy_returns'].mean() / data['strategy_returns'].std() * (252 ** 0.5) if data['strategy_returns'].std() > 0 else 0
        
        results = {
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
        
        return results
    
    def print_report(self, results):
        """打印回测报告"""
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
