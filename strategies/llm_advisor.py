"""
LLM 增强决策模块

通过 GPT-5.4 分析市场数据、生成交易建议、辅助策略决策
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from data.collector import fetch_ohlcv, get_current_price
from strategies.mean_reversion import MeanReversionStrategy
from strategies.trend_following import TrendFollowingStrategy


# ==================== LLM 接口 ====================

def call_llm(messages, model="gpt-5.2", temperature=0.3):
    """
    调用 LLM（通过 right.codes 中转站）
    
    Args:
        messages: OpenAI 格式的消息列表
        model: 模型名称
        temperature: 温度参数
    """
    import urllib.request
    import ssl
    
    api_key = os.environ.get('OPENAI_API_KEY', '')
    base_url = "https://www.right.codes/codex/v1"
    
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 2000,
    }).encode('utf-8')
    
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    
    # 使用系统代理
    proxy_handler = urllib.request.ProxyHandler({
        'http': 'http://127.0.0.1:7890',
        'https': 'http://127.0.0.1:7890',
    })
    opener = urllib.request.build_opener(proxy_handler)
    
    try:
        with opener.open(req, timeout=60) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            return result['choices'][0]['message']['content']
    except Exception as e:
        print(f"❌ LLM 调用失败: {e}")
        return None


# ==================== 市场数据整理 ====================

def prepare_market_context(symbol='BTC/USDT', exchange='binance'):
    """
    收集并整理市场数据，作为 LLM 的输入上下文
    """
    print(f"📊 收集 {symbol} 市场数据...")
    
    # 当前价格
    price = get_current_price(symbol, exchange)
    
    # 多时间框架数据
    timeframes_data = {}
    for tf in ['1h', '4h', '1d']:
        df = fetch_ohlcv(symbol, tf, limit=100, exchange_name=exchange)
        if df is not None:
            latest = df.iloc[-1]
            stats = {
                'price': latest['close'],
                'change_24h_pct': (df['close'].iloc[-1] / df['close'].iloc[-6] - 1) * 100 if len(df) >= 6 else 0,
                'high': df['high'].tail(24).max(),
                'low': df['low'].tail(24).min(),
                'volume_avg': df['volume'].tail(20).mean(),
                'volume_current': df['volume'].iloc[-1],
                'volatility_annual': latest.get('volatility', 0),
                'returns_7d': (df['close'].iloc[-1] / df['close'].iloc[0] - 1) * 100 if len(df) >= 7 else 0,
            }
            # 支撑/阻力位
            stats['support'] = df['low'].tail(50).min()
            stats['resistance'] = df['high'].tail(50).max()
            
            timeframes_data[tf] = stats
    
    # 技术指标信号
    signals = {}
    for StrategyClass in [MeanReversionStrategy, TrendFollowingStrategy]:
        try:
            df_4h = fetch_ohlcv(symbol, '4h', limit=500, exchange_name=exchange)
            if df_4h is not None:
                strategy = StrategyClass()
                signal = strategy.get_latest_signal(df_4h)
                signals[signal['strategy']] = signal
        except:
            pass
    
    return {
        'symbol': symbol,
        'timestamp': datetime.now().isoformat(),
        'current_price': price,
        'timeframes': timeframes_data,
        'strategy_signals': signals,
    }


def format_market_prompt(context):
    """将市场数据格式化为 LLM prompt"""
    
    price = context['current_price']
    
    prompt = f"""你是一位专业的加密货币量化交易分析师。请基于以下市场数据进行分析和交易建议。

## 市场概况
- 标的: {context['symbol']}
- 当前价格: ${price['price']:,.2f}
- 24h 涨跌: {price['change_24h']:+.2f}%
- 24h 最高: ${price['high_24h']:,.2f} | 最低: ${price['low_24h']:,.2f}
- 时间: {context['timestamp']}

## 多时间框架数据
"""
    
    for tf, data in context['timeframes'].items():
        prompt += f"""
