# AI 日报 · 云端执行任务书

> 这是每日例程的唯一权威规范。运行器只负责唤起 Agent；选题、核验和落盘都按本文执行。
> 当前工作目录就是仓库根目录。禁止依赖 `/Users/...`、Kimi Desktop 或任何仓库外文件。

## 0. 执行契约

1. 全程使用 `Asia/Shanghai` 的日期和时间。工作流启动时已将报告日期冻结到
   `/tmp/report-date`；即使运行跨越午夜，文件中的日期仍是本轮唯一标准。开始选题前，必须完整读取
   `content/reported.md`、`/tmp/report-date`、`/tmp/priority-news.json`、`/tmp/ai-builders.json`、
   `/tmp/artificial-analysis.json`、`/tmp/waytoagi.json`。任一文件缺失、JSON 损坏或未读完即失败退出。
2. 只允许修改：
   - `content/artifacts/*.json`
   - `content/reported.md`
   artifact JSON 必须直接位于 `content/artifacts/` 下，不得新建子目录。
   `content/reported.md` 仅允许在文件末尾追加，已提交的历史必须保持为完整且逐字相同的前缀。
3. 不要执行 `git commit`、`git push`，不要改前端或工作流；外层 GitHub Actions 负责验证和提交。
4. CI 已禁用 Shell 和子 Agent。不得尝试 Bash；确定性采集、状态同步、验证和构建由外层工作流执行。
5. 如果 WebSearch/FetchURL 不可用、当前日期的核心来源普遍失败，或无法获得真实 URL，必须失败退出，
   不得用模型记忆补写新闻，也不得写空日报或推进消费状态。
6. 每个上海自然日只生成一份主日报。若当天主日报已存在，先逐个核对
   `/tmp/priority-news.json` 的 `required: true` 候选：有未覆盖候选时，必须新建下一个连续序号的
   `kind: "addendum"` 补刊；全部已覆盖时才可只处理 WayToAGI 或 Artificial Analysis attachment，
   若两者也都无新增则幂等结束。
   首次生成时文件名为 `content/artifacts/YYYY-MM-DD-1.json`；同日补刊使用
   `YYYY-MM-DD-2.json`、`-3.json` 依次连续编号。不得覆盖任何已提交的主刊或补刊，
   同一事件不得因重跑重复。确定性采集器可能已在本轮开始前为 `/tmp/waytoagi.json`
   明确列出的期次新建或刷新 attachment，或根据 `/tmp/artificial-analysis.json` 新建榜单变化
   attachment；Agent 不得重写这些文件。
7. 写完日报和 `content/reported.md` 后即可结束。外层工作流会同步 WayToAGI 与 Artificial Analysis
   状态、运行验证和构建；验证失败则整次运行不提交。不能为了通过验证而删除历史内容或放宽规则。
8. WayToAGI 是确定性采集的完整归档，不适用主日报的语义筛选、跨期去重或条数配额。外层质量门会把
   `/tmp/waytoagi.json` 与 attachment 逐期逐条核对；任一期不完整时整次运行失败，且不消费、不提交。

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
3. 普通搜索默认覆盖过去 24 小时（上海时间昨天 10:00 起）；周一覆盖周末 72 小时。
   官方优先监控固定用 72 小时重叠窗口，依靠稳定 id 和覆盖门禁去重，不得因为超过普通搜索的 24 小时而忽略。
4. 去重后不足 12 条，可扩展到 48–72 小时，并在条目中注明原始日期。绝不用旧闻凑数。
5. 写完 artifact 后，把全部新增事件追加到 `content/reported.md`，格式见第 7 节。

---

## 3. 信息采集

独立搜索必须并行进行。优先读取官方发布和原始材料，关键事实尽量用第二个可靠来源交叉验证。
每个媒体来源都必须保留支撑该 item 的 item-specific 文章、公告或帖子 URL；严禁用媒体首页、
频道/栏目页、标签页或站内搜索页冒充原文。找不到具体原文时就不得把该媒体挂在 `sources`；
如果因此无法留下至少一个可核验来源，则不收录该条。量子位官网直链必须是
`https://www.qbitai.com/YYYY/MM/<数字>.html`；网易、搜狐等转载必须在来源名中明确标注转载媒体，
并链接到具体转载文章。

