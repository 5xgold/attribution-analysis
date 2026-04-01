"""策略归因分析核心脚本"""

import sys
import argparse
import json
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import akshare as ak
import statsmodels.api as sm
from pyecharts import options as opts
from pyecharts.charts import Line, Bar, Pie, Grid, Page
from pyecharts.globals import ThemeType

sys.path.append(str(Path(__file__).parent.parent))
from config import (
    BENCHMARK_INDEX, RISK_FREE_RATE, CACHE_DIR,
    CACHE_EXPIRY_DAYS, ROLLING_WINDOW, MIN_TRADING_DAYS,
    REPORT_TITLE, OUTPUT_DIR
)


def load_trades(csv_path):
    """加载交割单数据"""
    df = pd.read_csv(csv_path, dtype={'code': str})  # 确保代码列为字符串
    df['date'] = pd.to_datetime(df['date'], format='%Y%m%d')
    df = df.sort_values('date').reset_index(drop=True)

    # 确保数值列为正确类型
    numeric_cols = ['quantity', 'price', 'amount', 'brokerage_fee', 'stamp_duty',
                    'transfer_fee', 'other_fee', 'net_amount']
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    print(f"加载 {len(df)} 条交易记录")
    print(f"时间范围: {df['date'].min()} 至 {df['date'].max()}")
    return df


def get_stock_prices(code, start_date, end_date):
    """获取股票历史行情（带缓存）- 保存完整 OHLCV 数据"""
    # AKShare 需要6位代码
    code_str = str(code).zfill(6)
    cache_file = Path(CACHE_DIR) / f"{code_str}_{start_date}_{end_date}.csv"
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    # 检查缓存
    if cache_file.exists():
        cache_time = datetime.fromtimestamp(cache_file.stat().st_mtime)
        if datetime.now() - cache_time < timedelta(days=CACHE_EXPIRY_DAYS):
            return pd.read_csv(cache_file, parse_dates=['date'])

    # 从 AKShare 获取
    try:
        df = ak.stock_zh_a_hist(symbol=code_str, start_date=start_date, end_date=end_date, adjust="qfq")
        # 保留所有字段：日期、开盘、收盘、最高、最低、成交量、成交额、振幅、涨跌幅、涨跌额、换手率
        df = df.rename(columns={
            '日期': 'date',
            '开盘': 'open',
            '收盘': 'close',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'volume',
            '成交额': 'amount',
            '振幅': 'amplitude',
            '涨跌幅': 'pct_change',
            '涨跌额': 'change',
            '换手率': 'turnover'
        })
        df['date'] = pd.to_datetime(df['date'])
        # 保存所有列
        df.to_csv(cache_file, index=False)
        return df
    except Exception as e:
        print(f"警告: 获取 {code_str} 行情失败: {e}")
        return pd.DataFrame(columns=['date', 'open', 'high', 'low', 'close', 'volume'])


def get_benchmark_prices(start_date, end_date):
    """获取基准指数行情（带缓存）- 保存完整 OHLCV 数据"""
    cache_file = Path(CACHE_DIR) / f"benchmark_{BENCHMARK_INDEX}_{start_date}_{end_date}.csv"
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    if cache_file.exists():
        cache_time = datetime.fromtimestamp(cache_file.stat().st_mtime)
        if datetime.now() - cache_time < timedelta(days=CACHE_EXPIRY_DAYS):
            return pd.read_csv(cache_file, parse_dates=['date'])

    try:
        df = ak.stock_zh_index_daily(symbol=f"sh{BENCHMARK_INDEX}")
        # 保留所有字段
        df = df.rename(columns={
            'date': 'date',
            'open': 'open',
            'close': 'close',
            'high': 'high',
            'low': 'low',
            'volume': 'volume'
        })
        df['date'] = pd.to_datetime(df['date'])
        df = df[(df['date'] >= start_date) & (df['date'] <= end_date)]
        # 保存所有列
        df.to_csv(cache_file, index=False)
        return df
    except Exception as e:
        print(f"错误: 获取基准指数失败: {e}")
        sys.exit(1)


