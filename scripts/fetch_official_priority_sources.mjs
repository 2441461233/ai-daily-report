#!/usr/bin/env node

/**
 * Deterministically collect recent announcements from first-party AI lab sources.
 *
 * Keep source-specific parsing in SOURCE_DEFINITIONS so another official lab can
 * be added without changing collection, filtering, error reporting, or output.
 */

import { pathToFileURL } from 'node:url'

const DEFAULT_WINDOW_HOURS = 72
const DEFAULT_TIMEOUT_MS = 30_000

const MONTHS = new Map(
  [
    'jan',
    'feb',
    'mar',
    'apr',
    'may',
    'jun',
    'jul',
    'aug',
    'sep',
    'oct',
    'nov',
    'dec',
  ].map((month, index) => [month, index + 1]),
)

export class SourceParseError extends Error {
  constructor(message) {
    super(message)
    this.name = 'SourceParseError'
  }
}

class RejectedSignalError extends Error {
  constructor(message) {
    super(message)
    this.name = 'RejectedSignalError'
  }
}

export const SOURCE_DEFINITIONS = Object.freeze([
  Object.freeze({
    id: 'spacexai',
    name: 'SpaceXAI official releases',
    officialSource: 'SpaceXAI',
    critical: true,
    endpoints: Object.freeze([
      Object.freeze({
        id: 'news',
        name: 'SpaceXAI News',
        url: 'https://x.ai/news',
        parser: parseSpaceXaiNews,
      }),
      Object.freeze({
        id: 'release-notes',
        name: 'SpaceXAI API Release Notes',
        url: 'https://docs.x.ai/developers/release-notes',
        parser: parseSpaceXaiReleaseNotes,
      }),
      Object.freeze({
        id: 'hn-signal',
        name: 'Hacker News x.ai release signal',
        url: 'https://hn.algolia.com/api/v1/search_by_date?tags=story&query=x.ai&hitsPerPage=1000',
        parser: parseHackerNewsSpaceXaiSignals,
        corroborating: true,
        verifyArticles: true,
      }),
    ]),
  }),
])

function decodeHtml(value) {
  return String(value)
    .replace(/&#(\d+);/g, (_, codePoint) => String.fromCodePoint(Number(codePoint)))
    .replace(/&#x([\da-f]+);/gi, (_, codePoint) =>
      String.fromCodePoint(Number.parseInt(codePoint, 16)),
    )
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&quot;/gi, '"')
    .replace(/&apos;|&#39;/gi, "'")
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
}

function cleanText(value) {
  return decodeHtml(
    String(value)
      .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, ' ')
      .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, ' ')
      .replace(/<[^>]+>/g, ' '),
  )
    .replace(/\s+/g, ' ')
    .replace(/\s+([.,;:!?])/g, '$1')
    .trim()
}

function readAttribute(attributes, name) {
  const escapedName = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = String(attributes).match(
    new RegExp(`(?:^|\\s)${escapedName}\\s*=\\s*(?:"([^"]*)"|'([^']*)'|([^\\s>]+))`, 'i'),
  )
  return match ? decodeHtml(match[1] ?? match[2] ?? match[3] ?? '') : null
}

function canonicalArticleUrl(value, baseUrl) {
  if (!value) return null
  try {
    const url = new URL(value, baseUrl)
    const hostname = url.hostname.toLowerCase().replace(/^www\./, '')
    if (
      hostname !== 'x.ai' ||
      url.port !== '' ||
      !/^\/news\/[^/]+\/?$/i.test(url.pathname)
    ) return null
    url.hostname = 'x.ai'
    url.protocol = 'https:'
    url.username = ''
    url.password = ''
    url.search = ''
    url.hash = ''
    url.pathname = url.pathname.replace(/\/$/, '')
    return url.href
  } catch {
    return null
  }
}

function canonicalEvidenceUrl(value, baseUrl) {
  if (!value) return null
  try {
    const url = new URL(value, baseUrl)
    const hostname = url.hostname.toLowerCase().replace(/^www\./, '')
    if ((hostname !== 'x.ai' && hostname !== 'docs.x.ai') || url.port !== '') return null
    url.hostname = hostname
    url.protocol = 'https:'
    url.username = ''
    url.password = ''
    url.search = ''
    url.hash = ''
    url.pathname = url.pathname.replace(/\/$/, '') || '/'
    return url.href
  } catch {
    return null
  }
}

function validIsoDate(value) {
  const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (!match) return null
  const [, year, month, day] = match
  const date = new Date(`${year}-${month}-${day}T00:00:00.000Z`)
  return date.getUTCFullYear() === Number(year) &&
    date.getUTCMonth() + 1 === Number(month) &&
    date.getUTCDate() === Number(day)
    ? `${year}-${month}-${day}`
    : null
}

