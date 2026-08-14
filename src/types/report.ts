export interface Source {
  name: string
  url?: string
}

export interface NewsItem {
  text: string
  summary?: string
  expanded?: boolean
  flag: boolean // 单一来源
  priorityIds?: string[]
  sources: Source[]
}

export interface NewsModule {
  title: string
  note?: string
  items: NewsItem[]
}

export interface Report {
  slug: string // e.g. "2026-08-06--2"
  date: string
  weekday: string
  label: string // e.g. "第三期·午后增量"
  seq: number
  issue: number
  rich?: boolean
  kind?: 'addendum'
  oneLiner?: string
  sections: NewsModule[]
}

export interface ReportsPayload {
  generatedAt: string
  count: number
  reports: Report[]
}

export function itemCount(r: Report): number {
  return r.sections.reduce((n, s) => n + s.items.length, 0)
}