def rebuild_positions(trades_df):
    """重建每日持仓"""
    positions = {}  # {code: {'quantity': int, 'cost_basis': float}}
    daily_snapshots = []

    # 获取所有交易日期
    trade_dates = trades_df['date'].unique()

    for date in trade_dates:
        day_trades = trades_df[trades_df['date'] == date]

        # 处理当日交易
        for _, trade in day_trades.iterrows():
            code = trade['code']
            if code not in positions:
                positions[code] = {'quantity': 0, 'cost_basis': 0.0}

            if trade['direction'] == '买入':
                positions[code]['quantity'] += trade['quantity']
                positions[code]['cost_basis'] += abs(trade['net_amount'])
            elif trade['direction'] == '卖出':
                sell_qty = abs(trade['quantity'])  # 卖出数量为正
                positions[code]['quantity'] -= sell_qty
                # 成本按比例减少
                if positions[code]['quantity'] > 0:
                    # 还有剩余持仓，按比例减少成本
                    original_qty = positions[code]['quantity'] + sell_qty
                    if original_qty > 0:
                        ratio = sell_qty / original_qty
                        positions[code]['cost_basis'] *= (1 - ratio)
                else:
                    # 全部卖出，成本归零
                    positions[code]['cost_basis'] = 0.0

        # 记录当日持仓快照
        snapshot = {
            'date': date,
            'positions': {k: v.copy() for k, v in positions.items() if v['quantity'] > 0}
        }
        daily_snapshots.append(snapshot)

    return daily_snapshots


def calculate_portfolio_value(snapshots, start_date, end_date):
    """计算每日组合市值"""
    # 获取所有持仓股票的行情
    all_codes = set()
    for snap in snapshots:
        all_codes.update(snap['positions'].keys())

    print(f"获取 {len(all_codes)} 只股票的行情数据...")
    stock_prices = {}
    for code in all_codes:
        prices = get_stock_prices(code, start_date.strftime('%Y%m%d'), end_date.strftime('%Y%m%d'))
        if not prices.empty:
            stock_prices[code] = prices.set_index('date')['close']

    # 计算每日市值
    daily_values = []
    for snap in snapshots:
        date = snap['date']
        total_value = 0.0

        for code, pos in snap['positions'].items():
            if code in stock_prices and date in stock_prices[code].index:
                price = stock_prices[code].loc[date]
                total_value += price * pos['quantity']

        daily_values.append({'date': date, 'value': total_value})

    return pd.DataFrame(daily_values)


def calculate_returns(portfolio_values, benchmark_prices, trades_df):
    """计算组合和基准收益率"""
    # 合并组合市值和基准价格
    df = portfolio_values.merge(benchmark_prices, on='date', how='outer', suffixes=('_portfolio', '_benchmark'))
    df = df.sort_values('date').reset_index(drop=True)

    # 前向填充（处理非交易日）
    df['value'] = df['value'].ffill()
    df['close'] = df['close'].ffill()

    # 计算现金流
    df['cashflow'] = 0.0
    for _, trade in trades_df.iterrows():
        mask = df['date'] == trade['date']
        df.loc[mask, 'cashflow'] += trade['net_amount']

    # 计算组合收益率
    df['portfolio_return'] = 0.0
    for i in range(1, len(df)):
        prev_value = df.loc[i-1, 'value']
        curr_value = df.loc[i, 'value']
        cashflow = df.loc[i, 'cashflow']

        if prev_value > 0:
            df.loc[i, 'portfolio_return'] = (curr_value - prev_value - cashflow) / prev_value

    # 计算基准收益率
    df['benchmark_return'] = df['close'].pct_change().fillna(0)

    # 计算无风险利率（日化）
    rf_daily = (1 + RISK_FREE_RATE) ** (1/252) - 1
    df['rf'] = rf_daily

    # 计算超额收益率
    df['excess_portfolio'] = df['portfolio_return'] - df['rf']
    df['excess_benchmark'] = df['benchmark_return'] - df['rf']

    return df