export function normalizePublishedAt(value) {
  if (value === null || value === undefined) return null
  const text = cleanText(value)
  if (!text) return null

  const dateOnly = validIsoDate(text)
  if (dateOnly) return `${dateOnly}T00:00:00.000Z`

  const monthDate = text.match(
    /\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{1,2}),\s*(\d{4})\b/i,
  )
  if (monthDate) {
    const month = MONTHS.get(monthDate[1].slice(0, 3).toLowerCase())
    const normalizedDate = validIsoDate(
      `${monthDate[3]}-${String(month).padStart(2, '0')}-${monthDate[2].padStart(2, '0')}`,
    )
    return normalizedDate ? `${normalizedDate}T00:00:00.000Z` : null
  }

  const timestamp = Date.parse(text)
  return Number.isFinite(timestamp) ? new Date(timestamp).toISOString() : null
}

function publicationPrecision(value) {
  const text = cleanText(value)
  return validIsoDate(text) ||
    /^\d{4}-\d{2}-\d{2}T00:00:00(?:\.0+)?(?:Z|[+-]00:00)$/i.test(text) ||
    /\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},\s*\d{4}\b/i.test(
      text,
    )
    ? 'day'
    : 'instant'
}

function normalizePublication(value) {
  const publishedAt = normalizePublishedAt(value)
  return publishedAt ? { publishedAt, precision: publicationPrecision(value) } : null
}

function findPublishedAt(attributes, body) {
  for (const name of ['datetime', 'data-published-at', 'data-date']) {
    const value = readAttribute(attributes, name)
    const normalized = normalizePublication(value)
    if (normalized) return normalized
  }

  const time = body.match(/<time\b([^>]*)>([\s\S]*?)<\/time>/i)
  if (time) {
    const normalized =
      normalizePublication(readAttribute(time[1], 'datetime')) ?? normalizePublication(time[2])
    if (normalized) return normalized
  }

  return normalizePublication(body.match(
    /\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},\s*\d{4}\b/i,
  )?.[0])
}

function findDescription(body) {
  const paragraph = body.match(/<p\b[^>]*>([\s\S]*?)<\/p>/i)
  return paragraph ? cleanText(paragraph[1]) : null
}

function findTitle(attributes, body) {
  const attributeTitle = readAttribute(attributes, 'data-title')
  if (attributeTitle?.trim()) return cleanText(attributeTitle)

  const heading = body.match(/<h[1-6]\b[^>]*>([\s\S]*?)<\/h[1-6]>/i)
  if (heading) return cleanText(heading[1])

  const ariaLabel = readAttribute(attributes, 'aria-label')
  if (ariaLabel?.trim() && !/^read more$/i.test(ariaLabel.trim())) return cleanText(ariaLabel)

  return null
}

function flattenJsonLd(value, output) {
  if (!value || typeof value !== 'object') return
  if (Array.isArray(value)) {
    for (const item of value) flattenJsonLd(item, output)
    return
  }

  const types = [value['@type']].flat().filter(Boolean)
  if (types.some((type) => /^(?:NewsArticle|Article|BlogPosting)$/i.test(String(type)))) {
    const mainEntity = value.mainEntityOfPage
    const url =
      value.url ??
      (typeof mainEntity === 'string' ? mainEntity : mainEntity?.['@id'] ?? mainEntity?.url)
    output.push({
      url,
      title: value.headline ?? value.name,
      publishedAt: value.datePublished,
      summary: value.description,
    })
  }

  for (const nested of Object.values(value)) flattenJsonLd(nested, output)
}

function extractJsonLdItems(html, parserErrors) {
  const output = []
  const scriptPattern = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi
  for (const match of html.matchAll(scriptPattern)) {
    if (!/application\/ld\+json/i.test(readAttribute(match[1], 'type') ?? '')) continue
    try {
      flattenJsonLd(JSON.parse(decodeHtml(match[2]).trim()), output)
    } catch (error) {
      parserErrors.push(`invalid JSON-LD: ${error.message}`)
    }
  }
  return output
}

function findMetaContent(html, attribute, expectedValue) {
  for (const match of html.matchAll(/<meta\b([^>]*)>/gi)) {
    if ((readAttribute(match[1], attribute) ?? '').toLowerCase() !== expectedValue) continue
    const content = readAttribute(match[1], 'content')
    if (content) return cleanText(content)
  }
  return null
}

