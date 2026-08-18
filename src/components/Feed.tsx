import { useEffect, useRef, useState } from 'react'
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

function reportSectionId(reportSlug: string, originalIndex: number) {
  return `report-${reportSlug}-section-${originalIndex + 1}`
}

function compactSectionTitle(title: string) {
  return title.replace(/^[^A-Za-z0-9\u3400-\u9fff]+/u, '').trim() || title
}

export default function Feed({ report, moduleFilter, hasPrev, hasNext, onPrev, onNext }: Props) {
  const [tocPinnedFor, setTocPinnedFor] = useState<string | null>(null)
  const [tocInteracting, setTocInteracting] = useState(false)
  const [tocAutoVisible, setTocAutoVisible] = useState(false)
  const [wideToc, setWideToc] = useState(() => window.matchMedia('(min-width: 1440px)').matches)
  const [activeSectionIndex, setActiveSectionIndex] = useState(0)
  const [readingProgress, setReadingProgress] = useState(0)
  const feedShellRef = useRef<HTMLDivElement>(null)
  const tocHideTimer = useRef<number | null>(null)
  const sections = report.sections
    .map((section, originalIndex) => ({ section, originalIndex }))
    .filter(({ section }) => moduleFilter === null || section.title === moduleFilter)
  const showToc = moduleFilter === null && sections.length > 1
  const sectionOffsets = sections.reduce<number[]>(
    (offsets, { section }) => [
      ...offsets,
      offsets[offsets.length - 1] + section.items.length,
    ],
    [0],
  )

  const oneLiner = (report.oneLiner ?? '').replace(/^📌\s*今日一句话[：:]\s*/, '')
  const tocPinned = tocPinnedFor === report.slug
  /* wide screens (≥1440px) have a real gutter lane left of the article: the
     outline lives there permanently as a quiet scroll-spy list — the
     Feishu/Notion/Stripe-docs pattern. no auto-hide (it would vanish under
     the reader's cursor), no panel chrome, no click required. narrower
     screens keep click-to-pin so the panel never covers the text. */
  const tocOpen = tocPinned || wideToc
  const tocAwake = tocOpen || tocInteracting || tocAutoVisible
  const tocPanelId = `report-${report.slug}-toc`
  const activeTocIndex = Math.max(
    0,
    sections.findIndex(({ originalIndex }) => originalIndex === activeSectionIndex),
  )
  const activeTocTitle = compactSectionTitle(
    sections[activeTocIndex]?.section.title ?? sections[0]?.section.title ?? '',
  )

  useEffect(() => {
    const media = window.matchMedia('(min-width: 1440px)')
    const syncLayout = (event: MediaQueryListEvent) => setWideToc(event.matches)
    media.addEventListener('change', syncLayout)
    return () => media.removeEventListener('change', syncLayout)
  }, [])

  useEffect(() => {
    if (moduleFilter !== null || report.sections.length < 2) return

    const feedColumn = document.querySelector<HTMLElement>('.feed-col')
    let animationFrame = 0

    const updateReadingPosition = () => {
      const readingLine = Math.max(96, Math.min(window.innerHeight * 0.24, 180))
      const sectionElements = report.sections
        .map((_, originalIndex) => ({
          originalIndex,
          element: document.getElementById(reportSectionId(report.slug, originalIndex)),
        }))
        .filter(
          (entry): entry is { originalIndex: number; element: HTMLElement } =>
            entry.element !== null,
        )

      if (sectionElements.length === 0) return

      const desktopScroller = window.matchMedia('(min-width: 900px)').matches && feedColumn
      const atBottom = desktopScroller
        ? desktopScroller.scrollTop + desktopScroller.clientHeight >= desktopScroller.scrollHeight - 2
        : window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 2
      let current = sectionElements[0].originalIndex
      for (const entry of sectionElements) {
        if (entry.element.getBoundingClientRect().top <= readingLine) {
          current = entry.originalIndex
        } else {
          break
        }
      }
      if (atBottom) current = sectionElements.at(-1)?.originalIndex ?? current
      setActiveSectionIndex(current)

      const firstTop = sectionElements[0].element.getBoundingClientRect().top
      const lastBottom = sectionElements.at(-1)?.element.getBoundingClientRect().bottom ?? firstTop
      const contentHeight = Math.max(lastBottom - firstTop, 1)
      const readableDistance = Math.max(contentHeight - window.innerHeight * 0.55, 1)
      const progress = atBottom
        ? 1
        : Math.min(1, Math.max(0, (readingLine - firstTop) / readableDistance))
      setReadingProgress(progress)
    }

    const schedulePositionUpdate = () => {
      if (animationFrame) return
      animationFrame = window.requestAnimationFrame(() => {
        animationFrame = 0
        updateReadingPosition()
      })
    }

    const resizeObserver = new ResizeObserver(schedulePositionUpdate)

    const showTocWhileReading = () => {
      schedulePositionUpdate()
      setTocAutoVisible(true)
      if (tocHideTimer.current !== null) window.clearTimeout(tocHideTimer.current)
      tocHideTimer.current = window.setTimeout(() => setTocAutoVisible(false), 1700)
    }

    schedulePositionUpdate()
    if (feedShellRef.current) resizeObserver.observe(feedShellRef.current)
    feedColumn?.addEventListener('scroll', showTocWhileReading, { passive: true })
    window.addEventListener('scroll', showTocWhileReading, { passive: true })
    window.addEventListener('resize', schedulePositionUpdate)

    return () => {
      feedColumn?.removeEventListener('scroll', showTocWhileReading)
      window.removeEventListener('scroll', showTocWhileReading)
      window.removeEventListener('resize', schedulePositionUpdate)
      resizeObserver.disconnect()
      if (animationFrame) window.cancelAnimationFrame(animationFrame)
      if (tocHideTimer.current !== null) window.clearTimeout(tocHideTimer.current)
    }
  }, [moduleFilter, report.sections, report.slug])

  const jumpToSection = (originalIndex: number) => {
    const id = reportSectionId(report.slug, originalIndex)
    const section = document.getElementById(id)
    const heading = document.getElementById(`${id}-heading`)
    section?.scrollIntoView({ block: 'start' })
    heading?.focus({ preventScroll: true })
    setActiveSectionIndex(originalIndex)
    setTocPinnedFor(null)
    setTocInteracting(false)
  }

  return (
    <div ref={feedShellRef} className="feed-shell">
      <div className={`feed ${tocOpen ? 'toc-open' : ''}`}>
        {showToc && (
          <div className="toc-dock">
            <nav
              className={`article-toc ${tocOpen ? 'is-open' : ''} ${tocPinned ? 'is-pinned' : ''} ${tocAwake ? 'is-awake' : ''}`}
              aria-label="本期目录"
              onMouseEnter={() => setTocInteracting(true)}
              onMouseLeave={() => setTocInteracting(false)}
              onBlurCapture={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget)) setTocInteracting(false)
              }}
              onKeyDown={(event) => {
                if (event.key === 'Escape' && tocOpen) {
                  setTocPinnedFor(null)
                  setTocInteracting(false)
                  setTocAutoVisible(false)
                  document.getElementById(`${tocPanelId}-toggle`)?.focus()
                }
              }}
            >
              <div className="article-toc-spine" aria-hidden="true">
                <span style={{ height: `${Math.round(readingProgress * 100)}%` }} />
              </div>

              <button
                id={`${tocPanelId}-toggle`}
                type="button"
                className="article-toc-toggle"
                aria-expanded={tocOpen}
                aria-controls={tocPanelId}
                aria-label={`目录，当前在第 ${activeTocIndex + 1} 节：${activeTocTitle}`}
                tabIndex={tocOpen ? -1 : undefined}
                onClick={() => {
                  if (tocPinned) {
                    setTocPinnedFor(null)
                  } else {
                    setTocPinnedFor(report.slug)
                    window.requestAnimationFrame(() =>
                      document.getElementById(`${tocPanelId}-close`)?.focus(),
                    )
                  }
                }}
              >
                <svg
                  className="article-toc-icon"
                  viewBox="0 0 16 16"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.4"
                  strokeLinecap="round"
                  aria-hidden="true"
                >
                  <path d="M5.5 3.5h8" />
                  <path d="M5.5 8h8" />
                  <path d="M5.5 12.5h5" />
                  <circle cx="2.5" cy="3.5" r="0.9" fill="currentColor" stroke="none" />
                  <circle cx="2.5" cy="8" r="0.9" fill="currentColor" stroke="none" />
                  <circle cx="2.5" cy="12.5" r="0.9" fill="currentColor" stroke="none" />
                </svg>
                <span className="article-toc-toggle-index mono tnum" aria-hidden="true">
                  {String(activeTocIndex + 1).padStart(2, '0')}
                  <span className="article-toc-toggle-total">/{sections.length}</span>
                </span>
                <span className="article-toc-progress" aria-hidden="true">
                  <span style={{ width: `${Math.round(readingProgress * 100)}%` }} />
                </span>
              </button>

              <div id={tocPanelId} className="article-toc-panel" aria-hidden={!tocOpen}>
                <div className="article-toc-panel-inner">
                  <div className="article-toc-panel-head">
                    <p className="article-toc-label">本期目录</p>
                    <span className="article-toc-count mono tnum">
                      {activeTocIndex + 1}/{sections.length}
                    </span>
                    <button
                      id={`${tocPanelId}-close`}
                      type="button"
                      className="article-toc-close"
                      aria-label="收起目录"
                      tabIndex={tocOpen ? 0 : -1}
                      onClick={() => {
                        setTocPinnedFor(null)
                        setTocInteracting(false)
                        setTocAutoVisible(false)
                        window.requestAnimationFrame(() =>
                          document.getElementById(`${tocPanelId}-toggle`)?.focus(),
                        )
                      }}
                    >
                      ×
                    </button>
                  </div>
                  <ol className="article-toc-list">
                    {sections.map(({ section, originalIndex }, index) => (
                      <li key={section.title}>
                        <button
                          type="button"
                          className={`article-toc-link ${originalIndex === activeSectionIndex ? 'current' : ''}`}
                          aria-controls={reportSectionId(report.slug, originalIndex)}
                          aria-current={originalIndex === activeSectionIndex ? 'location' : undefined}
                          tabIndex={tocOpen ? 0 : -1}
                          title={`跳到：${section.title}`}
                          onClick={() => jumpToSection(originalIndex)}
                        >
                          <span className="article-toc-num mono tnum" aria-hidden="true">
                            {String(index + 1).padStart(2, '0')}
                          </span>
                          <span className="article-toc-title">{compactSectionTitle(section.title)}</span>
                        </button>
                      </li>
                    ))}
                  </ol>
                </div>
              </div>
            </nav>
          </div>
        )}

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

        {sections.map(({ section: sec, originalIndex }, sectionIndex) => {
          const id = reportSectionId(report.slug, originalIndex)
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
    </div>
  )
}
