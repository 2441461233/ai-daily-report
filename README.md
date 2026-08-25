# Evan 的 AI 日报

一个自动研究、归档并发布的个人 AI 行业日报。网站使用 React + TypeScript + Vite；日报内容、去重状态、
生成器和自动化工作流全部在同一个仓库内，不依赖 Evan 的 Mac、Kimi Desktop 或本机绝对路径。

## 工作方式

```text
每天 07:17 和 07:47（Asia/Shanghai）提前启动高质量主运行
  → 直采官方 news/changelog，生成强制重大候选清单
  → 读取 Artificial Analysis Intelligence Index 前 10，与已验证快照比较
  → 确定性采集 WayToAGI，并直接写入完整 attachment 与 /tmp/waytoagi.json
  → 将已校验的官方候选、Artificial Analysis 与 WayToAGI 输入一起冻结后传入隔离副本
  → Qwen3.7 Plus 联网发现候选，程序再直取模型选中的精确公开原文
  → 第二次严格 JSON Schema 调用只按证据卡编辑主日报
  → 第三次独立审稿逐条对照冻结原文，任一事实不受支持则整稿退回
  → 生成完成后移除 DashScope 密钥，再检查新 artifact manifest 和 reported.md 追加边界
  → Qwen 调用、硬门或候选预检失败时，运行此前不需要模型 API Key 的 Copilot 研究流程
  → 两条研究路径都失败时，才生成完全确定性的“自动恢复版”
在 08:47、09:17 安排独立确定性恢复检查；另一条不受生产并发取消影响的 watchdog 会在 09:40 检查远端主刊
  → 若当日主刊仍缺失，取消耗时的模型路径，立即采集并发布确定性恢复版
10:17 仅作为刊后新鲜度、重大候选与 Artificial Analysis 排名复查
  → 写入 content/artifacts/*.json
  → 强制核对每条重大候选的 id + 官方证据 URL + 版本匹配词
  → 有榜单变化时逐字核对独立 attachment；通过后才推进排名快照
  → 校验结构、榜单差异、WayToAGI 逐条完整性、不可变历史和改动范围
  → 生成 public/data/reports.json
  → 自动 commit / push
  → Vercel 收到 push 后构建并发布
```

职责划分：GitHub Actions 负责研究任务（Qwen 三阶段主链最长 25 分钟，整个生成 job 最长 60 分钟），Vercel 只负责静态站构建和托管。这样不会受
Vercel Serverless 单次执行时长和只读文件系统限制。

## 目录

```text
automation/AGENT_TASK.md              旧研究 Agent 规范，保留为编辑参考
content/artifacts/                    每期完整日报 JSON 与确定性补录
content/reported.md                   跨期语义去重档案
content/artificial-analysis-snapshot.json  Artificial Analysis 最近一次已验证榜单快照
content/waytoagi-consumed.txt         由 WayToAGI attachment 严格派生的消费状态
scripts/build_data.py                 将仓内内容编译为前端数据
scripts/build_fallback_report.py      无模型/API 的确定性自动恢复版生成器
scripts/generate_qwen_report.py       Qwen 联网研究、证据冻结与严格结构化编辑器
scripts/validate_content.py           内容 schema / URL / 重复校验
scripts/fetch_official_priority_sources.mjs  官方重大发布直采与备用路径
scripts/fetch_artificial_analysis.mjs  官方 Intelligence Index 前 10 与增量比较
scripts/validate_priority_coverage.py 重大候选的输入与入刊覆盖门禁
scripts/validate_artificial_analysis_run.py  榜单输入、快照与 attachment 完整性门禁
scripts/validate_waytoagi_run.py      WayToAGI 源数据与 attachment 逐条完整性门禁
scripts/check_daily_changes.py        限制云端生成任务的可写范围
scripts/fetch_builders.mjs            公共 AI builder / podcast feed
tests/                               官方源、覆盖门禁、补刊与 WayToAGI 回归测试
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

Artificial Analysis 监控直接读取官方公开 SSR 榜单与方法页，不需要额外 API key。系统保存 Intelligence
Index 前 10 的最近一次已验证快照；新进榜、掉榜、名次、显示分数、模型标记或方法版本发生变化时，才生成
`📊 Artificial Analysis 模型排名` attachment。首次接入只建立基线，抓取或解析失败会终止运行，不会伪装成
「无变化」。

手动查看 WayToAGI 尚未消费的期次：

```bash
python3 scripts/waytoagi.py
```

WayToAGI 官方镜像期次页是 SSR HTML。云端采集器会把每期完整解析为 `/tmp/waytoagi.json`，并在模型
调用前直接写好 attachment；每个 item 都保留标题、摘要和专属飞书原文 URL。确定性归档器把标题登记到
`reported.md`，模型不得改写采集结果。质量门会逐字核对源条数、顺序、标题、摘要和链接；任何不完整或被改写的
attachment 都不会被消费或提交。
上游若让两个不同条目共用同一个飞书链接，仍会按两个正文条目分别保存，不会按 URL 错删。
若上游后来补充或修正旧期条目，结构化输入会重新列出该期，并只重写对应 WayToAGI attachment；主日报仍
不可覆盖。`content/waytoagi-consumed.txt` 只由已验证的 `waytoagi-YYYYMMDD.json` 派生，不能手工推进。

官方优先采集用 72 小时重叠窗口，并为每个旗舰模型发布生成稳定 id、官方证据和版本匹配词。
比如 `x.ai/news` 入口返回 403 时，采集器会改用官方 API release notes；HN 只用于发现精确的
`x.ai/news/...` 链接，必须再直取具体官方文章并核对 canonical URL、模型版本和日期才会升级为
required candidate。任一 required 候选未在当日主刊或补刊中完整覆盖，
工作流都会明确失败；不会再以「页面打不开」为由静默弃收。

## 首次上线

仓库推到 GitHub 后，在 Actions Repository secrets 中配置 `DASHSCOPE_API_KEY`；Vercel 不需要该密钥。
Qwen API 不可用或稿件未通过生产门时，工作流先运行此前的无模型 API Key 研究流程；若它也失败，
再用已验证的公开 feed 发布“自动恢复版”。完整步骤见
[DEPLOY.md](DEPLOY.md)。

自动任务也可以在 GitHub 的 **Actions → Daily AI report → Run workflow** 手动补跑。`quality`
会保留 Qwen 与无 Key 研究链，`deadline` 则直接走确定性恢复版。若发现未覆盖的强制候选，
同日重跑会新建连续编号的小型补刊；若候选均已覆盖，才保持幂等。主刊与已提交补刊均不可覆盖。
