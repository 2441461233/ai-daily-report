import { useState } from 'react'
import { site } from '@/config'
import type { Report } from '@/types/report'
import { itemCount } from '@/types/report'
import Calendar from '@/components/Calendar'

interface Props {
  reports: Report[]
  counts: Record<string, number>
  report: Report
  calYear: number
  calMonth: number
  onShiftMonth: (y: number, m: number) => void
  onSelectDate: (date: string) => void
  onSelectIssue: (slug: string) => void
  moduleFilter: string | null
  onSelectModule: (m: string | null) => void
}

export default function Rail({
  reports,
  counts,
  report,
  calYear,
  calMonth,
  onShiftMonth,
  onSelectDate,
  onSelectIssue,
  moduleFilter,
  onSelectModule,
}: Props) {
  const dayIssues = reports.filter((r) => r.date === report.date)
  const otherIssues = dayIssues.filter((r) => r.slug !== report.slug)
  // mobile default: calendar collapsed so the news is first-screen content;
  // desktop always shows it (CSS forces .cal-wrap open regardless of state)
  const [calOpen, setCalOpen] = useState(false)

  return (
    <aside className="rail">
      <a href="#/" className="block">
        <h1 className="serif text-lg font-bold" style={{ letterSpacing: '0.14em' }}>
          {site.title}
        </h1>
        <p className="label mt-1.5">{site.tagline}</p>
      </a>

      <div className="cal-box mt-8 border-t hairline pt-6">
        <button
          className="cal-toggle"
          onClick={() => setCalOpen((v) => !v)}
          aria-expanded={calOpen}
        >
          <span>选择日期</span>
          <span className="mono">{calOpen ? '−' : '+'}</span>
        </button>
        <div className={`cal-wrap ${calOpen ? 'open' : ''}`}>
          <Calendar
            year={calYear}
            month={calMonth}
            counts={counts}
            selected={report.date}
            onSelectDate={onSelectDate}
            onShift={onShiftMonth}
          />
        </div>
      </div>

      {otherIssues.length > 0 && (
        <div className="mt-6 border-t hairline pt-4">
          <p className="label mb-1">这天的其他期</p>
          <p className="rail-hint mb-2">
            {report.date} 当天出了 {dayIssues.length} 期，你正在看「{report.label}」。
          </p>
          {otherIssues.map((r) => (
            <button
              key={r.slug}
              className="mod-row"
              onClick={() => onSelectIssue(r.slug)}
              title={r.label}
            >
              <span className="truncate">{r.label}</span>
              <span className="cnt tnum">{itemCount(r)} 条</span>
            </button>
          ))}
        </div>
      )}

      <div className="mt-6 border-t hairline pt-4">
        <p className="label mb-2">模块</p>
        <div className="mod-list">
          <button
            className={`mod-row ${moduleFilter === null ? 'current' : ''}`}
            onClick={() => onSelectModule(null)}
          >
            <span>全部</span>
            <span className="cnt tnum">{itemCount(report)}</span>
          </button>
          {report.sections.map((s) => (
            <button
              key={s.title}
              className={`mod-row ${moduleFilter === s.title ? 'current' : ''}`}
              onClick={() => onSelectModule(s.title)}
              title={s.title}
            >
              <span className="truncate">{s.title}</span>
              <span className="cnt tnum">{s.items.length}</span>
            </button>
          ))}
        </div>
      </div>

      <p className="label mt-auto pt-8 rail-copy">© 2026 {site.author}</p>
    </aside>
  )
}
