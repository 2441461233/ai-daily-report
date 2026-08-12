# AI 日报 · 云端执行任务书

> 这是每日例程的唯一权威规范。运行器只负责唤起 Agent；选题、核验和落盘都按本文执行。
> 当前工作目录就是仓库根目录。禁止依赖 `/Users/...`、Kimi Desktop 或任何仓库外文件。

## 0. 执行契约

1. 全程使用 `Asia/Shanghai` 的日期和时间。先完整阅读 `content/reported.md`，再开始采集。
2. 只允许修改：
   - `content/artifacts/*.json`
   - `content/reported.md`
3. 不要执行 `git commit`、`git push`，不要改前端或工作流；外层 GitHub Actions 负责验证和提交。
4. CI 已禁用 Shell 和子 Agent。不得尝试 Bash；确定性采集、状态同步、验证和构建由外层工作流执行。
5. 如果 WebSearch/FetchURL 不可用、当前日期的核心来源普遍失败，或无法获得真实 URL，必须失败退出，
   不得用模型记忆补写新闻，也不得写空日报或推进消费状态。
6. 每个主日报必须是一个新文件。文件名为 `content/artifacts/YYYY-MM-DD-N.json`，`N` 取当日
   已有主日报的下一个序号。不得覆盖已提交 artifact；同一事件不得因任务重跑而重复。
7. 写完日报和 `content/reported.md` 后即可结束。外层工作流会同步 WayToAGI 状态、运行验证和构建；
   验证失败则整次运行不提交。不能为了通过验证而删除历史内容或放宽规则。

---

## 1. 读者画像与筛选标准

Evan 是 AI 产品经理，会写代码做项目，目标是做**一人公司（OPC / 超级个体）**；同时是创作者，
深度关注 **AI MV（AIMV）、AI 音乐、AI 影视与媒体娱乐生态**。

筛选优先级由高到低：

1. **能马上上手的工具/能力**：新模型、新 API、新工具链。写清定价、开放状态、地区限制、是否开源。
2. **变现与商业机会**：真实收入数字、定价策略、获客渠道、比赛/激励计划、平台红利期。
3. **产品形态与竞品情报**：产品和交互怎么做、哪些方向有效或失败，用产品经理视角拆解。

次一级仍要覆盖：AI 音乐/影视的版权判例、平台政策、监管与分成规则。

不要写：纯股价噪音、无新信息的估值传闻、无技术细节的「宣布布局 AI」、连续三天无实质进展的旧闻。

---

## 2. 增量去重与时间窗口

1. `content/reported.md` 是语义去重的唯一依据。同一事件即使换了表述也要跳过。
2. 只有出现新价格、新裁决、新数据、正式发布等**实质性进展**才可再报；headline 前缀写
   `【进展】`，summary 明确相比上次新增了什么。
3. 默认覆盖过去 24 小时（上海时间昨天 10:00 起）；周一覆盖周末 72 小时。
4. 去重后不足 12 条，可扩展到 48–72 小时，并在条目中注明原始日期。绝不用旧闻凑数。
5. 写完 artifact 后，把全部新增事件追加到 `content/reported.md`，格式见第 7 节。

---

## 3. 信息采集

独立搜索必须并行进行。优先读取官方发布和原始材料，关键事实尽量用第二个可靠来源交叉验证。

### 3.1 X / AI builder 圈（只读公共 feed）

外层工作流已把公共 feed 写到 `/tmp/ai-builders.json`，直接用 Read 读取。JSON 含 `x`（推文、url、
bio）、`podcasts`（含 transcript）、`blogs`。只使用 JSON 中真实存在的内容和
URL，不访问或爬取 x.com，不猜职位。纯转发、纯情绪不收录。feed 约有一天延迟属正常。

若 podcast URL 只是频道页而非具体单集，搜索真实单集链接；找不到则不放链接或不收录。

### 3.2 中文源

机器之心、量子位、新智元、智东西、36氪、钛媒体、虎嗅、财新、晚点 LatePost、IT之家、极客公园、
InfoQ 中文，以及阿里通义、字节 Seed、腾讯混元、DeepSeek、月之暗面、MiniMax、智谱、阶跃星辰等官方渠道。

### 3.3 海外源

| 类别 | 来源 |
|---|---|
| 官方与科技媒体 | OpenAI、Anthropic、Google DeepMind、Meta AI、xAI、Mistral 官方博客/changelog；TechCrunch、The Verge、Ars Technica、VentureBeat、The Information、Axios、Hacker News |
| AI 音乐/影视与版权 | Billboard、MBW、Music Ally、Variety、THR、Digital Music News；Suno、Udio、ElevenLabs、Runway、Luma、Pika 官方发布与诉讼进展 |
| 独立开发者变现 | Indie Hackers、Product Hunt、Reddit r/SaaS/r/indiehackers、Starter Story、YC、Stripe/Lemon Squeezy 真实数据 |
| 论文与前沿 | arXiv cs.AI/cs.CL/cs.CV、Hugging Face Daily Papers/Trending Models、实验室技术报告和 engineering blog |

### 3.4 WayToAGI 知识库精选

它每天发布但镜像常滞后数日，缺失日期会返回 HTTP 500。外层工作流已把探测结果写到
`/tmp/waytoagi.txt`，直接用 Read 读取；每行是 `YYYYMMDD<TAB>URL`。对每个 URL 用 FetchURL 取条目并
经过同样的语义去重。首页是 SPA，不要用
首页抓正文。内容按**原文日期**单独归档，不能写进今天主 artifact，格式见第 6 节。

