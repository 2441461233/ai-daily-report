import { monthMatrix, shiftMonth, todayStr } from '@/lib/calendar'

interface Props {
  year: number
  month: number // 1-12
  counts: Record<string, number>
  selected: string | null
  onSelectDate: (date: string) => void
  onShift: (y: number, m: number) => void
}

const WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日']

export default function Calendar({ year, month, counts, selected, onSelectDate, onShift }: Props) {
  const weeks = monthMatrix(year, month, counts)
  const today = todayStr()
  const [py, pm] = shiftMonth(year, month, -1)
  const [ny, nm] = shiftMonth(year, month, 1)

  return (
    <div>
      <div className="mb-3 flex items-baseline justify-between">
        <button className="cal-nav" onClick={() => onShift(py, pm)} aria-label="上个月">
          ‹
        </button>
        <span className="mono tnum text-sm font-medium">
          {year}.{String(month).padStart(2, '0')}
        </span>
        <button className="cal-nav" onClick={() => onShift(ny, nm)} aria-label="下个月">
          ›
        </button>
      </div>

      <div className="cal-grid mb-1">
        {WEEKDAYS.map((w) => (
          <span key={w} className="cal-wd">
            {w}
          </span>
        ))}
      </div>

      <div className="cal-grid">
        {weeks.flat().map((c, i) => {
          if (c.date === null) return <span key={i} />
          const has = c.count > 0
          const isSel = c.date === selected
          const isToday = c.date === today
          const cls = [
            'cal-day',
            has ? 'has' : '',
            isSel ? 'sel' : '',
            isToday && !isSel ? 'today' : '',
          ]
            .filter(Boolean)
            .join(' ')
          return has ? (
            <button
              key={i}
              className={cls}
              onClick={() => onSelectDate(c.date!)}
              title={`${c.date} · 有日报`}
            >
              <span className="cal-num">{c.day}</span>
              <span className="cal-dots">
                <span className="cal-dot" />
              </span>
            </button>
          ) : (
            <span key={i} className={cls}>
              <span className="cal-num">{c.day}</span>
              <span className="cal-dots" />
            </span>
          )
        })}
      </div>

      <p className="cal-legend">
        <span className="cal-dot" />
        当天有日报，点击查看
      </p>
    </div>
  )
}