### {tf} 周期
- 价格: ${data['price']:,.2f}
- 近期变化: {data['change_24h_pct']:+.2f}%
- 区间高/低: ${data['high']:,.2f} / ${data['low']:,.2f}
- 支撑位: ${data['support']:,.2f} | 阻力位: ${data['resistance']:,.2f}
- 成交量: {data['volume_current']:,.0f} (20期均值: {data['volume_avg']:,.0f})
- 年化波动率: {data['volatility_annual']:.2%}
"""
    
    if context['strategy_signals']:
        prompt += "\n## 量化策略信号\n"
        for name, sig in context['strategy_signals'].items():
            emoji = {'LONG': '🟢', 'SHORT': '🔴', 'HOLD': '⚪'}.get(sig['signal'], '⚪')
            prompt += f"- {emoji} {name}: {sig['signal']} — {sig['details']['reason']}\n"
    
    prompt += """
## 请提供以下分析

请用 JSON 格式回复（不要用 markdown 代码块），包含以下字段：

{
    "market_analysis": "2-3句话的市场整体判断",
    "trend": "bullish/bearish/neutral",
    "key_levels": {
        "support": 支撑位价格,
        "resistance": 阻力位价格
    },
    "risk_level": "low/medium/high",
    "recommendation": "long/short/hold",
    "confidence": 0-100的置信度,
    "position_size_pct": 建议仓位百分比(1-100),
    "entry_price": 建议入场价,
    "stop_loss": 止损价,
    "take_profit": 止盈价,
    "reasoning": "详细的决策推理过程（3-5句话）",
    "risks": ["风险1", "风险2"],
    "time_horizon": "短期(1-3天)/中期(1-2周)/长期(1月+)"
}

