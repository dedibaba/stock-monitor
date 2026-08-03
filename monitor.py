#!/usr/bin/env python3
"""
股票监控脚本
- 监控 stock_list.json 中的股票
- 计算 MA120 均线，判断股价与均线的差距
- 低于均线 10% 标记为重点关注
- 通过 QQ 邮箱 SMTP 发送 HTML 报告
- 通过 Server酱 推送到微信
"""

import json
import os
import sys
import time
import random
import smtplib
import logging
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header

import akshare as ak
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def load_env():
    """加载 .env 文件中的环境变量（如果存在）"""
    env_path = os.path.join(SCRIPT_DIR, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("\"'")
                if key and value and key not in os.environ:
                    os.environ[key] = value

# ─────────────────────── 配置 ───────────────────────

# 股票清单路径（与脚本同目录）
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STOCK_LIST_PATH = os.path.join(SCRIPT_DIR, "stock_list.json")

# 加载 .env 文件（本地开发用）
load_env()

# QQ 邮箱配置（从环境变量 / .env 文件读取）
QQ_EMAIL = os.environ.get("QQ_EMAIL", "")
QQ_AUTH_CODE = os.environ.get("QQ_AUTH_CODE", "")
TO_EMAIL = os.environ.get("TO_EMAIL", "")

# Server酱 配置（可选）
SERVERCHAN_SENDKEY = os.environ.get("SERVERCHAN_SENDKEY", "")

# 均线周期
MA_PERIOD = 120
# 重点警报阈值（低于均线百分比）
ALERT_THRESHOLD = 10

# ─────────────────────── 工具函数 ───────────────────────


def load_stock_list():
    """加载股票清单"""
    with open(STOCK_LIST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def is_trading_day():
    """判断今天是否为 A 股交易日"""
    try:
        today = datetime.now().strftime("%Y%m%d")
        # 通过 AkShare 获取交易日历
        df = ak.tool_trade_date_hist_sina()
        trade_dates = set(df["trade_date"].astype(str).str.replace("-", ""))
        return today in trade_dates
    except Exception as e:
        logger.warning(f"判断交易日历失败: {e}，默认当作交易日继续")
        return True


def determine_market(code):
    """根据股票代码判断交易所"""
    if code.startswith("6"):
        return "sh"  # 上交所
    elif code.startswith(("0", "3")):
        return "sz"  # 深交所
    elif code.startswith("4") or code.startswith("8"):
        return "bj"  # 北交所
    else:
        return "sh"  # 默认上交所


def fetch_stock_data(stock_list):
    """
    获取所有股票的日 K 线数据。
    使用多数据源 failover 策略：
    1. 东方财富（主力接口）
    2. 新浪财经（备用接口）
    3. 腾讯财经（兜底接口）

    反爬措施：打乱请求顺序、随机延迟、单接口失败自动切换
    """
    results = {}
    errors = []

    # 定义数据源，按优先级排列
    sources = [
        ("东方财富", fetch_from_eastmoney),
        ("新浪财经", fetch_from_sina),
        ("腾讯财经", fetch_from_tencent),
    ]

    # 打乱股票顺序，避免固定顺序被识别为爬虫
    shuffled = stock_list[:]
    random.shuffle(shuffled)

    for idx, stock in enumerate(shuffled):
        code = stock["code"]
        name = stock["name"]
        market = determine_market(code)
        full_code = f"{market}{code}"

        data = None
        for source_name, fetch_func in sources:
            try:
                logger.info(f"[{idx+1}/{len(shuffled)}] 获取 {name}({code})，使用 {source_name}...")
                data = fetch_func(full_code, code, name)
                if data is not None and not data.empty:
                    logger.info(f"  ✓ {source_name} 获取成功")
                    break
                else:
                    logger.warning(f"  ✗ {source_name} 返回空数据")
            except Exception as e:
                logger.warning(f"  ✗ {source_name} 获取失败: {e}")

        if data is not None and not data.empty:
            results[code] = {"name": name, "data": data}
        else:
            errors.append(name)
            logger.error(f"所有接口均无法获取 {name}({code}) 的数据")

        # 随机延迟 1~3 秒，防止请求过于频繁被屏蔽
        delay = random.uniform(1.0, 3.0)
        logger.debug(f"  等待 {delay:.1f} 秒...")
        time.sleep(delay)

    return results, errors


def fetch_from_eastmoney(full_code, code, name):
    """数据源 1：东方财富（AkShare 接口）"""
    try:
        # 东方财富日 K 线接口
        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            adjust="",  # 不复权
            start_date=(datetime.now() - timedelta(days=365)).strftime("%Y%m%d"),
            end_date=datetime.now().strftime("%Y%m%d"),
        )
        if df is not None and not df.empty:
            df = df.rename(columns={"日期": "date", "收盘": "close"})
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            return df[["date", "close"]]
    except Exception:
        pass
    return None


def fetch_from_sina(full_code, code, name):
    """数据源 2：新浪财经（AkShare 接口）"""
    try:
        df = ak.stock_zh_a_daily(symbol=full_code, adjust="")
        if df is not None and not df.empty:
            df = df.rename(columns={"date": "date", "close": "close"})
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            return df[["date", "close"]]
    except Exception:
        pass
    return None


def fetch_from_tencent(full_code, code, name):
    """数据源 3：腾讯财经（AkShare 接口）"""
    try:
        df = ak.stock_zh_a_hist_tencent(symbol=code, adjust="")
        if df is not None and not df.empty:
            df = df.rename(columns={"日期": "date", "收盘": "close"})
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            return df[["date", "close"]]
    except Exception:
        pass
    return None


def calculate_ma120(df):
    """计算 120 日均线"""
    if len(df) < MA_PERIOD:
        return None, None
    ma120 = df["close"].rolling(window=MA_PERIOD).mean().iloc[-1]
    current_price = df["close"].iloc[-1]
    return current_price, ma120


def analyze_stocks(stock_data):
    """分析所有股票，返回分析结果"""
    results = []
    for code, info in stock_data.items():
        name = info["name"]
        df = info["data"]
        price, ma120 = calculate_ma120(df)

        if price is None or pd.isna(ma120):
            results.append({
                "name": name,
                "code": code,
                "price": "-",
                "ma120": "-",
                "gap": "-",
                "status": "数据不足",
                "is_alert": False,
            })
            continue

        gap_pct = (price - ma120) / ma120 * 100
        is_alert = gap_pct <= -ALERT_THRESHOLD

        results.append({
            "name": name,
            "code": code,
            "price": round(price, 2),
            "ma120": round(ma120, 2),
            "gap": round(gap_pct, 2),
            "status": "⚠️ 重点关注" if is_alert else "正常",
            "is_alert": is_alert,
        })

    # 按差距排序（从低到高）
    results.sort(key=lambda x: x["gap"] if isinstance(x["gap"], (int, float)) else 999)
    return results


# ─────────────────────── 邮件发送 ───────────────────────


def send_email(results, errors, failed_fetch, preview=False):
    """发送 HTML 格式的邮件报告；preview=True 时保存为本地文件"""
    today_str = datetime.now().strftime("%Y-%m-%d")
    alert_count = sum(1 for r in results if r["is_alert"])

    subject = f"📊 股票监控日报 {today_str} {'（⚠️ {} 只重点关注）'.format(alert_count) if alert_count else ''}"

    # 构建 HTML 表格
    table_rows = ""
    for r in results:
        gap_str = f"{r['gap']:+.2f}%" if isinstance(r["gap"], (int, float)) else r["gap"]
        if r["is_alert"]:
            row = f"""
            <tr style="background-color: #fff3cd; font-weight: bold;">
                <td>{r['name']}</td>
                <td>{r['code']}</td>
                <td>{r['price']}</td>
                <td>{r['ma120']}</td>
                <td style="color: #dc3545;">{gap_str}</td>
                <td style="color: #dc3545;">{r['status']}</td>
            </tr>"""
        elif isinstance(r["gap"], (int, float)) and r["gap"] < 0:
            row = f"""
            <tr style="background-color: #fff5f5;">
                <td>{r['name']}</td>
                <td>{r['code']}</td>
                <td>{r['price']}</td>
                <td>{r['ma120']}</td>
                <td style="color: #e74c3c;">{gap_str}</td>
                <td>{r['status']}</td>
            </tr>"""
        else:
            row = f"""
            <tr>
                <td>{r['name']}</td>
                <td>{r['code']}</td>
                <td>{r['price']}</td>
                <td>{r['ma120']}</td>
                <td style="color: #27ae60;">{gap_str}</td>
                <td>{r['status']}</td>
            </tr>"""

        table_rows += row

    # 失败获取的股票
    fetch_error_html = ""
    if failed_fetch:
        fetch_error_html = f"""
        <h3 style="color: #e74c3c;">❌ 数据获取失败（{len(failed_fetch)} 只）</h3>
        <p>以下股票无法从任何数据源获取数据：{", ".join(failed_fetch)}</p>
        """

    # 分析数据不足的股票
    insufficient_html = ""
    insufficient = [r for r in results if r["status"] == "数据不足"]
    if insufficient:
        insufficient_html = f"""
        <h3 style="color: #f39c12;">⚠️ 数据不足（{len(insufficient)} 只）</h3>
        <p>以下股票历史数据不足 120 天，无法计算 MA120：{", ".join(r['name'] for r in insufficient)}</p>
        """

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: "Microsoft YaHei", Arial, sans-serif; margin: 20px; }}
            h1 {{ color: #2c3e50; }}
            h2 {{ color: #34495e; }}
            table {{ border-collapse: collapse; width: 100%; margin-top: 10px; }}
            th {{ background-color: #2c3e50; color: white; padding: 10px; text-align: center; }}
            td {{ padding: 8px 10px; text-align: center; border-bottom: 1px solid #ddd; }}
            tr:hover {{ background-color: #f5f5f5; }}
            .summary {{ font-size: 16px; margin: 15px 0; }}
            .alert-box {{ background-color: #fff3cd; border: 1px solid #ffc107; padding: 15px; border-radius: 5px; margin: 15px 0; }}
            .footer {{ color: #999; font-size: 12px; margin-top: 20px; }}
        </style>
    </head>
    <body>
        <h1>📊 股票监控日报</h1>
        <p class="summary">日期：<strong>{today_str}</strong> &nbsp;|&nbsp;
           共监控 <strong>{len(results)}</strong> 只股票 &nbsp;|&nbsp;
           重点关注 <strong style="color: #dc3545;">{alert_count}</strong> 只</p>

        {f'<div class="alert-box">⚠️ 今日有 <strong>{alert_count}</strong> 只股票低于 120 日均线 10% 以上，请重点关注！</div>' if alert_count else '<div class="alert-box" style="background-color: #d4edda; border-color: #28a745;">✅ 今日无重点警报股票</div>'}

        <h2>📋 全部股票监控详情</h2>
        <table>
            <tr>
                <th>股票名称</th>
                <th>代码</th>
                <th>收盘价</th>
                <th>MA120</th>
                <th>与均线差距</th>
                <th>状态</th>
            </tr>
            {table_rows}
        </table>

        {fetch_error_html}
        {insufficient_html}

        <div class="footer">
            <p>说明：差距 = (收盘价 - MA120) / MA120 × 100%</p>
            <p>⚠️ 重点关注：差距 ≤ -10%（股价低于 120 日均线 10% 以上）</p>
            <p>本邮件由 GitHub Actions 自动发送 | 股票数据来自公开接口，仅供参考，不构成投资建议</p>
        </div>
    </body>
    </html>
    """

    if preview:
        preview_path = os.path.join(SCRIPT_DIR, "preview.html")
        with open(preview_path, "w", encoding="utf-8") as f:
            f.write(html_body)
        logger.info(f"预览模式：HTML 报告已保存到 {preview_path}")
        return True

    if not QQ_EMAIL or not QQ_AUTH_CODE or not TO_EMAIL:
        logger.error("邮件配置不完整，请检查环境变量 QQ_EMAIL / QQ_AUTH_CODE / TO_EMAIL")
        return False

    msg = MIMEMultipart("alternative")
    msg["From"] = QQ_EMAIL
    msg["To"] = TO_EMAIL
    msg["Subject"] = Header(subject, "utf-8")
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        server = smtplib.SMTP_SSL("smtp.qq.com", 465)
        server.login(QQ_EMAIL, QQ_AUTH_CODE)
        server.sendmail(QQ_EMAIL, [TO_EMAIL], msg.as_string())
        server.quit()
        logger.info("邮件发送成功")
        return True
    except Exception as e:
        logger.error(f"邮件发送失败: {e}")
        return False


# ─────────────────────── 微信推送 ───────────────────────


def send_wechat_push(results, errors, failed_fetch):
    """通过 Server酱 推送到微信（精简格式：仅股票名称 + 差距百分比）"""
    if not SERVERCHAN_SENDKEY:
        return

    today_str = datetime.now().strftime("%Y-%m-%d")
    alert_count = sum(1 for r in results if r["is_alert"])

    title = f"📊 股票监控 {today_str} | 重点警报 {alert_count} 只"

    lines = []
    lines.append(f"📅 {today_str}　　共监控 {len(results)} 只")
    lines.append("")

    # 所有有数据的股票，按 gap 从低到高排序
    valid = [r for r in results if isinstance(r["gap"], (int, float))]
    valid.sort(key=lambda r: r["gap"])

    # 第一档：低于均线 10% 以上
    tier1 = [r for r in valid if r["gap"] <= -10]
    # 第二档：低于均线 0~10%
    tier2 = [r for r in valid if -10 < r["gap"] < 0]
    # 第三档：站上均线
    tier3 = [r for r in valid if r["gap"] >= 0]

    if tier1:
        lines.append(f"🔴 低于均线 10%+（{len(tier1)}只）")
        for r in tier1:
            lines.append(f"{r['name']}　{r['gap']:+.2f}%")
        lines.append("")

    if tier2:
        lines.append(f"🟡 低于均线 0~10%（{len(tier2)}只）")
        for r in tier2:
            lines.append(f"{r['name']}　{r['gap']:+.2f}%")
        lines.append("")

    if tier3:
        lines.append(f"🟢 站上均线（{len(tier3)}只）")
        for r in tier3:
            lines.append(f"{r['name']}　+{r['gap']:.2f}%")
        lines.append("")

    if failed_fetch:
        lines.append(f"❌ 数据失败：{', '.join(failed_fetch)}")
        lines.append("")

    lines.append("📧 完整数据见邮件")

    desp = "\n".join(lines)

    try:
        import urllib.request
        import urllib.parse
        url = f"https://sctapi.ftqq.com/{SERVERCHAN_SENDKEY}.send"
        data = urllib.parse.urlencode({"title": title, "desp": desp}).encode()
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            if result.get("code") == 0 or result.get("errno") == 0:
                logger.info("微信推送成功")
            else:
                logger.warning(f"微信推送返回异常: {result}")
    except Exception as e:
        logger.error(f"微信推送失败: {e}")


# ─────────────────────── 主流程 ───────────────────────


def main():
    import argparse
    parser = argparse.ArgumentParser(description="股票监控日报")
    parser.add_argument("--preview", action="store_true", help="预览模式：保存 HTML 报告到本地文件，不发送邮件")
    parser.add_argument("--no-tradingday-check", action="store_true", help="跳过交易日检查（用于测试）")
    args = parser.parse_args()

    logger.info("=" * 50)
    logger.info("股票监控任务开始" + (" [预览模式]" if args.preview else ""))
    logger.info("=" * 50)

    # 1. 检查是否为交易日（预览模式可跳过）
    if not args.no_tradingday_check and not is_trading_day():
        logger.info("今天不是交易日，跳过监控")
        return

    # 2. 加载股票清单
    stock_list = load_stock_list()
    logger.info(f"加载股票清单，共 {len(stock_list)} 只")

    # 3. 获取股票数据（多源 failover）
    stock_data, failed_stocks = fetch_stock_data(stock_list)

    if not stock_data:
        logger.error("所有股票数据获取失败")
        return

    # 4. 分析数据
    results = analyze_stocks(stock_data)

    # 5. 发送邮件报告 或 预览
    send_email(
        results,
        failed_stocks,
        [r["name"] for r in results if r["status"] == "数据不足"],
        preview=args.preview,
    )

    # 6. 发送微信推送（预览模式跳过）
    if not args.preview:
        send_wechat_push(
            results,
            failed_stocks,
            [r["name"] for r in results if r["status"] == "数据不足"],
        )

    alert_count = sum(1 for r in results if r["is_alert"])
    logger.info(f"监控完成：重点关注 {alert_count} 只")


def _send_error_email(error_msg):
    """发送错误通知邮件"""
    if not QQ_EMAIL or not QQ_AUTH_CODE or not TO_EMAIL:
        return
    today_str = datetime.now().strftime("%Y-%m-%d")
    msg = MIMEMultipart()
    msg["From"] = QQ_EMAIL
    msg["To"] = TO_EMAIL
    msg["Subject"] = Header(f"❌ 股票监控失败 {today_str}", "utf-8")
    msg.attach(MIMEText(f"<pre>{error_msg}</pre>", "html", "utf-8"))
    try:
        server = smtplib.SMTP_SSL("smtp.qq.com", 465)
        server.login(QQ_EMAIL, QQ_AUTH_CODE)
        server.sendmail(QQ_EMAIL, [TO_EMAIL], msg.as_string())
        server.quit()
        logger.info("错误通知邮件已发送")
    except Exception as e:
        logger.error(f"错误通知邮件发送失败: {e}")


if __name__ == "__main__":
    main()