### 3.0 官方重大候选（强制输入）

外层工作流已把官方 news/changelog 的确定性采集结果写到 `/tmp/priority-news.json`。该文件使用
72 小时重叠窗口；顶层 `sources` 记录直采/备用端点状态，`candidates` 中每条含稳定 `id`、
`url`、`evidenceUrls`、`matchTerms`、`summary/details` 和 `required`。

- `required: true` 是硬约束。先用稳定 id + 精确证据 URL + 全部匹配词核对 HEAD：
  更早日期已强覆盖的记为 `already_covered`、不得重复；本轮仍未强覆盖的必须进入今日主刊或补刊的
  `🔥 AI 重要事件`，普通配额和模型主观排序不能将它淘汰。
- 对应 item 必须写 `priorityIds: ["完整的 candidate.id"]`；`sources` 至少包含该候选的
  `url` 或 `evidenceUrls` 之一；`headline + summary` 必须明确出现全部 `matchTerms`。
- `summary/details` 是采集器从官方发布/release notes 提取的可用事实，可作为初稿上下文；
  artifact 必须保留 candidate 精确列出的官方证据 URL，并尽量补充第二来源。
- 采集器可用 Hacker News 发现指向 `x.ai/news/...` 的新链接，但只有在直取该官方文章并核对
  canonical URL、模型版本与发布日期后才会生成 required candidate；不要把社区标题本身当作证据。
- 官网入口页返回 403 不等于不可核验。必须沿 candidate 的具体文章、官方 docs/release notes、
  item-specific 官方 X 或可靠二级报道继续核验，不得因为一个泛用 URL 失败就静默弃收。
- 若仍无法写出可靠摘要，必须失败并明确报告该 candidate id，禁止把它当成可选新闻跳过。

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

WayToAGI 通常每天发布，但官方镜像可能滞后数日；尚未发布的日期会返回 HTTP 500。镜像期次页是
**服务端渲染（SSR）的完整 HTML，不是 SPA**。确定性采集器负责发现期次、解析正文，并在 Agent 启动前
同时写好 `/tmp/waytoagi.json` 与对应 attachment；不要再次 FetchURL 抓镜像或飞书页面，也不要从首页推荐位、
metadata 或模型记忆补全。

直接用 Read 完整读取 `/tmp/waytoagi.json`。顶层结构是：

```json
{
  "schemaVersion": 1,
  "sourceIndex": "https://www.waytoagi.com/zh/blog",
  "generatedAt": "2026-08-13T10:00:00+08:00",
  "issues": [
    {
      "stamp": "20260811",
      "date": "2026-08-11",
      "sourceUrl": "https://www.waytoagi.com/zh/blog/news-20260811",
      "sourceItemCount": 6,
      "items": [
        {
          "title": "……",
          "summary": "……",
          "url": "https://waytoagi.feishu.cn/wiki/..."
        }
      ]
    }
  ]
}
```

对 `issues` 中每一期，先核对采集器已写好的 attachment，然后把每条标题追加到 `content/reported.md`。
源中有几条，attachment 就必须有几条，顺序、`headline`、`summary` 和 item-specific 飞书 URL 都与输入逐字
一致；不得让模型润色、筛选、合并、删减或拆分，也不得把 WayToAGI 条目写入今天的主 artifact。任何不一致
都应失败退出，不要尝试自行修补或再次抓取。artifact 格式见第 6 节。

不要直接修改 `content/waytoagi-consumed.txt`。消费状态只由已通过完整性门禁的
`content/artifacts/waytoagi-YYYYMMDD.json` 派生；输入期次缺少 attachment、条数不等、顺序/标题映射错误、
原文 URL 不匹配或输入本身结构不完整时，必须让质量门失败，整次运行不消费也不提交。上游偶尔会让两个
不同条目共用同一个飞书 URL；此时仍按两个正文条目分别归档，不能按 URL 去重。不得手工推进、保留
孤立状态或以 `content/reported.md` 代替 attachment。

