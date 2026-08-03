# 股票监控日报

自动监控股票清单中的股票，计算 120 日均线（MA120），将分析结果通过邮件和微信推送。

## 项目结构

```
stock-monitor/
├── stock_list.json          ← 股票清单（你手动编辑这里增删股票）
├── monitor.py               ← 主监控脚本
├── requirements.txt         ← Python 依赖
├── .github/
│   └── workflows/
│       └── stock-monitor.yml ← GitHub Actions 定时任务配置
└── README.md
```

## 监控规则

- **重点关注**：收盘价 < MA120 × 0.90（低于均线 10% 以上）
- **正常展示**：所有股票与 MA120 的差距百分比
- **运行时间**：每个交易日（A 股）北京时间 15:05

## 通知方式

| 渠道 | 内容 |
|------|------|
| QQ 邮箱 | 完整 HTML 表格报告 |
| 微信推送 | 简要摘要（Server酱） |

## 快速开始

### 1. 编辑股票清单

编辑 `stock_list.json`，格式：

```json
[
  { "code": "600519", "name": "贵州茅台" },
  { "code": "000858", "name": "五粮液" }
]
```

### 2. 配置 GitHub Secrets

在 GitHub 仓库 → Settings → Secrets and variables → Actions 中添加：

| Secret | 说明 | 获取方式 |
|--------|------|----------|
| `QQ_EMAIL` | 你的 QQ 邮箱地址 | QQ 邮箱设置 → 账户 → SMTP 服务 |
| `QQ_AUTH_CODE` | QQ 邮箱 SMTP 授权码（16 位） | QQ 邮箱设置 → 账户 → 生成授权码 |
| `TO_EMAIL` | 收件人邮箱（你自己的邮箱） | 即你的邮箱地址 |
| `SERVERCHAN_SENDKEY` | Server酱 SendKey（可选，用于微信推送） | [sct.ftqq.com](https://sct.ftqq.com) 注册获取 |

### 3. 推送到 GitHub

```bash
git init
git add .
git commit -m "init: stock monitor"
git remote add origin https://github.com/<你的用户名>/stock-monitor.git
git push -u origin main
```

推送后 GitHub Actions 会在每个交易日自动运行。

### 4. 手动触发测试

在 GitHub 仓库 → Actions → 股票监控日报 → Run workflow 可以手动触发一次测试。

## 数据源

脚本内置三个数据源，按优先级自动切换（东方财富 → 新浪 → 腾讯），确保稳定性。

## 免责声明

本工具仅供学习和个人研究使用，数据来自公开接口，仅供参考，不构成任何投资建议。投资有风险，入市需谨慎。
