import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReportsPayload } from '@/types/report'
import { site } from '@/config'
import Rail from '@/components/Rail'
import Feed from '@/components/Feed'

const DATA_URL = `${import.meta.env.BASE_URL}data/reports.json`

interface ReportScopedValue<T> {
  slug: string
  value: T
}

function parseHash(): string | null {
  const h = window.location.hash.replace(/^#\/?/, '')
  return /^\d{4}-\d{2}-\d{2}--\d+$/.test(h) ? h : null
}

export default function Home() {
  const [payload, setPayload] = useState<ReportsPayload | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [slug, setSlug] = useState<string | null>(() => parseHash())
  const [moduleSelection, setModuleSelection] = useState<ReportScopedValue<string | null> | null>(
    null,
  )
  const [calendarView, setCalendarView] = useState<
    ReportScopedValue<[number, number]> | null
  >(null)

  useEffect(() => {
    fetch(DATA_URL)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then((d: ReportsPayload) => setPayload(d))
      .catch((e) => setError(String(e)))
  }, [])

  useEffect(() => {
    const onHash = () => {
      setSlug(parseHash())
      setModuleSelection(null)
      setCalendarView(null)
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    if (payload) for (const r of payload.reports) c[r.date] = (c[r.date] ?? 0) + 1
    return c
  }, [payload])

  const index = useMemo(() => {
    if (!payload || payload.reports.length === 0) return -1
    const found = slug ? payload.reports.findIndex((r) => r.slug === slug) : -1
    return found >= 0 ? found : payload.reports.length - 1
  }, [payload, slug])

  const report = payload && index >= 0 ? payload.reports[index] : null
  const reportCalendar: [number, number] | null = report
    ? [Number(report.date.slice(0, 4)), Number(report.date.slice(5, 7))]
    : null
  const cal =
    report && calendarView?.slug === report.slug ? calendarView.value : reportCalendar
  const moduleFilter =
    report && moduleSelection?.slug === report.slug ? moduleSelection.value : null

  // Keep external browser state in sync with the selected issue.
  useEffect(() => {
    if (!report) return
    const want = `#/${report.slug}`
    if (window.location.hash !== want) window.history.replaceState(null, '', want)
    document.title = `${report.date} ${report.label} · ${site.title}`
  }, [report])

  const goto = useCallback(
    (i: number) => {
      if (!payload || i < 0 || i >= payload.reports.length) return
      window.location.hash = `#/${payload.reports[i].slug}`
    },
    [payload],
  )

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!report) return
      if (e.key === 'ArrowLeft') goto(index - 1)
      if (e.key === 'ArrowRight') goto(index + 1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [report, index, goto])

  const selectDate = useCallback(
    (date: string) => {
      if (!payload) return
      // pick the latest issue of that day
      const dayIssues = payload.reports.filter((r) => r.date === date)
      if (dayIssues.length > 0) window.location.hash = `#/${dayIssues[dayIssues.length - 1].slug}`
    },
    [payload],
  )

  if (error) {
    return (
      <main className="mx-auto max-w-[680px] px-6 py-24">
        <p className="label">Error</p>
        <p className="serif mt-4 text-lg">数据读取失败:{error}</p>
        <p className="mt-2 text-sm text-[var(--ink-3)]">请先运行 python3 scripts/build_data.py 生成数据。</p>
      </main>
    )
  }

  if (!payload || !report || !cal) {
    return (
      <main className="mx-auto max-w-[680px] px-6 py-24">
        <p className="label">Loading / 读取中…</p>
      </main>
    )
  }

  return (
    <div className="layout">
      <Rail
        reports={payload.reports}
        counts={counts}
        report={report}
        calYear={cal[0]}
        calMonth={cal[1]}
        onShiftMonth={(y, m) => setCalendarView({ slug: report.slug, value: [y, m] })}
        onSelectDate={selectDate}
        onSelectIssue={(s) => goto(payload.reports.findIndex((r) => r.slug === s))}
        moduleFilter={moduleFilter}
        onSelectModule={(value) => setModuleSelection({ slug: report.slug, value })}
      />
      <main className="feed-col">
        <Feed
          report={report}
          moduleFilter={moduleFilter}
          hasPrev={index > 0}
          hasNext={index < payload.reports.length - 1}
          onPrev={() => goto(index - 1)}
          onNext={() => goto(index + 1)}
        />
      </main>
    </div>
  )
}