### 3.5 Artificial Analysis 模型榜单

外层确定性采集器已从 Artificial Analysis 官方公开 LLM Leaderboard 读取 Intelligence Index 前 10 名，
并与上一次通过质量门后提交的快照比较，结果写在 `/tmp/artificial-analysis.json`。监控覆盖新进榜、掉出
前 10、名次变化、显示分数变化与榜单方法版本变化；首次接入只建立 baseline，不把整张榜单误报为变化。

直接用 Read 完整读取该输入：

- `changes` 为空且 `artifact` 为 `null` 时，表示本轮没有榜单变化；不要自行创建榜单条目。
- `artifact` 非空时，确定性采集器已经把同一份 `document` 写到其 `path`。逐字段核对后保持文件不变，
  不要把其中 item 重复写进主 artifact 或 `content/reported.md`；榜单快照与 attachment 自身就是去重档案。
- 不要再次抓榜单、改写标题/摘要、筛选变化或把榜单条目塞进主日报六板块。`previousSnapshot` 只用于说明
  变化，`currentSnapshot` 要等所有门禁通过后才由外层同步；Agent 不得修改快照文件。
- 官方榜单抓取或解析失败会在 Agent 启动前 fail closed；失败不得解释成「排名无变化」。

榜单 attachment 使用 `content/artifacts/artificial-analysis-YYYYMMDD-HHMMSS.json`（上海采集时间），
同日多次真实变化会保留为多份不可变记录。板块标题固定为
`📊 Artificial Analysis 模型排名`，并通过 `attachTo` 合并到当天主日报。它和 WayToAGI 一样不占主日报
20–28 条配额；没有变化时不生成空板块。

### 3.6 GitHub Trending

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
| 5 | `🚀 AI 一人公司（OPC）` | 2–3 | 独立开发者案例、变现路径、Agent 基建与工具链 |
| 6 | `💻 GitHub Trending` | 4–6 | 总榜 + Python 榜；`note` 用 1–2 句总结本周期开源风向 |

WayToAGI 的 `🧭 WayToAGI 知识库精选` 与 Artificial Analysis 的
`📊 Artificial Analysis 模型排名` 不计入主日报，按确定性 attachment 单独写。
任何 required 官方候选的优先级高于上表普通条数配额：槽位不足时移出低优先级普通项，
不得删除 required；单刊容量不足时按每份 1–5 条写连续补刊。

板块 3、5 以及 expanded 分析用一句 `对你的映射：……` 结尾，给本周可执行的动作。`oneLiner`
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
          "priorityIds": ["lab:model-version"],
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
- 每条必须有非空 `headline`、`summary`、布尔值 `expanded` 和至少一个支撑该条的具体原文 HTTP(S) URL。
- 单一来源且未交叉验证，在该来源 `name` 后加 `（单一来源）`；传闻在 headline 明写「未经证实」。
- 不得拼 URL、引用搜索结果页、媒体首页或频道页，也不得把模型记忆当来源。
- `priorityIds` 只用于声明官方优先候选；普通条目可省略，声明后必须通过 id + 精确证据 URL + 全部匹配词的覆盖门禁。

### 5.1 同日补刊 artifact

当今日主刊已提交但仍有未覆盖 required 候选时，新建下一个连续文件序号：

```json
{
  "date": "2026-08-13 星期四",
  "kind": "addendum",
  "label": "第十三期·补刊",
  "generatedAt": "2026-08-13T12:30:00+08:00",
  "oneLiner": "📌 补刊：补录已核验的重大发布。",
  "sections": [
    {
      "title": "🔥 AI 重要事件",
      "items": [
        {
          "headline": "……",
          "summary": "……",
          "expanded": false,
          "priorityIds": ["lab:model-version"],
          "sources": [{"name": "官方来源（单一来源）", "url": "https://example.com/release"}]
        }
      ]
    }
  ]
}
```