/** Parse one directly fetched first-party x.ai news article. */
export function parseSpaceXaiArticle(html, expectedUrl) {
  if (typeof html !== 'string' || !html.trim()) {
    throw new SourceParseError('article response body is empty')
  }
  if (/Attention Required!|you have been blocked|cf-error-details/i.test(html)) {
    throw new SourceParseError('received a Cloudflare block page instead of the article')
  }
  const canonicalExpected = canonicalArticleUrl(expectedUrl, 'https://x.ai/news')
  if (!canonicalExpected) throw new SourceParseError('expected article URL is not an x.ai news URL')

  let canonicalUrl = null
  for (const match of html.matchAll(/<link\b([^>]*)>/gi)) {
    const relations = (readAttribute(match[1], 'rel') ?? '').toLowerCase().split(/\s+/)
    if (!relations.includes('canonical')) continue
    canonicalUrl = canonicalArticleUrl(readAttribute(match[1], 'href'), canonicalExpected)
    break
  }
  if (canonicalUrl !== canonicalExpected) {
    throw new RejectedSignalError(
      `article canonical URL ${canonicalUrl ?? 'is missing'}; expected ${canonicalExpected}`,
    )
  }

  const parserErrors = []
  const jsonLdItems = extractJsonLdItems(html, parserErrors)
  const structured = jsonLdItems.find((item) => {
    const itemUrl = item.url ? canonicalArticleUrl(item.url, canonicalExpected) : canonicalExpected
    return itemUrl === canonicalExpected && modelMatchTerms(item.title ?? '').length === 2
  })
  const documentTitle = cleanText(html.match(/<title\b[^>]*>([\s\S]*?)<\/title>/i)?.[1] ?? '')
    .replace(/\s*\|\s*SpaceXAI\s*$/i, '')
  const title =
    cleanText(structured?.title ?? '') ||
    findMetaContent(html, 'property', 'og:title') ||
    documentTitle ||
    cleanText(html.match(/<h1\b[^>]*>([\s\S]*?)<\/h1>/i)?.[1] ?? '')
  const publication =
    normalizePublication(structured?.publishedAt) ?? findPublishedAt('', html)
  const summary =
    cleanText(structured?.summary ?? '') ||
    findMetaContent(html, 'name', 'description') ||
    findMetaContent(html, 'property', 'og:description') ||
    findDescription(html)
  const missing = []
  if (!title) missing.push('title')
  if (!publication) missing.push('publishedAt')
  if (!summary) missing.push('summary')
  if (parserErrors.length) missing.push(`valid JSON-LD (${parserErrors.join('; ')})`)
  if (missing.length) {
    throw new SourceParseError(`official article is missing ${missing.join(', ')}`)
  }
  return {
    title,
    url: canonicalExpected,
    ...publication,
    summary,
    details: summary,
    evidenceUrls: [canonicalExpected],
  }
}

/** Parse the SpaceXAI news index into source items before time filtering. */
export function parseSpaceXaiNews(html, source = SOURCE_DEFINITIONS[0].endpoints[0]) {
  if (typeof html !== 'string' || !html.trim()) {
    throw new SourceParseError('response body is empty')
  }

  if (/Attention Required!|you have been blocked|cf-error-details/i.test(html)) {
    throw new SourceParseError('received a Cloudflare block page instead of the news index')
  }

  const parserErrors = []
  const rawItems = extractJsonLdItems(html, parserErrors)
  const anchorPattern = /<a\b([^>]*)>([\s\S]*?)<\/a>/gi

  for (const match of html.matchAll(anchorPattern)) {
    const url = canonicalArticleUrl(readAttribute(match[1], 'href'), source.url)
    if (!url) continue
    rawItems.push({
      url,
      title: findTitle(match[1], match[2]),
      publication: findPublishedAt(match[1], match[2]),
      summary: findDescription(match[2]),
    })
  }

  if (rawItems.length === 0) {
    const suffix = parserErrors.length ? ` (${parserErrors.join('; ')})` : ''
    throw new SourceParseError(`no SpaceXAI news article links were recognized${suffix}`)
  }

  const merged = new Map()
  for (const rawItem of rawItems) {
    const url = canonicalArticleUrl(rawItem.url, source.url)
    if (!url) continue
    const previous = merged.get(url) ?? {
      url,
      title: null,
      publishedAt: null,
      precision: null,
      summary: null,
      details: null,
      evidenceUrls: [url],
    }
    previous.title ||= cleanText(rawItem.title ?? '') || null
    const publication = rawItem.publication ?? normalizePublication(rawItem.publishedAt)
    previous.publishedAt ||= publication?.publishedAt ?? null
    previous.precision ||= publication?.precision ?? null
    previous.summary ||= cleanText(rawItem.summary ?? '') || null
    previous.details ||= previous.summary
    merged.set(url, previous)
  }

  const items = []
  for (const item of merged.values()) {
    const missing = []
    if (!item.title) missing.push('title')
    if (!item.publishedAt) missing.push('publishedAt')
    if (missing.length) {
      parserErrors.push(`${item.url}: missing ${missing.join(' and ')}`)
      continue
    }
    items.push(item)
  }

  if (items.length === 0) {
    throw new SourceParseError(`recognized news links but parsed no complete items: ${parserErrors.join('; ')}`)
  }

  return { items, errors: parserErrors }
}