def alpha_beta_analysis(returns_df):
    """Alpha/Beta 回归分析"""
    # 过滤有效数据，确保数值类型
    valid = returns_df[['excess_portfolio', 'excess_benchmark']].copy()
    valid = valid.dropna()

    # 确保数据类型为 float
    valid['excess_portfolio'] = valid['excess_portfolio'].astype(float)
    valid['excess_benchmark'] = valid['excess_benchmark'].astype(float)

    if len(valid) < MIN_TRADING_DAYS:
        raise ValueError(f"交易日数量不足（{len(valid)} < {MIN_TRADING_DAYS}）")

    # OLS 回归
    X = sm.add_constant(valid['excess_benchmark'].values)
    y = valid['excess_portfolio'].values
    model = sm.OLS(y, X).fit()

    alpha_daily = model.params[0]
    beta = model.params[1]
    r_squared = model.rsquared

    # 年化 Alpha
    alpha_annual = alpha_daily * 252

    # 计算其他指标（确保数值类型）
    value_series = returns_df['value'].astype(float)
    close_series = returns_df['close'].astype(float)
    portfolio_return = returns_df['portfolio_return'].astype(float)

    total_return = (value_series.iloc[-1] / value_series.iloc[0]) - 1
    benchmark_total = (close_series.iloc[-1] / close_series.iloc[0]) - 1
    excess_return = total_return - benchmark_total

    # 夏普比率
    sharpe = (portfolio_return.mean() / portfolio_return.std()) * np.sqrt(252)

    # 最大回撤
    cumulative = (1 + portfolio_return).cumprod()
    running_max = cumulative.expanding().max()
    drawdown = (cumulative - running_max) / running_max
    max_drawdown = drawdown.min()

    # 年化波动率
    volatility = portfolio_return.std() * np.sqrt(252)

    return {
        'alpha_annual': alpha_annual,
        'beta': beta,
        'r_squared': r_squared,
        'total_return': total_return,
        'benchmark_total': benchmark_total,
        'excess_return': excess_return,
        'sharpe': sharpe,
        'max_drawdown': max_drawdown,
        'volatility': volatility,
    }


def print_terminal_report(results, start_date, end_date):
    """打印终端报告"""
    print("\n" + "=" * 50)
    print(f"{REPORT_TITLE}")
    print(f"分析区间：{start_date.strftime('%Y-%m-%d')} 至 {end_date.strftime('%Y-%m-%d')}")
    print("=" * 50)

    print("\n【核心指标】")
    print(f"组合总收益率：     {results['total_return']:+.2%}")
    print(f"基准总收益率：     {results['benchmark_total']:+.2%}")
    print(f"超额收益率：       {results['excess_return']:+.2%}")
    print()
    print(f"Alpha（年化）：    {results['alpha_annual']:+.2%}  {'✓ 策略有效' if results['alpha_annual'] > 0 else '✗ 策略失效'}")
    print(f"Beta：             {results['beta']:.2f}   {'✓ 市场敏感度正常' if 0.5 < results['beta'] < 1.5 else '⚠ 异常'}")
    print(f"R²：               {results['r_squared']:.2f}   {'✓ 模型拟合良好' if results['r_squared'] > 0.7 else '⚠ 拟合度低'}")
    print()
    print(f"夏普比率：         {results['sharpe']:.2f}")
    print(f"最大回撤：         {results['max_drawdown']:.2%}")
    print(f"年化波动率：       {results['volatility']:.2%}")

    print("\n【收益归因】")
    beta_contrib = results['benchmark_total'] * results['beta']
    alpha_contrib = results['total_return'] - beta_contrib
    print(f"市场贡献（Beta）： {beta_contrib:+.2%}")
    print(f"策略贡献（Alpha）： {alpha_contrib:+.2%}")

    print("\n【结论】")
    if results['alpha_annual'] > 0:
        print("策略表现优异，Alpha 显著为正。")
        if results['benchmark_total'] < 0:
            print("在下跌市场中仍获得正收益，风控有效。")
    else:
        print("策略表现不佳，Alpha 为负。")
        if results['beta'] > 1:
            print("市场敏感度过高，放大了市场波动。")

    print("=" * 50)