重要：
1. 所有价格必须是具体数字，不要用描述性语言
2. confidence 要反映你真实的信心水平，不要总是给高分
3. 止损要合理（不超过 5%），止盈要有吸引力（至少 1:2 盈亏比）
4. 如果信号不明确，果断建议 hold
"""
    
    return prompt


# ==================== LLM 分析 ====================

def analyze_with_llm(symbol='BTC/USDT', exchange='binance'):
    """完整的 LLM 增强分析流程"""
    
    print("=" * 70)
    print(f"🧠 LLM 增强分析 - {symbol}")
    print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    
    # 1. 收集数据
    context = prepare_market_context(symbol, exchange)
    
    # 2. 构建 prompt
    prompt = format_market_prompt(context)
    
    # 3. 调用 LLM
    print("\n🤖 正在调用 GPT-5.4 进行分析...")
    
    messages = [
        {
            "role": "system",
            "content": "你是一位经验丰富的加密货币量化交易分析师，擅长技术分析和风险管理。你的分析要客观、数据驱动，不盲目看多或看空。当信号不明确时，你宁愿建议观望。回复必须是纯 JSON，不要包含 markdown 代码块标记。"
        },
        {
            "role": "user", 
            "content": prompt
        }
    ]
    
    response = call_llm(messages, model="gpt-5.2")
    
    if not response:
        print("❌ LLM 分析失败")
        return None
    
    # 4. 解析响应
    try:
        # 清理可能的 markdown 代码块标记
        clean = response.strip()
        if clean.startswith('```'):
            clean = '\n'.join(clean.split('\n')[1:])
        if clean.endswith('```'):
            clean = '\n'.join(clean.split('\n')[:-1])
        if clean.startswith('```json'):
            clean = clean[7:]
        
        analysis = json.loads(clean)
    except json.JSONDecodeError as e:
        print(f"⚠️ JSON 解析失败，尝试修复...")
        print(f"原始响应: {response[:500]}")
        # 尝试提取 JSON 部分
        import re
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            try:
                analysis = json.loads(json_match.group())
            except:
                print("❌ 无法解析 LLM 响应")
                return None
        else:
            print("❌ 无法从响应中提取 JSON")
            return None
    
    # 5. 输出结果
    print_analysis(analysis, symbol)
    
    return analysis


def print_analysis(analysis, symbol):
    """打印 LLM 分析结果"""
    
    print("\n" + "=" * 70)
    print(f"📋 LLM 分析报告 - {symbol}")
    print("=" * 70)
    
    rec = analysis.get('recommendation', 'hold')
    rec_emoji = {'long': '🟢做多', 'short': '🔴做空', 'hold': '⚪观望'}.get(rec, '⚪')
    trend = analysis.get('trend', 'neutral')
    trend_emoji = {'bullish': '📈', 'bearish': '📉', 'neutral': '➡️'}.get(trend, '➡️')
    risk = analysis.get('risk_level', 'medium')
    risk_emoji = {'low': '🟢', 'medium': '🟡', 'high': '🔴'}.get(risk, '🟡')
    
    print(f"\n{trend_emoji} 市场判断: {analysis.get('market_analysis', 'N/A')}")
    print(f"📊 趋势方向: {trend} | 风险等级: {risk_emoji} {risk}")
    
    print(f"\n{rec_emoji} 操作建议: {rec.upper()}")
    print(f"📐 置信度: {analysis.get('confidence', 0)}%")
    print(f"💰 建议仓位: {analysis.get('position_size_pct', 0)}%")
    
    if rec != 'hold':
        print(f"\n📍 关键价位:")
        print(f"   入场价: ${analysis.get('entry_price', 'N/A'):,.2f}" if isinstance(analysis.get('entry_price'), (int, float)) else f"   入场价: {analysis.get('entry_price', 'N/A')}")
        print(f"   止损:   ${analysis.get('stop_loss', 'N/A'):,.2f}" if isinstance(analysis.get('stop_loss'), (int, float)) else f"   止损:   {analysis.get('stop_loss', 'N/A')}")
        print(f"   止盈:   ${analysis.get('take_profit', 'N/A'):,.2f}" if isinstance(analysis.get('take_profit'), (int, float)) else f"   止盈:   {analysis.get('take_profit', 'N/A')}")
        
        if analysis.get('entry_price') and analysis.get('stop_loss') and analysis.get('take_profit'):
            entry = analysis['entry_price']
            sl = analysis['stop_loss']
            tp = analysis['take_profit']
            risk_pct = abs(entry - sl) / entry * 100
            reward_pct = abs(tp - entry) / entry * 100
            rr_ratio = reward_pct / risk_pct if risk_pct > 0 else 0
            print(f"   风险: {risk_pct:.2f}% | 收益: {reward_pct:.2f}% | 盈亏比: 1:{rr_ratio:.1f}")
    
    print(f"\n🧠 推理过程:")
    print(f"   {analysis.get('reasoning', 'N/A')}")
    
    if analysis.get('risks'):
        print(f"\n⚠️ 风险提示:")
        for risk in analysis['risks']:
            print(f"   • {risk}")
    
    print(f"\n⏰ 建议持仓周期: {analysis.get('time_horizon', 'N/A')}")
    print("=" * 70)


# ==================== 多标的对比分析 ====================

def compare_analysis(symbols=None, exchange='binance'):
    """多标的对比分析"""
    if symbols is None:
        symbols = ['BTC/USDT', 'SOL/USDT', 'ETH/USDT']
    
    results = {}
    for symbol in symbols:
        print(f"\n{'#' * 70}")
        print(f"### 分析 {symbol}")
        print(f"{'#' * 70}")
        result = analyze_with_llm(symbol, exchange)
        if result:
            results[symbol] = result
    
    # 打印对比表
    if results:
        print("\n" + "=" * 70)
        print("📊 多标的对比总结")
        print("=" * 70)
        for symbol, analysis in results.items():
            rec = analysis.get('recommendation', 'hold')
            conf = analysis.get('confidence', 0)
            trend = analysis.get('trend', '?')
            rec_emoji = {'long': '🟢', 'short': '🔴', 'hold': '⚪'}.get(rec, '⚪')
            trend_emoji = {'bullish': '📈', 'bearish': '📉', 'neutral': '➡️'}.get(trend, '➡️')
            print(f"  {symbol:12s} {rec_emoji} {rec:5s} | {trend_emoji} {trend:8s} | 置信度 {conf}% | 仓位 {analysis.get('position_size_pct', 0)}%")
        print("=" * 70)
    
    return results


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='LLM 增强量化分析')
    parser.add_argument('--symbol', default='BTC/USDT', help='交易对')
    parser.add_argument('--exchange', default='binance', help='交易所')
    parser.add_argument('--compare', action='store_true', help='多标的对比')
    
    args = parser.parse_args()
    
    if args.compare:
        compare_analysis(exchange=args.exchange)
    else:
        analyze_with_llm(args.symbol, args.exchange)
