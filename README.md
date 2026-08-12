# Evan 的 AI 日报

一个自动研究、归档并发布的个人 AI 行业日报。网站使用 React + TypeScript + Vite；日报内容、去重状态、
Agent 任务书和自动化工作流全部在同一个仓库内，不依赖 Evan 的 Mac、Kimi Desktop 或本机绝对路径。

## 工作方式

```text
每天 10:23（Asia/Shanghai）
  → GitHub Actions 启动 Kimi Code Agent
  → 并行检索、核验并语义去重
  → 写入 content/artifacts/*.json
  → 校验结构、来源 URL、不可变历史和改动范围
  → 生成 public/data/reports.json
  → 自动 commit / push
  → Vercel 收到 push 后构建并发布
```

职责划分：GitHub Actions 负责可能持续十几分钟的研究任务，Vercel 只负责静态站构建和托管。这样不会受
Vercel Serverless 单次执行时长和只读文件系统限制。

## 目录

```text
automation/AGENT_TASK.md              每日选题、核验和输出规范（唯一真源）
content/artifacts/                    每期完整日报 JSON 与 WayToAGI 补录
content/reported.md                   跨期语义去重档案
content/waytoagi-consumed.txt         WayToAGI 消费状态
scripts/build_data.py                 将仓内内容编译为前端数据
scripts/validate_content.py           内容 schema / URL / 重复校验
scripts/check_daily_changes.py        限制云端 Agent 的可写范围
scripts/fetch_builders.mjs            公共 AI builder / podcast feed
.github/workflows/daily-report.yml    每日无人值守任务
.github/workflows/ci.yml              每次提交的构建质量门
```

历史 Kimi run 已提取为纯 artifact，不包含本机运行日志或绝对路径。`dist/` 是可重建产物，不提交。

## 本地开发

要求 Node.js 22.19+ 和 Python 3.9+：

```bash
npm ci
npm run dev
```

完整校验：

```bash
npm run check
```

手动查看 WayToAGI 尚未消费的期次：

```bash
python3 scripts/waytoagi.py
```

## 首次上线

仓库推到 GitHub 后，还需要一次性添加 Kimi Secret 并连接 Vercel。完整步骤见 [DEPLOY.md](DEPLOY.md)。

自动任务也可以在 GitHub 的 **Actions → Daily AI report → Run workflow** 手动补跑；文件命名、内容去重和
改动范围检查让同日重跑保持幂等。
