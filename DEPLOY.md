# GitHub Actions + Vercel 部署

本项目只需一次配置，之后每天自动研究、提交和发布。定时研究在 GitHub Actions 运行；Vercel 监听
`main` 分支并发布网站。

## 1. GitHub Actions 与 Qwen 密钥

日报主刊使用阿里云百炼 `qwen3.7-plus`。在仓库 **Settings → Secrets and variables → Actions** 创建
Repository secret `DASHSCOPE_API_KEY`。密钥只注入模型步骤，不写进仓库、构建产物或 Vercel。

- 完整密钥若曾出现在聊天、日志或截图中，应先在百炼控制台轮换，再更新 Repository secret。
- Qwen 无权限、额度暂时不可用、超时或输出未通过门禁时，工作流会先运行此前不需要模型 API Key 的
  Copilot 研究流程；若它也失败，再用已验证的官方候选与 builder feed 发布带 `·自动恢复版` 标签的
  确定性日报。因此单一模型通道故障不会造成整日空档。

Qwen 在 `$RUNNER_TEMP` 的仓库副本中运行。已校验的 `/tmp/priority-news.json`、
`/tmp/artificial-analysis.json`、`/tmp/ai-builders.json` 与 `/tmp/waytoagi.json` 作为同一批冻结输入传给生成器。
第一阶段使用内置 web search 发现候选 URL，程序只对模型选中的公开原文做直接 HTTP 读取、正文抽取和哈希冻结；无法直读或多 URL 混合归因的证据不会进入编辑器。第二阶段只按冻结证据卡输出严格 JSON；
第三阶段由新调用逐条核对人物身份、数字、归因、因果和结论强度，任何 fail 都会放弃整份 Qwen 稿；
程序自动复制来源并拒绝未知或重复 evidenceId、凭空数字、Artificial Analysis 主刊重复、板块错误和
强制候选遗漏。Qwen 调用返回后，工作流立即移除 `DASHSCOPE_API_KEY`，再运行内容与覆盖校验。
新 artifact manifest 和 `reported.md` 追加前缀还会经过独立候选预检；该预检失败也会先触发旧 Copilot 路由，
而不是直接降到确定性恢复版。只有通过这些门禁的新日报文件才会复制回主工作区。

Qwen 外层限时为 35 分钟，覆盖最多两批并发 research、editor 与独立审稿；旧 Copilot 安装和运行分别最长 8 与 45 分钟。
生成 job 总限时为 120 分钟，为三层路由之后的确定性门禁与产物上传保留余量。
Qwen 路由每天的估算费用硬上限为 ¥1.00；研究、编辑和审稿都在发起请求前预留最坏费用，预算不足时不会发起该请求，而是转入无 Key 兜底。

仓库 **Settings → Actions → General → Workflow permissions** 需允许 **Read and write permissions**，
否则日报能生成但无法自动 commit。

在 **Actions → Daily AI report → Run workflow** 手动跑一次。成功时会新增日报 commit；失败不会写入
半成品。之后工作流每天 UTC 02:17（上海 10:17）主运行，UTC 02:47（上海 10:47）再做一次幂等兜底。两次都避开 GitHub Actions 的整点高负载窗口。

工作流会先把 WayToAGI 官方 SSR 镜像的待处理期次完整解析到 `/tmp/waytoagi.json`，并由确定性采集器直接
生成一对一的 `waytoagi-YYYYMMDD.json` attachment；模型不参与改写。每期条数、顺序、标题、摘要和每条
专属飞书原文 URL 都会经过完整性门禁；任一条缺失或错配时，本轮不会更新消费状态，也不会提交。消费状态
`content/waytoagi-consumed.txt` 只由已验证 attachment 派生，不需要也不允许手工维护。若上游后来补充或
修正旧期条目，输入会重新列出该期并刷新对应 WayToAGI attachment；这不会授权覆盖任何主日报。
采集结果及本轮 WayToAGI 文件清单会在模型调用前计算哈希，摘要保存在 Actions 步骤输出这一
模型不可写的边界。质量门在调用任何仓库脚本前，还会用内联白名单拒绝越权文件修改；提交 job 会在
干净 checkout 中再次校验摘要与覆盖，不能通过同时改输入和校验脚本绕过门禁。artifact 只允许位于顶层目录，
`content/reported.md` 也会与 HEAD 比较并强制 append-only，避免嵌套文件或重写历史绕过检查。

工作流同时直采官方 news/changelog 生成 `/tmp/priority-news.json`，在模型调用前校验结构和新鲜度并将摘要封存到步骤输出。
模型后的覆盖门禁会对每个 required 候选同时核对稳定 id、精确官方 URL 和模型/版本匹配词。
单个官网入口 403 时可从官方 release notes 降级取证；HN 发现信号只在直取官方具体文章并核对
canonical URL、模型版本和日期后才会入选。重要候选缺失则整轮失败。若当日主刊已存在，
未覆盖候选会进入新的连续编号补刊，不覆盖旧主刊。

生成 job 无论成败都会尝试上传保留 14 天的 `daily-diagnostics-*` artifact，内含官方候选输入、
来源降级记录、脱敏 Qwen usage、Token/联网搜索成本估算、质量门输出与 git diff，便于区分「未发现」、「核验失败」和「覆盖失败」。

## 2. 连接 Vercel

1. 登录 Vercel，选择 **Add New → Project**。
2. Import `2441461233/ai-daily-report`。
3. Framework 会读取仓库内 `vercel.json`；确认：

   | 设置 | 值 |
   |---|---|
   | Framework | Vite |
   | Install Command | `npm ci` |
   | Build Command | `npm run build` |
   | Output Directory | `dist` |

4. 点击 Deploy。

Vercel 不需要任何模型 API Key，也不配置 Cron。每次 Daily AI report 推送新 commit，Vercel Git 集成会自动
触发新的 Production Deployment。

## 3. 验证与补跑

- GitHub **Actions → CI**：内容校验、lint、生产构建均应为绿色。
- GitHub **Actions → Daily AI report**：可查看检索/生成日志、下载 `daily-diagnostics-*` 并手动补跑。
- Vercel **Deployments**：最新 deployment 应对应日报机器人的 commit。
- 若当天无 commit，优先看 Daily AI report 日志；质量门会明确报告 Qwen/恢复版状态、来源失败、JSON/URL
  不合规、WayToAGI 归档不完整或生成任务越界修改。

## 4. 关闭旧本机任务

云端首次手动运行成功后，关闭 Kimi Desktop 里旧的 `ai-daily` 定时任务，避免本机任务和 GitHub Actions
在同一天各生成一份。旧任务关闭前不要同时让两边运行。