function lastMatch(text, pattern) {
  return [...text.matchAll(pattern)].at(-1) ?? null
}

function monthDayPublication(monthName, day, year) {
  const month = MONTHS.get(monthName.slice(0, 3).toLowerCase())
  const date = validIsoDate(
    `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`,
  )
  return date ? { publishedAt: `${date}T00:00:00.000Z`, precision: 'day' } : null
}

function truncateText(value, maximumLength) {
  const text = cleanText(value)
  if (text.length <= maximumLength) return text
  const shortened = text.slice(0, maximumLength + 1)
  const boundary = shortened.lastIndexOf(' ')
  return `${shortened.slice(0, boundary > maximumLength * 0.7 ? boundary : maximumLength).trimEnd()}…`
}

function summarizeDetails(value) {
  const sentences = cleanText(value).split(/(?<=[.!?])\s+(?=[A-Z])/)
  return truncateText(sentences.slice(0, 2).join(' '), 420)
}

function extractEvidenceUrls(html, baseUrl) {
  const urls = []
  for (const match of html.matchAll(/<a\b([^>]*)>/gi)) {
    const url = canonicalEvidenceUrl(readAttribute(match[1], 'href'), baseUrl)
    if (url && !urls.includes(url)) urls.push(url)
  }
  return urls
}

/** Parse dated model entries from the first-party SpaceXAI API changelog. */
export function parseSpaceXaiReleaseNotes(
  html,
  source = SOURCE_DEFINITIONS[0].endpoints[1],
) {
  if (typeof html !== 'string' || !html.trim()) {
    throw new SourceParseError('response body is empty')
  }

  const pageText = cleanText(html)
  const updated = lastMatch(
    pageText,
    /Last updated:\s*(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},\s*(\d{4})/gi,
  )
  const defaultYear = updated ? Number(updated[1]) : null
  const parserErrors = []
  const items = []
  const headings = [...html.matchAll(/<h[3-4]\b[^>]*>([\s\S]*?)<\/h[3-4]>/gi)]
  const sectionHeadings = [...html.matchAll(/<h2\b[^>]*>([\s\S]*?)<\/h2>/gi)]

  for (let index = 0; index < headings.length; index += 1) {
    const heading = headings[index]
    const title = cleanText(heading[1])
    const nextHeadingIndex = headings[index + 1]?.index ?? html.length
    const body = html.slice(heading.index + heading[0].length, nextHeadingIndex)
    const paragraph = body.match(/<p\b[^>]*>([\s\S]*?)<\/p>/i)
    const details = paragraph ? truncateText(paragraph[1], 1_200) : ''
    if (!MODEL_NAME_PATTERN.test(title)) continue
    if (classifyAnnouncement(title, details).category !== 'major_model_release') {
      const mentionsAvailability =
        /\b(?:now\s+(?:live|available)|released|launch(?:es|ed|ing)?|introduc(?:e|ed|ing))\b/i.test(
          details,
        )
      const nearbyDate = cleanText(
        html.slice(Math.max(0, heading.index - 1_500), heading.index),
      )
      const isRecentYear = !defaultYear || nearbyDate.includes(String(defaultYear))
      if (mentionsAvailability && isRecentYear) {
        parserErrors.push(`${title}: model availability wording was not classified`)
      }
      continue
    }

    const precedingHtml = html.slice(Math.max(0, heading.index - 12_000), heading.index)
    const precedingText = cleanText(precedingHtml)
    const dateMatch = lastMatch(
      precedingText,
      /\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{1,2})(?:,\s*(\d{4}))?\b/gi,
    )
    const sectionHeading = sectionHeadings.filter(
      (candidate) => candidate.index < heading.index,
    ).at(-1)
    const sectionYear = cleanText(sectionHeading?.[1] ?? '').match(/\b(\d{4})\b/)?.[1]
    const publication = dateMatch
      ? monthDayPublication(
          dateMatch[1],
          Number(dateMatch[2]),
          Number(dateMatch[3] ?? sectionYear ?? defaultYear),
        )
      : null

    const evidence = paragraph ? extractEvidenceUrls(paragraph[1], source.url) : []
    const announcementUrl = evidence.find((url) => /^https:\/\/x\.ai\/news\//i.test(url))
    const docsUrl = evidence.find((url) => {
      const pathname = new URL(url).pathname
      return /^\/developers\/(?:models\/)?(?:grok|gpt|claude|gemini|llama|mistral|command|deepseek|qwen|kimi|minimax|glm|seed)[-\d]/i.test(
        pathname,
      )
    })
    const missing = []
    if (!publication) missing.push('publishedAt')
    if (!details) missing.push('details')
    if (missing.length) {
      parserErrors.push(`${title}: missing ${missing.join(', ')}`)
      continue
    }
    // Some first-party release-note entries publish before their detail page.
    // The dated changelog entry is still official evidence, so retain it with
    // a model-derived stable id instead of silently discarding the launch.
    const itemSpecificUrl = announcementUrl ?? docsUrl
    const url = itemSpecificUrl ?? source.url

    items.push({
      title,
      url,
      stableSlug: itemSpecificUrl ? undefined : stableModelSlug(title, details),
      ...publication,
      summary: summarizeDetails(details),
      details,
      evidenceUrls: [...new Set([url, ...evidence, source.url])],
    })
  }

  if (items.length === 0) {
    const suffix = parserErrors.length ? `: ${parserErrors.join('; ')}` : ''
    throw new SourceParseError(`no complete major model releases were parsed from release notes${suffix}`)
  }
  return { items, errors: parserErrors }
}

