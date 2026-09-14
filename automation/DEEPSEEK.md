# DeepSeek V4.1 Flash 日报

当前主模型为 DeepSeek V4.1 Flash，模型 ID 为 `deepseek-flash`。模型接口仅直连
`https://api.deepseek.com`，使用 GitHub Actions Secret `DEEPSEEK_API_KEY`。

`scripts/collect_daily_evidence.py` 独立采集官方 RSS、可读公开原文、arXiv 摘要、
GitHub 当日 Trending 和 Follow Builders 作者动态。原文打不开时只能按明确标注的
官方 RSS 摘要写作；社区讨论时间不等于原文发布日期。DeepSeek 不依赖内置搜索工具。

`scripts/generate_deepseek_report.py` 先检查余额与模型权限，再按来源 ID 选题、编辑
结构化主刊，最后独立逐条审稿。来源 URL 由程序复制。原有结构、事实依据、强制候选、
附件隔离和历史不可覆盖检查继续生效。最多三轮修订，每轮均重新验证。

每次生成运行的费用预留上限为 **US$0.45**，可通过 `DEEPSEEK_COST_CAP_USD` 配置。
请求前按峰值单价预留最坏费用；返回后记录真实 Token、缓存命中及按调用开始时段
估算的美元费用。网络响应结果未知时仍占用预留。费用是估算值，以供应商账单为准。
计费来源为 [DeepSeek 官方定价](https://api-docs.deepseek.com/quick_start/pricing/)。

## 当日试刊

在 Actions → Daily AI report → Run workflow 选择分支，设置 `route=quality`、
`preview=true`。试刊 job 只有仓库读取权限，只在临时 checkout 中移除当日旧副本，
生产生成与提交 job 均跳过，不会覆盖线上日报。

下载 `deepseek-preview-*` artifact 查看 `report.html`、`report.md`、最终 JSON、
冻结来源、每轮候选稿、审稿结果和 `deepseek-usage.json`。试刊资料保留七天。

生产运行保留既有截止时间恢复机制。质量路径未成功采用 DeepSeek 时，`model-health`
会在提交尝试结束后明确失败；恢复版可以发布，但不会把模型故障隐藏为绿色成功。

旧 `generate_qwen_report.py` 保留为共享证据门禁及历史 Qwen 实现。
工作流内部的 `qwen` step ID 暂时保留以复用既有隔离、预检与提交逻辑；它实际执行的是 DeepSeek。