补刊只能有一个 `🔥 AI 重要事件` 板块、1–5 条，且每条必须声明至少一个未覆盖的
`priorityIds`。不得拿旧闻、普通新闻或已覆盖候选凑数。文件名中的同日序号与全站中文期号是两套编号；
`label` 仍从 `reported.md` 最后一期递增，不得复用主刊期号。

---

## 6. 按原文日期归档（WayToAGI）

确定性采集器已把 `/tmp/waytoagi.json` 中每个 issue 写成
`content/artifacts/waytoagi-YYYYMMDD.json`。输入既可能包含尚未归档的新期次，也可能包含已有 attachment、
但上游后来增加、删除或修正条目的旧期次；采集器只会刷新输入明确列出的对应文件。Agent 必须保持这些文件
不变，只做读取核对和 `content/reported.md` 归档，不得借机改动其他历史 attachment，更不得覆盖主日报。
attachment 的 `items` 数量严格等于 `sourceItemCount`，且顺序、标题、摘要和原文 URL 与输入逐条完全一致。

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

每条必须同时保留两个来源：第一个是该 issue 的 `sourceUrl`；第二个是该输入 item 的专属飞书 `url`。
不得把多条内容统一改成近 7 日更新日志 URL，也不得用期次页替代 item-specific 原文 URL；但如果输入中
两个不同正文条目因上游数据问题恰好共用同一 item URL，仍须分别保留。归档顺序、标题映射和条数
必须与输入一致；不做语义筛选、去重、合并或删减。`attachTo` 让构建器把它追加到对应日期；目标日没有
主日报时，它会成为 `补录` 期。

外层完整性门禁会将 `/tmp/waytoagi.json` 和所有新建或刷新的 attachment 逐期逐条比较。只要有一个 issue
缺失，或任一条的标题、摘要、顺序、来源 URL、计数不一致，就失败退出；不得提交部分归档。门禁通过后，
外层才从当前 attachment 集合重新派生 `content/waytoagi-consumed.txt`，因此 attachment 是消费状态的唯一真源。

---

## 7. 回写去重存档

主日报在 `content/reported.md` 末尾追加：

```markdown
## YYYY-MM-DD（第N期）

- YYYY-MM-DD | 事件关键词（含关键数字与来源媒体名）
```

一行一条，覆盖主日报和本轮 WayToAGI 新增的全部条目。不要粘贴长摘要，但关键词要足够支持主日报下一轮
语义去重。注意：`content/reported.md` 只用于主日报去重和检索记录，不是 WayToAGI 的消费状态，也不得据此
跳过 `/tmp/waytoagi.json` 中的任何条目。
补刊也必须用独立的同日 heading 追加，且 heading 中的 label 与补刊 artifact 完全一致；
`reported.md` 本身不算 priority coverage 证据，必须在 artifact item 中完整声明。

---

## 8. 最终质量门

- 不得编造新闻、数字、链接、人名；抓不到就不写。
- 价格、融资、模型参数、榜单等关键事实优先官方来源，并尽量双源交叉验证。
- 任何来源失败都要在最终运行摘要中说明；全部核心源失败则让任务失败，不推进状态。
- 收尾前逐个列出 priority `required` 候选的 `covered_today` / `already_covered` 状态；任一 `missing` 都必须失败，不得静默跳过。
- WayToAGI 输入与 attachment 必须逐期逐条一对一完整匹配；完整性门禁失败时不消费、不提交任何本轮改动。
- Artificial Analysis 输入、差异、榜单 attachment 与当前快照必须逐字匹配；门禁失败时不推进快照。
- 你结束时的 `git diff` 中不得出现第 0 节允许范围以外的文件。
- 写入完成后只输出：新增期号、条数、priority required/covered/missing 数、
  Artificial Analysis 榜单是否变化、WayToAGI 补录日期、失败来源；不要复述全文。