/**
 * Parse HN's deterministic Algolia index as a discovery signal for x.ai.
 * Only item-specific x.ai/news URLs can become candidates; HN contributes the
 * exact timestamp and discovery path, while official x.ai URLs remain the
 * required evidence. This closes the blind spot where the official news index
 * is blocked and release notes lag a just-published article.
 */
export function parseHackerNewsSpaceXaiSignals(
  json,
  source = SOURCE_DEFINITIONS[0].endpoints[2],
) {
  let document
  try {
    document = JSON.parse(json)
  } catch (error) {
    throw new SourceParseError(`invalid Hacker News JSON: ${error.message}`)
  }
  if (!document || !Array.isArray(document.hits)) {
    throw new SourceParseError('Hacker News response is missing hits array')
  }

  const items = []
  for (const hit of document.hits) {
    if (!hit || typeof hit !== 'object') continue
    const url = canonicalArticleUrl(hit.url, 'https://x.ai/news')
    const rawTitle = cleanText(hit.title ?? hit.story_title ?? '')
    const publication = normalizePublication(hit.created_at)
    const exactReleaseTitle = rawTitle.match(
      /^(?:(?:introducing|announcing|launching|releasing|released)\s+)?grok[-\s]?\d+(?:[.\-]\d+)*$/i,
    )
    if (!url || !exactReleaseTitle || !publication) continue
    const title = /\b(?:introduc|announc|launch|releas|unveil)/i.test(rawTitle)
      ? rawTitle
      : `Introducing ${rawTitle}`
    items.push({
      title,
      url,
      stableSlug: stableModelSlug(title),
      ...publication,
      summary: title,
      details: title,
      evidenceUrls: [url],
    })
  }
  return { items, errors: [] }
}

const MODEL_NAME_PATTERN =
  /\b(grok|gpt|claude|gemini|llama|mistral|command|deepseek|qwen|kimi|minimax|glm|seed)[-\s]?(\d+(?:[.\-]\d+)*)\b/i

const BRAND_NAMES = Object.freeze({
  grok: 'Grok',
  gpt: 'GPT',
  claude: 'Claude',
  gemini: 'Gemini',
  llama: 'Llama',
  mistral: 'Mistral',
  command: 'Command',
  deepseek: 'DeepSeek',
  qwen: 'Qwen',
  kimi: 'Kimi',
  minimax: 'MiniMax',
  glm: 'GLM',
  seed: 'Seed',
})

function modelMatchTerms(title, details = '') {
  const match = `${cleanText(title)} ${cleanText(details)}`.match(MODEL_NAME_PATTERN)
  if (!match) return []
  return [BRAND_NAMES[match[1].toLowerCase()] ?? match[1], match[2].replaceAll('-', '.')]
}

function stableModelSlug(title, details = '') {
  const terms = modelMatchTerms(title, details)
  if (terms.length < 2) return null
  return `${terms[0]}-${terms[1]}`
    .normalize('NFKC')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
}

export function classifyAnnouncement(title, details = '') {
  const normalizedTitle = cleanText(title)
  const normalizedDetails = cleanText(details)
  const hasModelName = MODEL_NAME_PATTERN.test(normalizedTitle)
  const titleLaunch =
    /\b(?:introduc(?:e|es|ed|ing)|announc(?:e|es|ed|ing)|launch(?:es|ed|ing)?|releas(?:e|es|ed|ing)|unveil(?:s|ed|ing)?)\b/i.test(
      normalizedTitle,
    )
  const officialModelAvailability =
    /\b(?:frontier|flagship|foundation|language|reasoning|multimodal)\s+model\b/i.test(
      normalizedDetails,
    ) &&
    /\b(?:now available|released|launch(?:es|ed|ing)?|introduc(?:e|ed|ing))\b/i.test(
      normalizedDetails,
    )

  if (hasModelName && (titleLaunch || officialModelAvailability)) {
    return { category: 'major_model_release', required: true }
  }
  return { category: 'official_announcement', required: false }
}

