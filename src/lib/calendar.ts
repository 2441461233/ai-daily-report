/** month matrix: weeks starting Monday, null pads */
export interface CalCell {
  date: string | null // YYYY-MM-DD
  day: number // 0 for pads
  count: number // issues that day
}

function pad(n: number): string {
  return String(n).padStart(2, '0')
}

export function monthKey(y: number, m: number): string {
  return `${y}-${pad(m)}`
}

export function monthMatrix(
  year: number,
  month: number, // 1-12
  counts: Record<string, number>,
): CalCell[][] {
  const first = new Date(year, month - 1, 1)
  const daysInMonth = new Date(year, month, 0).getDate()
  const lead = (first.getDay() + 6) % 7 // Monday-first offset

  const cells: CalCell[] = []
  for (let i = 0; i < lead; i++) cells.push({ date: null, day: 0, count: 0 })
  for (let d = 1; d <= daysInMonth; d++) {
    const date = `${year}-${pad(month)}-${pad(d)}`
    cells.push({ date, day: d, count: counts[date] ?? 0 })
  }
  while (cells.length % 7 !== 0) cells.push({ date: null, day: 0, count: 0 })

  const weeks: CalCell[][] = []
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7))
  return weeks
}

export function shiftMonth(y: number, m: number, delta: number): [number, number] {
  const t = m - 1 + delta
  return [y + Math.floor(t / 12), ((t % 12) + 12) % 12 + 1]
}

export function todayStr(): string {
  const d = new Date()
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}
