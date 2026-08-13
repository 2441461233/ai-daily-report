# Evan 的 AI 日报

一个自动研究、归档并发布的个人 AI 行业日报。网站使用 React + TypeScript + Vite；日报内容、去重状态、
Agent 任务书和自动化工作流全部在同一个仓库内，不依赖 Evan 的 Mac、Kimi Desktop 或本机绝对路径。

## 工作方式

```text
每天 10:00（Asia/Shanghai / UTC 02:00）
  → GitHub Actions 启动 Kimi Code Agent
  → 确定性采集 WayToAGI，并直接写入完整 attachment 与 /tmp/waytoagi.json
  → 并行检索、核验主日报并语义去重
  → 写入 content/artifacts/*.json
  → 校验结构、来源 URL、WayToAGI 逐条完整性、不可变历史和改动范围
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
content/waytoagi-consumed.txt         由 WayToAGI attachment 严格派生的消费状态
scripts/build_data.py                 将仓内内容编译为前端数据
scripts/validate_content.py           内容 schema / URL / 重复校验
scripts/validate_waytoagi_run.py      WayToAGI 源数据与 attachment 逐条完整性门禁
scripts/check_daily_changes.py        限制云端 Agent 的可写范围
scripts/fetch_builders.mjs            公共 AI builder / podcast feed
tests/test_waytoagi.py                SSR 解析、去重与上游刷新回归测试
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

WayToAGI 官方镜像期次页是 SSR HTML。云端采集器会把每期完整解析为 `/tmp/waytoagi.json`，并在 Agent
启动前直接写好 attachment；每个 item 都保留标题、摘要和专属飞书原文 URL。Agent 只负责把标题登记到
`reported.md`，不得改写采集结果。质量门会逐字核对源条数、顺序、标题、摘要和链接；任何不完整或被改写的
attachment 都不会被消费或提交。
上游若让两个不同条目共用同一个飞书链接，仍会按两个正文条目分别保存，不会按 URL 错删。
若上游后来补充或修正旧期条目，结构化输入会重新列出该期，并只重写对应 WayToAGI attachment；主日报仍
不可覆盖。`content/waytoagi-consumed.txt` 只由已验证的 `waytoagi-YYYYMMDD.json` 派生，不能手工推进。

## 首次上线

仓库推到 GitHub 后，还需要一次性添加 Kimi Secret 并连接 Vercel。完整步骤见 [DEPLOY.md](DEPLOY.md)。

自动任务也可以在 GitHub 的 **Actions → Daily AI report → Run workflow** 手动补跑；文件命名、内容去重和
改动范围检查让同日重跑保持幂等。