function articleSlug(url) {
  return new URL(url).pathname.split('/').filter(Boolean).at(-1)
}

function toCandidate(item, source) {
  const classification = classifyAnnouncement(item.title, item.details ?? item.summary)
  if (classification.category !== 'major_model_release') return null
  const matchTerms = modelMatchTerms(item.title, item.details)
  if (matchTerms.length < 2) return null
  const summary = truncateText(item.summary || item.title, 420)
  const details = truncateText(item.details || summary, 1_200)
  const stableSlug = item.stableSlug ?? stableModelSlug(item.title, item.details) ?? articleSlug(item.url)
  if (!stableSlug) return null
  return {
    id: `${source.id}:${stableSlug}`,
    title: item.title,
    url: item.url,
    publishedAt: item.publishedAt,
    precision: item.precision,
    category: 'major_model_release',
    required: true,
    officialSource: source.officialSource,
    evidenceUrls: [...new Set([item.url, ...(item.evidenceUrls ?? [])])],
    matchTerms,
    summary,
    details,
  }
}

export function isWithinWindow(publishedAt, now, windowHours, precision = 'instant') {
  const publishedMs = Date.parse(publishedAt)
  if (!Number.isFinite(publishedMs)) return false
  const nowMs = now.getTime()
  const cutoff = nowMs - windowHours * 60 * 60 * 1000
  // Date-only official entries represent an unknown instant within that UTC
  // calendar day.  Count them when the day overlaps the window; otherwise a
  // late-day launch would age out up to 24 hours too early.
  const publicationEndMs =
    precision === 'day' ? publishedMs + 24 * 60 * 60 * 1000 : publishedMs
  return publishedMs <= nowMs && publicationEndMs > cutoff
}

function validateCollectionOptions(now, windowHours) {
  if (!(now instanceof Date) || !Number.isFinite(now.getTime())) {
    throw new TypeError('now must be a valid Date')
  }
  if (!Number.isFinite(windowHours) || windowHours <= 0) {
    throw new TypeError('windowHours must be a positive number')
  }
}

function errorRecord(source, endpoint, stage, error) {
  return {
    source: source.id,
    endpoint: endpoint.id,
    url: endpoint.url,
    stage,
    message: error instanceof Error ? error.message : String(error),
  }
}

