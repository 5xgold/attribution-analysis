"""券商数据转换脚本：PDF/截图 → 标准 CSV"""

import sys
import argparse
import pdfplumber
import pandas as pd
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from config import COLUMN_MAPPING, STANDARD_COLUMNS


def parse_pdf(pdf_path):
    """解析 PDF 交割单 - 使用文本提取"""
    all_rows = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if '客户资金明细' not in text:
                continue

            lines = text.split('\n')
            in_section = False
            for line in lines:
                if '客户资金明细' in line:
                    in_section = True
                    continue
                if in_section and line.strip().startswith('2026'):
                    # 按空格分割，保留17个字段
                    parts = line.strip().split()
                    if len(parts) >= 17:
                        all_rows.append(parts[:17])

    if not all_rows:
        raise ValueError("PDF 中未找到客户资金明细数据")

    headers = ['date', 'market', 'account', 'currency', 'business_type', 'code', 'name',
               'quantity', 'price', 'inventory', 'amount', 'balance',
               'brokerage_fee', 'stamp_duty', 'transfer_fee', 'other_fee', 'remark']

    return headers, all_rows


def normalize_columns(headers, rows):
    """列名标准化"""
    df = pd.DataFrame(rows, columns=headers)

    # 检查必需列
    required = ["date", "code", "name", "quantity", "price", "amount"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"缺少必需列: {missing}")

    # 补充缺失列
    for col in STANDARD_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    # 推断买卖方向（必须在 net_amount 之前）
    df["direction"] = df.apply(infer_direction, axis=1)

    # 计算 net_amount
    df["net_amount"] = df.apply(calculate_net_amount, axis=1)

    # 过滤掉非股票交易（场外开基、申购配号等）
    df = df[df['market'].isin(['上海', '深圳', '沪港通'])]
    df = df[df['direction'].isin(['买入', '卖出'])]

    return df[STANDARD_COLUMNS]


def infer_direction(row):
    """推断买卖方向"""
    # 从业务类型推断
    business_type = str(row.get("business_type", ""))
    remark = str(row.get("remark", ""))

    if "买" in business_type or "买" in remark or "Buy" in business_type:
        return "买入"
    if "卖" in business_type or "卖" in remark or "Sell" in business_type:
        return "卖出"

    # 从金额符号推断（买入为负）
    amount = float(row.get("amount", 0) or 0)
    if amount < 0:
        return "买入"
    elif amount > 0:
        return "卖出"

    return "未知"


def calculate_net_amount(row):
    """计算实际收付金额"""
    amount = float(row.get("amount", 0))
    fee = float(row.get("brokerage_fee", 0))
    stamp = float(row.get("stamp_duty", 0))
    transfer = float(row.get("transfer_fee", 0))
    other = float(row.get("other_fee", 0))

    total_fee = fee + stamp + transfer + other

    if row.get("direction") == "买入":
        return -(amount + total_fee)
    else:
        return amount - total_fee


def convert_pdf_to_csv(pdf_path, output_path):
    """主函数：PDF → CSV"""
    print(f"正在解析 PDF: {pdf_path}")
    headers, rows = parse_pdf(pdf_path)

    print(f"提取到 {len(rows)} 行数据")
    print(f"表头: {headers}")

    print("正在标准化列名...")
    df = normalize_columns(headers, rows)

    print(f"转换完成，共 {len(df)} 条交易记录")
    print(f"保存到: {output_path}")
    df.to_csv(output_path, index=False, encoding="utf-8-sig")

    # 打印前5行供用户确认
    print("\n前5行数据预览：")
    print(df.head().to_string())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="券商数据转换")
    parser.add_argument("--input", required=True, help="输入文件路径（PDF）")
    parser.add_argument("--output", required=True, help="输出 CSV 路径")

    args = parser.parse_args()

    try:
        convert_pdf_to_csv(args.input, args.output)
        print("\n✓ 转换成功")
    except Exception as e:
        print(f"\n✗ 转换失败: {e}")
        sys.exit(1)