不要直接修改 `content/waytoagi-consumed.txt`。外层工作流只会在 attachment 成功写入后，从 artifact
文件名确定性地同步消费状态。

### 3.5 GitHub Trending

检查 `https://github.com/trending?since=daily` 和
`https://github.com/trending/python?since=daily`，筛选 AI 项目。每条说明「它是什么 + 为什么值得关注」，
不能只列仓库名或 star 数。

---

## 4. 主日报结构

主日报 20–28 条，只有 1–2 条 `expanded: true`；其余每条 1–3 句。全文中文，专有名词保留英文。

| # | 板块标题（逐字一致） | 条数 | 要求 |
|---|---|---:|---|
| 1 | `🔥 AI 重要事件` | 3–5 | 模型发布、重大公司动态、融资、政策监管，只选真正重要的 |
| 2 | `🎬 AI 创作 · 视频/音乐/媒体娱乐` | 3–4 | AIMV、音乐、影视生态、版权、比赛与变现；兼顾视频与音乐 |
| 3 | `🌍 海外观察` | 3–4 | builder 原创观点、海外变现案例、产业/版权、播客高信息量判断 |
| 4 | `📄 论文与技术前沿` | 2–3 | 说清解决什么问题，以及对产品意味着什么，不堆术语 |
| 5 | `💻 GitHub Trending` | 4–6 | 总榜 + Python 榜；`note` 用 1–2 句总结本周期开源风向 |
| 6 | `🚀 AI 一人公司（OPC）` | 2–3 | 独立开发者案例、变现路径、Agent 基建与工具链 |

WayToAGI 的 `🧭 WayToAGI 知识库精选` 不计入主日报，按第 6 节单独写。

板块 3、6 以及 expanded 分析用一句 `对你的映射：……` 结尾，给本周可执行的动作。`oneLiner`
以 `📌 今日一句话：` 开头，把当天信息浓缩成一个有取舍的行动判断。

---

## 5. 主日报 artifact

写到 `content/artifacts/YYYY-MM-DD-N.json`：

```json
{
  "date": "2026-08-10 星期一",
  "label": "第九期",
  "generatedAt": "2026-08-10T10:15:00+08:00",
  "oneLiner": "📌 今日一句话：……",
  "sections": [
    {
      "title": "🔥 AI 重要事件",
      "items": [
        {
          "headline": "一句话标题，含关键数字",
          "summary": "1–3 句正文；深度条目是一段完整分析",
          "expanded": false,
          "sources": [
            {"name": "官方来源", "url": "https://example.com/original"},
            {"name": "交叉来源", "url": "https://example.org/report"}
          ]
        }
      ]
    }
  ]
}
```

字段规则：

- `date` 必须是 `YYYY-MM-DD 星期X`；`generatedAt` 必须含 `+08:00`。
- `label` 沿用全站连续期号。根据 `content/reported.md` 最后一期计算中文期号，不能每天从第一期重置。
- 每条必须有非空 `headline`、`summary`、布尔值 `expanded` 和至少一个真实可访问的 HTTP(S) URL。
- 单一来源且未交叉验证，在该来源 `name` 后加 `（单一来源）`；传闻在 headline 明写「未经证实」。
- 不得拼 URL、引用搜索结果页或把模型记忆当来源。

---

## 6. 按原文日期归档（WayToAGI）

每个已消费期次写 `content/artifacts/waytoagi-YYYYMMDD.json`：

```json
{
  "date": "2026-08-05",
  "label": "WayToAGI 精选",
  "attachTo": "2026-08-05",
  "generatedAt": "2026-08-05T23:59:00+08:00",
  "sections": [
    {
      "title": "🧭 WayToAGI 知识库精选",
      "note": "来自 WayToAGI 知识库精选 8/5，按原文日期归档。",
      "items": [
        {
          "headline": "……",
          "summary": "……",
          "expanded": false,
          "sources": [
            {"name": "WayToAGI 精选 8/5", "url": "https://www.waytoagi.com/zh/blog/news-20260805"},
            {"name": "原文（飞书）", "url": "https://waytoagi.feishu.cn/wiki/..."}
          ]
        }
      ]
    }
  ]
}
```

每条必须同时保留 WayToAGI 期次页和飞书原文两个来源。`attachTo` 让构建器把它追加到对应日期；目标日
没有主日报时，它会成为 `补录` 期。

---

## 7. 回写去重存档

主日报在 `content/reported.md` 末尾追加：

```markdown
## YYYY-MM-DD（第N期）

- YYYY-MM-DD | 事件关键词（含关键数字与来源媒体名）
```

一行一条，覆盖主日报和本轮 WayToAGI 新增的全部条目。不要粘贴长摘要，但关键词要足够支持下一轮语义去重。

---

## 8. 最终质量门

- 不得编造新闻、数字、链接、人名；抓不到就不写。
- 价格、融资、模型参数、榜单等关键事实优先官方来源，并尽量双源交叉验证。
- 任何来源失败都要在最终运行摘要中说明；全部核心源失败则让任务失败，不推进状态。
- 你结束时的 `git diff` 中不得出现第 0 节允许范围以外的文件。
- 写入完成后只输出：新增期号、条数、WayToAGI 补录日期、失败来源；不要复述全文。