async function collectEndpoint(source, endpoint, { fetchImpl, now, windowHours, timeoutMs }) {
  const endpointResult = {
    id: endpoint.id,
    name: endpoint.name,
    url: endpoint.url,
    status: 'error',
    discoveredCount: 0,
    candidateCount: 0,
  }
  const errors = []
  const unresolvedCandidateIds = new Set()

  let response
  try {
    response = await fetchImpl(endpoint.url, {
      headers: {
        accept: 'text/html,application/xhtml+xml',
        'user-agent': 'ai-daily-report-official-sources/1.0',
      },
      redirect: 'follow',
      signal: AbortSignal.timeout(timeoutMs),
    })
  } catch (error) {
    errors.push(errorRecord(source, endpoint, 'fetch', error))
    return { endpointResult, candidates: [], errors }
  }

  if (!response.ok) {
    errors.push(
      errorRecord(
        source,
        endpoint,
        'fetch',
        `HTTP ${response.status}${response.statusText ? ` ${response.statusText}` : ''}`,
      ),
    )
    return { endpointResult, candidates: [], errors }
  }

  let parsed
  try {
    const body = await response.text()
    parsed = endpoint.parser(body, endpoint)
  } catch (error) {
    errors.push(errorRecord(source, endpoint, 'parse', error))
    return { endpointResult, candidates: [], errors }
  }

  endpointResult.discoveredCount = parsed.items.length
  const parserHasErrors = parsed.errors.length > 0
  for (const message of parsed.errors) {
    errors.push(errorRecord(source, endpoint, 'parse', message))
  }
  let filteredItems = parsed.items.filter((item) => {
      if (Date.parse(item.publishedAt) > now.getTime()) {
        errors.push(
          errorRecord(
            source,
            endpoint,
            'filter',
            `${item.url}: future publishedAt ${item.publishedAt} was excluded`,
          ),
        )
        return false
      }
      return isWithinWindow(item.publishedAt, now, windowHours, item.precision)
    })
  if (endpoint.verifyArticles) {
    const verifiedItems = []
    const uniqueSignals = new Map()
    for (const item of filteredItems) {
      // Deduplicate repeated submissions of the same canonical URL, but keep
      // distinct URLs for one model id: a newer fake/404 link must not hide an
      // earlier genuine official article for the same version.
      if (!uniqueSignals.has(item.url)) uniqueSignals.set(item.url, item)
    }
    for (const item of uniqueSignals.values()) {
      const verificationEndpoint = { ...endpoint, url: item.url }
      const signalCandidateId = toCandidate(item, source)?.id
      try {
        const articleResponse = await fetchImpl(item.url, {
          headers: {
            accept: 'text/html,application/xhtml+xml',
            'user-agent': 'ai-daily-report-official-sources/1.0',
          },
          redirect: 'follow',
          signal: AbortSignal.timeout(timeoutMs),
        })
        if (articleResponse.status === 404 || articleResponse.status === 410) {
          throw new RejectedSignalError(
            `HTTP ${articleResponse.status}${articleResponse.statusText ? ` ${articleResponse.statusText}` : ''}`,
          )
        }
        if (!articleResponse.ok) {
          throw new Error(
            `HTTP ${articleResponse.status}${articleResponse.statusText ? ` ${articleResponse.statusText}` : ''}`,
          )
        }
        const verified = parseSpaceXaiArticle(await articleResponse.text(), item.url)
        const signalTerms = modelMatchTerms(item.title, item.details)
        const articleTerms = modelMatchTerms(verified.title, verified.details)
        if (
          signalTerms.length !== 2 ||
          articleTerms.length !== 2 ||
          signalTerms.some((term, index) => term.toLowerCase() !== articleTerms[index].toLowerCase())
        ) {
          throw new RejectedSignalError(
            `article model ${articleTerms.join(' ')} does not match signal ${signalTerms.join(' ')}`,
          )
        }
        if (
          Date.parse(verified.publishedAt) > now.getTime()
        ) {
          throw new RejectedSignalError(
            `verified article has future publishedAt ${verified.publishedAt}`,
          )
        }
        // A newly submitted HN link can point to an old official article. That
        // signal is resolved, but it is not a new release for this window.
        if (!isWithinWindow(verified.publishedAt, now, windowHours, verified.precision)) {
          continue
        }
        verified.stableSlug = item.stableSlug
        verifiedItems.push(verified)
      } catch (error) {
        errors.push(errorRecord(source, verificationEndpoint, 'verify', error))
        // A first-party 404/410 or successfully parsed contradictory article
        // conclusively rejects a community signal. Network/server failures and
        // malformed 2xx official pages remain unresolved and fail closed.
        if (signalCandidateId && !(error instanceof RejectedSignalError)) {
          unresolvedCandidateIds.add(signalCandidateId)
        }
      }
    }
    filteredItems = verifiedItems
  }
  const candidates = filteredItems
    .map((item) => toCandidate(item, source))
    .filter(Boolean)
  endpointResult.candidateCount = candidates.length
  endpointResult.status = errors.length ? 'partial' : 'ok'
  return {
    endpointResult,
    candidates,
    errors,
    unresolvedCandidateIds: [...unresolvedCandidateIds],
    coverageSufficient:
      (!endpoint.corroborating && !parserHasErrors) ||
      (endpoint.corroborating && candidates.length > 0),
  }
}

function mergeCandidate(previous, candidate) {
  if (!previous) return candidate
  const preferCandidateTitle =
    !/\b(?:introducing|announcing|released|launching)\b/i.test(previous.title) &&
    /\b(?:introducing|announcing|released|launching)\b/i.test(candidate.title)
  const preferCandidateUrl =
    !/^https:\/\/x\.ai\/news\//i.test(previous.url) &&
    /^https:\/\/x\.ai\/news\//i.test(candidate.url)
  return {
    ...previous,
    title: preferCandidateTitle ? candidate.title : previous.title,
    url: preferCandidateUrl ? candidate.url : previous.url,
    publishedAt:
      previous.precision === 'day' && candidate.precision === 'instant'
        ? candidate.publishedAt
        : previous.publishedAt,
    precision:
      previous.precision === 'day' && candidate.precision === 'instant'
        ? candidate.precision
        : previous.precision,
    evidenceUrls: [...new Set([...previous.evidenceUrls, ...candidate.evidenceUrls])],
    summary: previous.summary || candidate.summary,
    details:
      candidate.details.length > previous.details.length ? candidate.details : previous.details,
  }
}

