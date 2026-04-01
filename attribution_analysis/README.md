# 策略归因分析框架

## 快速开始

### 1. 安装依赖

```bash
cd attribution_analysis
pip install -r requirements.txt
```

### 2. 准备数据

从券商导出 PDF 交割单，放到 `data/raw/` 目录。

### 3. 转换数据

```bash
python scripts/convert_broker_data.py \
    --input data/raw/交割单.pdf \
    --output data/trades.csv
```

### 4. 运行分析

```bash
python scripts/attribution.py \
    --trades data/trades.csv \
    --start-date 2025-01-01 \
    --end-date 2026-03-31 \
    --output output/report.html
```

### 5. 查看报告

```bash
open output/report.html
```

## 数据格式

标准 CSV 格式：
```csv
date,market,code,name,direction,quantity,price,amount,brokerage_fee,stamp_duty,transfer_fee,other_fee,net_amount,remark
20260103,沪深市场,600519,贵州茅台,买入,100,1680.00,168000.00,50.40,0,16.80,0,-168067.20,
```

## 配置

编辑 `config.py` 修改基准指数、无风险利率等参数。

## 示例输出

### 终端输出

```
==================================================
策略归因分析报告
分析区间：2025-01-01 至 2026-03-31
==================================================

【核心指标】
组合总收益率：     +2.5%
基准总收益率：     -8.0%
超额收益率：       +10.5%

Alpha（年化）：    +8.2%  ✓ 策略有效
Beta：             0.95   ✓ 市场敏感度正常
R²：               0.78   ✓ 模型拟合良好

夏普比率：         1.35
最大回撤：         -12.3%
年化波动率：       18.5%

【收益归因】
市场贡献（Beta）： -7.6%
策略贡献（Alpha）： +10.1%

【结论】
策略表现优异，Alpha 显著为正。
在下跌市场中仍获得正收益，风控有效。
==================================================
```

### HTML 报告

包含交互式图表：
- 净值曲线（可缩放）
- 月度超额收益柱状图
- 滚动 Beta 曲线

## 常见问题

### Q: PDF 解析失败怎么办？

A: 检查 PDF 是否包含可提取的表格（不是扫描件）。如果是扫描件，需要使用截图识别模式。

### Q: 某只股票获取行情失败？

A: 可能是股票代码格式问题或已退市。检查 `data/cache/` 目录下的缓存文件，手动补充数据。

### Q: Alpha 为负是什么原因？

A: 说明策略跑输市场。可能原因：
1. 选股能力不足
2. 交易成本过高
3. 择时不当

建议和师傅讨论策略调整方向。

## 技术栈

- Python 3.14
- AKShare - A股数据获取
- pandas - 数据处理
- statsmodels - 统计分析
- pyecharts - 可视化
- pdfplumber - PDF解析

## 项目结构

```
attribution_analysis/
├── config.py              # 全局配置
├── requirements.txt       # 依赖列表
├── README.md             # 本文档
├── data/
│   ├── raw/              # 原始PDF文件
│   ├── cache/            # AKShare缓存
│   └── trades.csv        # 标准格式交割单
├── scripts/
│   ├── convert_broker_data.py  # PDF转换脚本
│   └── attribution.py          # 核心分析脚本
├── output/               # 生成的报告
└── templates/            # HTML模板（预留）
```

## 许可证

MIT License
