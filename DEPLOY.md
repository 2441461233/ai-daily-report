# GitHub Actions + Vercel 部署

本项目只需一次配置，之后每天自动研究、提交和发布。定时研究在 GitHub Actions 运行；Vercel 监听
`main` 分支并发布网站。

## 1. GitHub Actions 密钥

打开仓库：

**Settings → Secrets and variables → Actions → Secrets → New repository secret**

添加：

| 名称 | 必需 | 值 |
|---|---|---|
| `KIMI_API_KEY` | 是 | Kimi / Moonshot 开放平台 API Key |
| `KIMI_WEB_SEARCH_API_KEY` | 否 | 独立搜索 Key；不填时复用 `KIMI_API_KEY` |
| `KIMI_WEB_FETCH_API_KEY` | 否 | 独立网页读取 Key；不填时复用 `KIMI_API_KEY` |

密钥必须和 API endpoint 属于同一平台。工作流默认使用 K3 官方国际平台：

- 模型：`kimi-k3`
- 上下文：`1048576`（1M），思考强度 `max`
- API：`https://api.moonshot.ai/v1`
- 搜索：`https://api.moonshot.ai/v1/search`
- 网页读取：`https://api.moonshot.ai/v1/fetch`

如 Key 来自其他 Kimi 平台，在同页 **Variables** 新建对应变量覆盖默认值：

| Variable | 用途 |
|---|---|
| `KIMI_MODEL` | 模型 ID，例如 `kimi-k2.5` |
| `KIMI_BASE_URL` | 模型 API 根地址 |
| `KIMI_WEB_SEARCH_BASE_URL` | WebSearch 服务地址 |
| `KIMI_WEB_FETCH_BASE_URL` | FetchURL 服务地址 |
| `KIMI_CONTEXT_SIZE` | 上下文长度，K3 默认 `1048576` |

若 Key 来自中国区 `platform.moonshot.cn`，把三个 endpoint 变量对应改为
`https://api.moonshot.cn/v1`、`https://api.moonshot.cn/v1/search` 和
`https://api.moonshot.cn/v1/fetch`。

仓库 **Settings → Actions → General → Workflow permissions** 需允许 **Read and write permissions**，
否则日报能生成但无法自动 commit。

在 **Actions → Daily AI report → Run workflow** 手动跑一次。成功时会新增日报 commit；失败不会写入
半成品。之后工作流每天 UTC 02:23（上海 10:23）自动运行。GitHub 的 cron 可能延迟几分钟，不保证准点。

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

Vercel 不需要 Kimi API Key，也不配置 Cron。每次 Daily AI report 推送新 commit，Vercel Git 集成会自动
触发新的 Production Deployment。

## 3. 验证与补跑

- GitHub **Actions → CI**：内容校验、lint、生产构建均应为绿色。
- GitHub **Actions → Daily AI report**：可查看检索/生成日志并手动补跑。
- Vercel **Deployments**：最新 deployment 应对应日报机器人的 commit。
- 若当天无 commit，优先看 Daily AI report 日志；质量门会明确报告缺 Secret、来源失败、JSON/URL
  不合规或 Agent 越界修改。

## 4. 关闭旧本机任务

云端首次手动运行成功后，关闭 Kimi Desktop 里旧的 `ai-daily` 定时任务，避免本机任务和 GitHub Actions
在同一天各生成一份。旧任务关闭前不要同时让两边运行。