async function collectSourceGroup(source, options) {
  if (!Array.isArray(source.endpoints) || source.endpoints.length === 0) {
    throw new TypeError(`${source.id}: endpoints must be a non-empty array`)
  }
  const endpointResults = await Promise.all(
    source.endpoints.map((endpoint) => collectEndpoint(source, endpoint, options)),
  )
  const merged = new Map()
  for (const candidate of endpointResults.flatMap((result) => result.candidates)) {
    merged.set(candidate.id, mergeCandidate(merged.get(candidate.id), candidate))
  }
  const candidates = [...merged.values()]
  const unresolvedSignals = new Set(
    endpointResults.flatMap((result) => result.unresolvedCandidateIds ?? []),
  )
  for (const candidate of candidates) unresolvedSignals.delete(candidate.id)
  const successfulEndpoints = endpointResults.filter(
    (result) => result.endpointResult.status !== 'error',
  )
  // A fully parsed authoritative endpoint can prove that the recent window is
  // genuinely empty; a corroborating endpoint can only prove coverage when it
  // found an exact official release URL. Keep this independent from the final
  // merged count so a healthy changelog can certify genuinely quiet days.
  const coverageSufficient =
    unresolvedSignals.size === 0 &&
    endpointResults.some((result) => result.coverageSufficient === true)
  const errors = endpointResults.flatMap((result) => result.errors)
  return {
    sourceResult: {
      id: source.id,
      name: source.name,
      officialSource: source.officialSource,
      critical: source.critical === true,
      coverageSufficient,
      status:
        successfulEndpoints.length === 0 ? 'error' : errors.length > 0 ? 'partial' : 'ok',
      fetchedAt: options.now.toISOString(),
      discoveredCount: endpointResults.reduce(
        (total, result) => total + result.endpointResult.discoveredCount,
        0,
      ),
      candidateCount: candidates.length,
      unresolvedSignals: [...unresolvedSignals].sort(),
      endpoints: endpointResults.map((result) => result.endpointResult),
    },
    candidates,
    errors,
  }
}

/** Collect recent official announcements. Network and time are injectable for tests. */
export async function collectOfficialPrioritySources({
  sources = SOURCE_DEFINITIONS,
  fetchImpl = globalThis.fetch,
  now = new Date(),
  windowHours = DEFAULT_WINDOW_HOURS,
  timeoutMs = DEFAULT_TIMEOUT_MS,
} = {}) {
  validateCollectionOptions(now, windowHours)
  if (typeof fetchImpl !== 'function') throw new TypeError('fetchImpl must be a function')
  if (!Array.isArray(sources) || sources.length === 0) {
    throw new TypeError('sources must be a non-empty array')
  }

  const results = await Promise.all(
    sources.map((source) =>
      collectSourceGroup(source, { fetchImpl, now, windowHours, timeoutMs }),
    ),
  )
  const candidates = results
    .flatMap((result) => result.candidates)
    .sort(
      (left, right) =>
        Date.parse(right.publishedAt) - Date.parse(left.publishedAt) ||
        left.id.localeCompare(right.id),
    )

  return {
    schemaVersion: 1,
    generatedAt: now.toISOString(),
    windowHours,
    sources: results.map((result) => result.sourceResult),
    candidates,
    errors: results.flatMap((result) => result.errors),
  }
}

export function parseCliArgs(argv) {
  const options = { windowHours: DEFAULT_WINDOW_HOURS, now: new Date() }
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index]
    if (argument === '--help') return { help: true }

    let name = argument
    let value
    const equals = argument.indexOf('=')
    if (equals >= 0) {
      name = argument.slice(0, equals)
      value = argument.slice(equals + 1)
    } else if (name === '--hours' || name === '--now') {
      value = argv[index + 1]
      index += 1
    }

    if (name === '--hours') {
      options.windowHours = Number(value)
    } else if (name === '--now') {
      options.now = new Date(value)
    } else {
      throw new TypeError(`unknown argument: ${argument}`)
    }
  }
  validateCollectionOptions(options.now, options.windowHours)
  return options
}

async function main() {
  let options
  try {
    options = parseCliArgs(process.argv.slice(2))
  } catch (error) {
    console.error(`official source collector: ${error.message}`)
    process.exitCode = 2
    return
  }

  if (options.help) {
    process.stdout.write(
      'Usage: node scripts/fetch_official_priority_sources.mjs [--hours N] [--now ISO]\n',
    )
    return
  }

  const output = await collectOfficialPrioritySources(options)
  process.stdout.write(`${JSON.stringify(output, null, 2)}\n`)
  const lostCriticalCoverage = output.sources.some(
    (source) => source.critical && source.coverageSufficient !== true,
  )
  if (lostCriticalCoverage || output.sources.every((source) => source.status === 'error')) {
    process.exitCode = 1
  }
}

const entryUrl = process.argv[1] ? pathToFileURL(process.argv[1]).href : null
if (entryUrl === import.meta.url) {
  main().catch((error) => {
    console.error(`official source collector: ${error.message}`)
    process.exitCode = 1
  })
}