def generate_html_report(returns_df, results, output_path):
    """生成 HTML 报告"""
    # 1. 净值曲线
    cumulative_portfolio = (1 + returns_df['portfolio_return']).cumprod()
    cumulative_benchmark = (1 + returns_df['benchmark_return']).cumprod()

    line_chart = (
        Line(init_opts=opts.InitOpts(theme=ThemeType.LIGHT, width="1200px", height="400px"))
        .add_xaxis(returns_df['date'].dt.strftime('%Y-%m-%d').tolist())
        .add_yaxis("组合净值", cumulative_portfolio.tolist(), is_smooth=True)
        .add_yaxis("基准净值", cumulative_benchmark.tolist(), is_smooth=True)
        .set_global_opts(
            title_opts=opts.TitleOpts(title="净值曲线"),
            tooltip_opts=opts.TooltipOpts(trigger="axis"),
            xaxis_opts=opts.AxisOpts(type_="category"),
            yaxis_opts=opts.AxisOpts(name="净值"),
            datazoom_opts=[opts.DataZoomOpts(range_start=0, range_end=100)],
        )
    )

    # 2. 月度超额收益
    returns_df['month'] = returns_df['date'].dt.to_period('M')
    monthly = returns_df.groupby('month').agg({
        'portfolio_return': lambda x: (1 + x).prod() - 1,
        'benchmark_return': lambda x: (1 + x).prod() - 1,
    })
    monthly['excess'] = monthly['portfolio_return'] - monthly['benchmark_return']

    bar_chart = (
        Bar(init_opts=opts.InitOpts(theme=ThemeType.LIGHT, width="1200px", height="400px"))
        .add_xaxis(monthly.index.astype(str).tolist())
        .add_yaxis("月度超额收益", (monthly['excess'] * 100).tolist())
        .set_global_opts(
            title_opts=opts.TitleOpts(title="月度超额收益"),
            tooltip_opts=opts.TooltipOpts(trigger="axis", formatter="{b}: {c}%"),
            xaxis_opts=opts.AxisOpts(type_="category"),
            yaxis_opts=opts.AxisOpts(name="超额收益 (%)"),
        )
    )

    # 3. 滚动 Beta
    rolling_beta = []
    for i in range(ROLLING_WINDOW, len(returns_df)):
        window = returns_df.iloc[i-ROLLING_WINDOW:i]
        X = sm.add_constant(window['excess_benchmark'])
        model = sm.OLS(window['excess_portfolio'], X).fit()
        rolling_beta.append(model.params[1])

    beta_dates = returns_df['date'].iloc[ROLLING_WINDOW:].dt.strftime('%Y-%m-%d').tolist()
    beta_chart = (
        Line(init_opts=opts.InitOpts(theme=ThemeType.LIGHT, width="1200px", height="400px"))
        .add_xaxis(beta_dates)
        .add_yaxis("滚动 Beta (60日)", rolling_beta, is_smooth=True)
        .set_global_opts(
            title_opts=opts.TitleOpts(title="滚动 Beta"),
            tooltip_opts=opts.TooltipOpts(trigger="axis"),
            xaxis_opts=opts.AxisOpts(type_="category"),
            yaxis_opts=opts.AxisOpts(name="Beta"),
            datazoom_opts=[opts.DataZoomOpts(range_start=0, range_end=100)],
        )
    )

    # 组合页面
    page = Page(layout=Page.SimplePageLayout)
    page.add(line_chart, bar_chart, beta_chart)
    page.render(output_path)

    print(f"\n详细报告已生成：{output_path}")


def main():
    parser = argparse.ArgumentParser(description="策略归因分析")
    parser.add_argument("--trades", required=True, help="交割单 CSV 路径")
    parser.add_argument("--start-date", help="开始日期 (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="结束日期 (YYYY-MM-DD)")
    parser.add_argument("--output", help="输出 HTML 路径")

    args = parser.parse_args()

    # 1. 加载数据
    print("正在加载交割单...")
    trades = load_trades(args.trades)

    start = pd.to_datetime(args.start_date) if args.start_date else trades['date'].min()
    end = pd.to_datetime(args.end_date) if args.end_date else trades['date'].max()

    # 2. 重建持仓
    print("正在重建持仓...")
    snapshots = rebuild_positions(trades)

    # 3. 计算市值
    print("正在计算组合市值...")
    portfolio_values = calculate_portfolio_value(snapshots, start, end)

    # 4. 获取基准数据
    print("正在获取基准指数数据...")
    benchmark_prices = get_benchmark_prices(start.strftime('%Y%m%d'), end.strftime('%Y%m%d'))

    # 5. 计算收益率
    print("正在计算收益率...")
    returns_df = calculate_returns(portfolio_values, benchmark_prices, trades)

    # 6. Alpha/Beta 分析
    print("正在进行 Alpha/Beta 分析...")
    results = alpha_beta_analysis(returns_df)

    # 7. 输出报告
    print_terminal_report(results, start, end)

    if args.output:
        output_path = args.output
    else:
        output_path = f"{OUTPUT_DIR}/{datetime.now().strftime('%Y-%m-%d')}_report.html"

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    generate_html_report(returns_df, results, output_path)


if __name__ == "__main__":
    main()
