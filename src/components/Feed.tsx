import { useState } from 'react'
import { site } from '@/config'
import type { NewsItem, Report } from '@/types/report'
import { itemCount } from '@/types/report'

/* GitHub Trending rows are a long "a、b、c" sentence — split the repo names
   into chips so they scan as discrete units instead of a wall of text */
function Headline({ text }: { text: string }) {
  const m = text.match(/^(GitHub Trending[^：:]*?)[：:]\s*(.+)$/)
  if (!m) return <>{text}</>
  const repos = m[2].split('、').map((s) => s.trim()).filter(Boolean)
  return (
    <>
      {m[1]}：
      <span className="repo-chips">
        {repos.map((r, i) => (
          <span key={i} className="repo-chip">
            {r}
          </span>
        ))}
      </span>
    </>
  )
}

function SourceChips({ item }: { item: NewsItem }) {
  const linked = item.sources.filter((s) => s.url)
  return (
    <span className="news-src">
      {item.flag && <span className="flag-chip">单一来源</span>}
      {item.sources.map((s, i) =>
        s.url ? (
          <a
            key={i}
            className="src-chip is-link"
            href={s.url}
            target="_blank"
            rel="noopener noreferrer"
            title={`查看原文：${s.url}`}
          >
            {s.name}
            <span className="src-arrow">↗</span>
          </a>
        ) : (
          <span key={i} className="src-chip" title="这条来源没有存下原文链接">
            {s.name}
          </span>
        ),
      )}
      {linked.length === 0 && <span className="src-none">无原文链接</span>}
    </span>
  )
}

/* summaries longer than ~2 lines are clamped by default; the reader can
   expand inline — this keeps a 16-item issue scannable */
function NewsRow({ item, num }: { item: NewsItem; num: number }) {
  const [open, setOpen] = useState(false)
  const longSummary = (item.summary?.length ?? 0) > 90
  return (
    <div className={`news-row ${item.expanded ? 'focus' : ''}`}>
      <span className="news-num tnum">{String(num).padStart(2, '0')}</span>
      <div className="min-w-0 flex-1">
        <p className="news-text">
          {item.expanded && <span className="focus-chip">焦点</span>}
          <Headline text={item.text} />
        </p>
        {item.summary && (
          <p className={`news-summary ${longSummary && !open ? 'clamped' : ''}`}>{item.summary}</p>
        )}
        {longSummary && (
          <button className="sum-toggle mono" onClick={() => setOpen((v) => !v)}>
            {open ? '收起 ↑' : '展开全文 ↓'}
          </button>
        )}
        <SourceChips item={item} />
      </div>
    </div>
  )
}

interface Props {
  report: Report
  moduleFilter: string | null
  hasPrev: boolean
  hasNext: boolean
  onPrev: () => void
  onNext: () => void
}

export default function Feed({ report, moduleFilter, hasPrev, hasNext, onPrev, onNext }: Props) {
  const sections = report.sections
    .map((section, originalIndex) => ({ section, originalIndex }))
    .filter(({ section }) => moduleFilter === null || section.title === moduleFilter)
  const sectionOffsets = sections.reduce<number[]>(
    (offsets, { section }) => [
      ...offsets,
      offsets[offsets.length - 1] + section.items.length,
    ],
    [0],
  )

  const oneLiner = (report.oneLiner ?? '').replace(/^📌\s*今日一句话[：:]\s*/, '')
  const sectionId = (originalIndex: number) =>
    `report-${report.slug}-section-${originalIndex + 1}`
  const jumpToSection = (originalIndex: number) => {
    const id = sectionId(originalIndex)
    const section = document.getElementById(id)
    const heading = document.getElementById(`${id}-heading`)
    section?.scrollIntoView({ block: 'start' })
    heading?.focus({ preventScroll: true })
  }

  return (
    <div className="feed">
      <header className="border-b hairline pb-6">
        <div className="flex items-baseline justify-between">
          <p className="label">AI Daily · {report.label}</p>
          <p className="mono tnum text-xs text-[var(--ink-3)]">
            NO.{String(report.issue).padStart(3, '0')}
          </p>
        </div>
        <div className="mt-3 flex items-baseline justify-between gap-4">
          <h2 className="serif tnum text-4xl font-bold">{report.date}</h2>
          <span className="mono tnum shrink-0 text-xs text-[var(--ink-3)]">
            <button className="cal-nav" disabled={!hasPrev} onClick={onPrev} aria-label="上一期">
              ‹
            </button>
            <span className="mx-1">
              {report.weekday} · {itemCount(report)} 条
            </span>
            <button className="cal-nav" disabled={!hasNext} onClick={onNext} aria-label="下一期">
              ›
            </button>
          </span>
        </div>

        {oneLiner && (
          <p className="oneliner">
            <span className="mono mr-2 text-[var(--ink-3)]">📌</span>
            {oneLiner}
          </p>
        )}
      </header>

      {moduleFilter === null && sections.length > 1 && (
        <nav className="article-toc" aria-label="本期目录">
          <p className="article-toc-label label">目录</p>
          <ol className="article-toc-list">
            {sections.map(({ section, originalIndex }, index) => (
              <li key={section.title}>
                <button
                  type="button"
                  className="article-toc-link"
                  aria-controls={sectionId(originalIndex)}
                  title={`跳到：${section.title}`}
                  onClick={() => jumpToSection(originalIndex)}
                >
                  <span className="article-toc-num mono tnum" aria-hidden="true">
                    {String(index + 1).padStart(2, '0')}
                  </span>
                  <span className="article-toc-title">{section.title}</span>
                </button>
              </li>
            ))}
          </ol>
        </nav>
      )}

      {sections.map(({ section: sec, originalIndex }, sectionIndex) => {
        const id = sectionId(originalIndex)
        return (
          <section
            key={sec.title}
            id={id}
            className="article-section mt-2"
            aria-labelledby={`${id}-heading`}
          >
            <header className="mod-head">
              <h3
                id={`${id}-heading`}
                className="serif text-lg font-bold"
                style={{ letterSpacing: '0.06em' }}
                tabIndex={-1}
              >
                {sec.title}
              </h3>
              <span className="cnt mono tnum">{sec.items.length}</span>
              <span className="rule flex-1 self-center border-t hairline" />
            </header>
            {sec.note && <p className="mod-note">{sec.note}</p>}
            <div>
              {sec.items.map((item, i) => (
                <NewsRow key={i} item={item} num={sectionOffsets[sectionIndex] + i + 1} />
              ))}
            </div>
          </section>
        )
      })}

      {sections.length === 0 && (
        <p className="py-16 text-sm text-[var(--ink-3)]">这个模块下没有资讯。</p>
      )}

      <p className="feed-copy label">© 2026 {site.author}</p>
    </div>
  )
}
