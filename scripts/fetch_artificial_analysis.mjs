#!/usr/bin/env node

/**
 * Collect the public Artificial Analysis Intelligence Index top 10.
 *
 * Artificial Analysis renders the complete leaderboard as a semantic HTML
 * table. This collector intentionally reads that first-party table instead of
 * relying on an undocumented API or executing/parsing framework internals.
 */

import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

export const SOURCE_URL = 'https://artificialanalysis.ai/leaderboards/models/'
export const METHODOLOGY_URL =
  'https://artificialanalysis.ai/methodology/intelligence-benchmarking'
export const METRIC_NAME = 'Artificial Analysis Intelligence Index'
export const TOP_LIMIT = 10

const DEFAULT_TIMEOUT_MS = 30_000
const REPOSITORY_ROOT = fileURLToPath(new URL('../', import.meta.url))
const ARTIFACT_LABEL = 'Artificial Analysis 排名变化'
const SECTION_TITLE = '📊 Artificial Analysis 模型排名'

export const SOURCE = Object.freeze({
  id: 'artificial-analysis-models',
  name: 'Artificial Analysis LLM Leaderboard',
  url: SOURCE_URL,
  methodologyUrl: METHODOLOGY_URL,
  metric: METRIC_NAME,
  method: 'official_public_ssr_table',
})

export class ArtificialAnalysisParseError extends Error {
  constructor(message) {
    super(message)
    this.name = 'ArtificialAnalysisParseError'
  }
}

export class ArtificialAnalysisSnapshotError extends Error {
  constructor(message) {
    super(message)
    this.name = 'ArtificialAnalysisSnapshotError'
  }
}

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function roundedScore(value) {
  return Number(Number(value).toFixed(2))
}

function roundedDelta(value) {
  const rounded = Number(value.toFixed(2))
  return Object.is(rounded, -0) ? 0 : rounded
}

function modelUrl(slug) {
  return new URL(`/models/${encodeURIComponent(slug)}`, SOURCE_URL).href
}

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
  ).replace(/\s+/g, ' ').trim()
}

function readAttribute(attributes, name) {
  const escapedName = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = String(attributes).match(
    new RegExp(`(?:^|\\s)${escapedName}\\s*=\\s*(?:"([^"]*)"|'([^']*)'|([^\\s>]+))`, 'i'),
  )
  return match ? decodeHtml(match[1] ?? match[2] ?? match[3] ?? '') : null
}

function modelLinkFromRow(rowHtml) {
  for (const match of String(rowHtml).matchAll(/<a\b([^>]*)>/gi)) {
    const href = readAttribute(match[1], 'href')
    if (!href) continue
    try {
      const url = new URL(href, SOURCE_URL)
      if (url.origin !== new URL(SOURCE_URL).origin) continue
      const pathMatch = url.pathname.match(/^\/models\/([^/]+)\/?$/i)
      if (!pathMatch) continue
      const slug = decodeURIComponent(pathMatch[1])
      if (!slug || /[\s/]/.test(slug)) continue
      return { slug, url: modelUrl(slug) }
    } catch {
      continue
    }
  }
  return null
}

function parseScore(value, rank) {
  const normalized = cleanText(value).replace(/\s+/g, '')
  const match = normalized.match(/^(-?\d+(?:\.\d+)?)([*＊])?$/)
  if (!match) {
    throw new ArtificialAnalysisParseError(
      `rank ${rank} has an invalid Intelligence Index score: ${JSON.stringify(normalized)}`,
    )
  }
  const score = Number(match[1])
  if (!Number.isFinite(score)) {
    throw new ArtificialAnalysisParseError(`rank ${rank} has a non-finite score`)
  }
  return { score: roundedScore(score), estimated: Boolean(match[2]) }
}

function parseLeaderboardRow(rowHtml, rank) {
  const cells = [...String(rowHtml).matchAll(/<td\b[^>]*>([\s\S]*?)<\/td>/gi)]
    .map((match) => match[1])
  if (cells.length < 4) {
    throw new ArtificialAnalysisParseError(
      `rank ${rank} row has ${cells.length} cells; expected at least 4`,
    )
  }

  const link = modelLinkFromRow(rowHtml)
  const name = cleanText(cells[0])
  const creator = cleanText(cells[2])
  if (!link) {
    throw new ArtificialAnalysisParseError(`rank ${rank} has no canonical /models/{slug} link`)
  }
  if (!name || !creator) {
    throw new ArtificialAnalysisParseError(`rank ${rank} has an empty model name or creator`)
  }

  const { score, estimated } = parseScore(cells[3], rank)
  return {
    rank,
    slug: link.slug,
    name,
    creator,
    score,
    estimated,
    url: link.url,
  }
}

/** Parse the first-party SSR HTML and return a persistence-ready snapshot. */
export function parseArtificialAnalysisLeaderboard(html, { limit = TOP_LIMIT } = {}) {
  if (typeof html !== 'string' || html.trim() === '') {
    throw new ArtificialAnalysisParseError('leaderboard HTML is empty')
  }
  if (!Number.isInteger(limit) || limit < 1) {
    throw new TypeError('limit must be a positive integer')
  }

  const tables = [...html.matchAll(/<table\b[^>]*>([\s\S]*?)<\/table>/gi)]
    .map((match) => match[1])
    .filter((table) => {
      const header = table.match(/<thead\b[^>]*>([\s\S]*?)<\/thead>/i)?.[1]
      return header && cleanText(header).includes(METRIC_NAME)
    })
  if (tables.length !== 1) {
    throw new ArtificialAnalysisParseError(
      `expected exactly one SSR leaderboard table for ${METRIC_NAME}, found ${tables.length}`,
    )
  }

  const body = tables[0].match(/<tbody\b[^>]*>([\s\S]*?)<\/tbody>/i)?.[1]
  if (!body) throw new ArtificialAnalysisParseError('leaderboard table has no tbody')
  const rows = [...body.matchAll(/<tr\b[^>]*>([\s\S]*?)<\/tr>/gi)]
  if (rows.length < limit) {
    throw new ArtificialAnalysisParseError(
      `leaderboard table contains ${rows.length} rows; expected at least ${limit}`,
    )
  }

  const models = rows.slice(0, limit).map((row, index) =>
    parseLeaderboardRow(row[1], index + 1),
  )
  const slugs = new Set(models.map((model) => model.slug))
  if (slugs.size !== models.length) {
    throw new ArtificialAnalysisParseError('top leaderboard rows contain duplicate model slugs')
  }
  for (let index = 1; index < models.length; index += 1) {
    if (models[index].score > models[index - 1].score) {
      throw new ArtificialAnalysisParseError(
        `leaderboard is not sorted by descending Intelligence Index at rank ${index + 1}`,
      )
    }
  }

  return {
    schemaVersion: 1,
    sourceUrl: SOURCE_URL,
    metric: METRIC_NAME,
    methodologyVersion: null,
    limit,
    models,
  }
}

// A short alias is convenient for callers and keeps the parsing contract clear.
export const parseLeaderboardHtml = parseArtificialAnalysisLeaderboard

/** Parse the current version from the public Intelligence benchmarking page. */
export function parseMethodologyVersion(html) {
  if (typeof html !== 'string' || html.trim() === '') {
    throw new ArtificialAnalysisParseError('methodology HTML is empty')
  }

  const versions = new Set()
  const headingPattern = /<(h[1-6])\b[^>]*>([\s\S]*?)<\/\1>/gi
  for (const match of html.matchAll(headingPattern)) {
    const text = cleanText(match[2])
    const version = text.match(
      /^Artificial Analysis Intelligence Index\s+v(\d+\.\d+(?:\.\d+)?)$/i,
    )?.[1]
    if (version) versions.add(version)
  }

  if (versions.size !== 1) {
    throw new ArtificialAnalysisParseError(
      `expected exactly one current Intelligence Index methodology version, found ${versions.size}`,
    )
  }
  return [...versions][0]
}

function normalizeSnapshotModel(value, expectedRank) {
  if (!isRecord(value)) {
    throw new ArtificialAnalysisSnapshotError(`model at rank ${expectedRank} must be an object`)
  }

  const rank = Number(value.rank)
  const slug = typeof value.slug === 'string' ? value.slug.trim() : ''
  const name = typeof value.name === 'string' ? value.name.trim() : ''
  const score = Number(value.score)
  if (rank !== expectedRank) {
    throw new ArtificialAnalysisSnapshotError(
      `snapshot ranks must be consecutive; expected ${expectedRank}, got ${value.rank}`,
    )
  }
  if (!slug || !name || !Number.isFinite(score)) {
    throw new ArtificialAnalysisSnapshotError(
      `snapshot model at rank ${expectedRank} needs non-empty slug/name and a finite score`,
    )
  }

  const creator =
    typeof value.creator === 'string' && value.creator.trim() !== ''
      ? value.creator.trim()
      : null

  return {
    rank,
    slug,
    name,
    creator,
    score: roundedScore(score),
    estimated: value.estimated === true,
    url: modelUrl(slug),
  }
}

/** Accept a standalone snapshot or a prior collector output. */
export function normalizeSnapshot(document, { limit = TOP_LIMIT } = {}) {
  let snapshot = document
  if (isRecord(snapshot?.currentSnapshot)) snapshot = snapshot.currentSnapshot
  else if (isRecord(snapshot?.current) && Array.isArray(snapshot.current.models)) {
    snapshot = snapshot.current
  }

  if (!isRecord(snapshot) || !Array.isArray(snapshot.models)) {
    throw new ArtificialAnalysisSnapshotError('snapshot must contain a models array')
  }
  if (snapshot.models.length !== limit) {
    throw new ArtificialAnalysisSnapshotError(
      `snapshot must contain exactly ${limit} models, got ${snapshot.models.length}`,
    )
  }

  const methodologyVersion = snapshot.methodologyVersion ?? null
  if (
    methodologyVersion !== null &&
    (typeof methodologyVersion !== 'string' ||
      !/^\d+\.\d+(?:\.\d+)?$/.test(methodologyVersion))
  ) {
    throw new ArtificialAnalysisSnapshotError(
      'snapshot methodologyVersion must be null or a dotted numeric version',
    )
  }

  const models = snapshot.models.map((model, index) =>
    normalizeSnapshotModel(model, index + 1),
  )
  const slugs = new Set(models.map((model) => model.slug))
  if (slugs.size !== models.length) {
    throw new ArtificialAnalysisSnapshotError('snapshot contains duplicate model slugs')
  }

  return {
    schemaVersion: 1,
    sourceUrl: SOURCE_URL,
    metric: METRIC_NAME,
    methodologyVersion,
    limit,
    models,
  }
}

/**
 * Compare two normalized top-10 snapshots.
 *
 * Ordering is stable: methodology, entrants, exits, rank changes, score
 * changes, then metadata changes; models follow their current rank (or prior
 * rank for exits).
 */
export function compareSnapshots(previous, current) {
  const normalizedCurrent = normalizeSnapshot(current, { limit: current?.limit ?? TOP_LIMIT })
  if (previous === null || previous === undefined) return []
  const normalizedPrevious = normalizeSnapshot(previous, {
    limit: normalizedCurrent.limit,
  })

  const previousBySlug = new Map(
    normalizedPrevious.models.map((model) => [model.slug, model]),
  )
  const currentBySlug = new Map(
    normalizedCurrent.models.map((model) => [model.slug, model]),
  )
  const changes = []

  if (
    normalizedPrevious.methodologyVersion !== normalizedCurrent.methodologyVersion
  ) {
    changes.push({
      type: 'methodology_changed',
      previousVersion: normalizedPrevious.methodologyVersion,
      currentVersion: normalizedCurrent.methodologyVersion,
    })
  }

  for (const model of normalizedCurrent.models) {
    if (previousBySlug.has(model.slug)) continue
    changes.push({
      type: 'entered_top_10',
      slug: model.slug,
      name: model.name,
      creator: model.creator,
      currentRank: model.rank,
      currentScore: model.score,
    })
  }

  for (const model of normalizedPrevious.models) {
    if (currentBySlug.has(model.slug)) continue
    changes.push({
      type: 'exited_top_10',
      slug: model.slug,
      name: model.name,
      creator: model.creator,
      previousRank: model.rank,
      previousScore: model.score,
    })
  }

  for (const model of normalizedCurrent.models) {
    const oldModel = previousBySlug.get(model.slug)
    if (!oldModel || oldModel.rank === model.rank) continue
    const rankDelta = oldModel.rank - model.rank
    changes.push({
      type: 'rank_changed',
      slug: model.slug,
      name: model.name,
      creator: model.creator,
      previousRank: oldModel.rank,
      currentRank: model.rank,
      rankDelta,
      direction: rankDelta > 0 ? 'up' : 'down',
    })
  }

  for (const model of normalizedCurrent.models) {
    const oldModel = previousBySlug.get(model.slug)
    if (!oldModel || oldModel.score === model.score) continue
    const scoreDelta = roundedDelta(model.score - oldModel.score)
    changes.push({
      type: 'score_changed',
      slug: model.slug,
      name: model.name,
      creator: model.creator,
      currentRank: model.rank,
      previousScore: oldModel.score,
      currentScore: model.score,
      scoreDelta,
      direction: scoreDelta > 0 ? 'up' : 'down',
    })
  }

  const metadataFields = ['name', 'creator', 'estimated']
  for (const model of normalizedCurrent.models) {
    const oldModel = previousBySlug.get(model.slug)
    if (!oldModel) continue
    const changedFields = metadataFields.filter(
      (field) => oldModel[field] !== model[field],
    )
    if (changedFields.length === 0) continue
    changes.push({
      type: 'metadata_changed',
      slug: model.slug,
      name: model.name,
      creator: model.creator,
      currentRank: model.rank,
      changedFields,
      previousMetadata: {
        name: oldModel.name,
        creator: oldModel.creator,
        estimated: oldModel.estimated,
      },
      currentMetadata: {
        name: model.name,
        creator: model.creator,
        estimated: model.estimated,
      },
    })
  }

  return changes
}

function validDate(value, label = 'date') {
  const date = value instanceof Date ? new Date(value.getTime()) : new Date(value)
  if (!Number.isFinite(date.getTime())) throw new TypeError(`${label} must be a valid date`)
  return date
}

export function formatShanghaiTimestamp(value) {
  const date = validDate(value)
  const shanghai = new Date(date.getTime() + 8 * 60 * 60 * 1000)
  return `${shanghai.toISOString().slice(0, 19)}+08:00`
}

export function reportDateFor(value) {
  return formatShanghaiTimestamp(value).slice(0, 10)
}

function scoreText(value) {
  return Number(value).toFixed(2).replace(/\.00$/, '')
}

function sourceList(url = SOURCE_URL) {
  return [{ name: 'Artificial Analysis（单一来源）', url }]
}

function artifactItemFor(change) {
  const shared = { expanded: false, sources: sourceList() }
  switch (change.type) {
    case 'methodology_changed': {
      const previous = change.previousVersion === null
        ? '未记录'
        : `v${change.previousVersion}`
      const current = change.currentVersion === null
        ? '未记录'
        : `v${change.currentVersion}`
      return {
        headline: `评测方法更新：Intelligence Index ${previous} → ${current}`,
        summary: `${METRIC_NAME} 的公开方法版本由 ${previous} 更新为 ${current}；跨版本分数和名次需结合方法调整解读。`,
        expanded: false,
        sources: sourceList(METHODOLOGY_URL),
      }
    }
    case 'entered_top_10':
      return {
        headline: `新进 Top 10：${change.name} 升至第 ${change.currentRank} 名`,
        summary: `${change.name} 新进入 ${METRIC_NAME} 前十，当前第 ${change.currentRank} 名，得分 ${scoreText(change.currentScore)}。`,
        ...shared,
      }
    case 'exited_top_10':
      return {
        headline: `退出 Top 10：${change.name}`,
        summary: `${change.name} 退出 ${METRIC_NAME} 前十；上一快照为第 ${change.previousRank} 名，得分 ${scoreText(change.previousScore)}。`,
        ...shared,
      }
    case 'rank_changed': {
      const movement = change.direction === 'up' ? '上升' : '下降'
      return {
        headline: `排名${movement}：${change.name} 第 ${change.previousRank} → ${change.currentRank} 名`,
        summary: `${change.name} 在 ${METRIC_NAME} 中${movement} ${Math.abs(change.rankDelta)} 位，由第 ${change.previousRank} 名变为第 ${change.currentRank} 名。`,
        ...shared,
      }
    }
    case 'score_changed': {
      const sign = change.scoreDelta > 0 ? '+' : ''
      return {
        headline: `分数变化：${change.name} ${scoreText(change.previousScore)} → ${scoreText(change.currentScore)}`,
        summary: `${change.name} 当前位列第 ${change.currentRank}，Intelligence Index 得分变化 ${sign}${scoreText(change.scoreDelta)}，由 ${scoreText(change.previousScore)} 变为 ${scoreText(change.currentScore)}。`,
        ...shared,
      }
    }
    case 'metadata_changed': {
      const descriptions = change.changedFields.map((field) => {
        if (field === 'name') {
          return `名称“${change.previousMetadata.name}”→“${change.currentMetadata.name}”`
        }
        if (field === 'creator') {
          const previous = change.previousMetadata.creator ?? '未标注'
          const current = change.currentMetadata.creator ?? '未标注'
          return `开发者“${previous}”→“${current}”`
        }
        const previous = change.previousMetadata.estimated ? '估算分' : '正式分'
        const current = change.currentMetadata.estimated ? '估算分' : '正式分'
        return `分数标记“${previous}”→“${current}”`
      })
      return {
        headline: `榜单信息更新：${change.name}`,
        summary: `${change.name} 当前位列第 ${change.currentRank}，官方榜单元数据更新：${descriptions.join('；')}。`,
        ...shared,
      }
    }
    default:
      throw new TypeError(`unsupported change type: ${change.type}`)
  }
}

export function buildArtifact({ reportDate, generatedAt, currentSnapshot, changes }) {
  if (!Array.isArray(changes) || changes.length === 0) return null
  const normalizedCurrent = normalizeSnapshot(currentSnapshot)
  const leader = normalizedCurrent.models[0]
  const shanghaiGeneratedAt = formatShanghaiTimestamp(generatedAt)
  const dateStamp = shanghaiGeneratedAt.slice(0, 10).replaceAll('-', '')
  const timeStamp = shanghaiGeneratedAt.slice(11, 19).replaceAll(':', '')

  return {
    path: `content/artifacts/artificial-analysis-${dateStamp}-${timeStamp}.json`,
    document: {
      date: reportDate,
      label: ARTIFACT_LABEL,
      attachTo: reportDate,
      generatedAt: shanghaiGeneratedAt,
      sections: [
        {
          title: SECTION_TITLE,
          note: `与上一份快照相比发现 ${changes.length} 项变化；当前榜首为 ${leader.name}（${scoreText(leader.score)} 分）。`,
          items: changes.map(artifactItemFor),
        },
      ],
    },
  }
}

async function fetchOfficialHtml({
  fetchImpl = globalThis.fetch,
  url,
  label,
  timeoutMs = DEFAULT_TIMEOUT_MS,
} = {}) {
  if (typeof fetchImpl !== 'function') throw new TypeError('fetchImpl must be a function')

  const response = await fetchImpl(url, {
    headers: {
      accept: 'text/html,application/xhtml+xml',
      'user-agent': 'ai-daily-report/1.0',
    },
    redirect: 'follow',
    signal: AbortSignal.timeout(timeoutMs),
  })
  if (!response?.ok) {
    const status = response?.status ?? 'unknown'
    const statusText = response?.statusText ? ` ${response.statusText}` : ''
    throw new Error(`${label} fetch failed: HTTP ${status}${statusText}`)
  }

  const html = await response.text()
  if (!html.trim()) throw new Error(`${label} fetch returned an empty body`)
  return html
}

export async function fetchLeaderboardHtml({
  fetchImpl = globalThis.fetch,
  sourceUrl = SOURCE_URL,
  timeoutMs = DEFAULT_TIMEOUT_MS,
} = {}) {
  return fetchOfficialHtml({
    fetchImpl,
    url: sourceUrl,
    label: 'Artificial Analysis leaderboard',
    timeoutMs,
  })
}

export async function fetchMethodologyHtml({
  fetchImpl = globalThis.fetch,
  methodologyUrl = METHODOLOGY_URL,
  timeoutMs = DEFAULT_TIMEOUT_MS,
} = {}) {
  return fetchOfficialHtml({
    fetchImpl,
    url: methodologyUrl,
    label: 'Artificial Analysis methodology',
    timeoutMs,
  })
}

export async function collectArtificialAnalysis({
  fetchImpl = globalThis.fetch,
  now = new Date(),
  previousSnapshot = null,
  timeoutMs = DEFAULT_TIMEOUT_MS,
} = {}) {
  const collectedAt = validDate(now, 'now')
  const reportDate = reportDateFor(collectedAt)
  const previous =
    previousSnapshot === null || previousSnapshot === undefined
      ? null
      : normalizeSnapshot(previousSnapshot)
  const [html, methodologyHtml] = await Promise.all([
    fetchLeaderboardHtml({ fetchImpl, timeoutMs }),
    fetchMethodologyHtml({ fetchImpl, timeoutMs }),
  ])
  const currentSnapshot = parseArtificialAnalysisLeaderboard(html)
  currentSnapshot.methodologyVersion = parseMethodologyVersion(methodologyHtml)
  const changes = compareSnapshots(previous, currentSnapshot)
  const status = previous === null ? 'baseline' : changes.length > 0 ? 'changed' : 'unchanged'
  const artifact = buildArtifact({
    reportDate,
    generatedAt: collectedAt,
    currentSnapshot,
    changes,
  })

  return {
    schemaVersion: 1,
    reportDate,
    generatedAt: collectedAt.toISOString(),
    status,
    source: SOURCE,
    previousSnapshot: previous,
    currentSnapshot,
    // Explicit aliases preserve the simpler current/previous collector contract.
    previous,
    current: currentSnapshot,
    changes,
    artifact,
  }
}

export async function loadPreviousSnapshot(snapshotPath) {
  if (!snapshotPath) return null
  let serialized
  try {
    serialized = await readFile(snapshotPath, 'utf8')
  } catch (error) {
    if (error?.code === 'ENOENT') return null
    throw new ArtificialAnalysisSnapshotError(
      `could not read snapshot ${snapshotPath}: ${error.message}`,
    )
  }

  let document
  try {
    document = JSON.parse(serialized)
  } catch (error) {
    throw new ArtificialAnalysisSnapshotError(
      `snapshot ${snapshotPath} is not valid JSON: ${error.message}`,
    )
  }
  return normalizeSnapshot(document)
}

export async function writeArtifactFile(artifact, destination = true) {
  if (artifact === null) return null
  if (!isRecord(artifact) || !isRecord(artifact.document) || !artifact.path) {
    throw new TypeError('artifact must contain path and document')
  }

  const defaultPath = path.join(REPOSITORY_ROOT, artifact.path)
  let outputPath = defaultPath
  if (typeof destination === 'string') {
    const resolved = path.resolve(destination)
    outputPath = path.extname(resolved).toLowerCase() === '.json'
      ? resolved
      : path.join(resolved, path.basename(artifact.path))
  } else if (destination !== true) {
    throw new TypeError('artifact destination must be true, a file, or a directory')
  }

  await mkdir(path.dirname(outputPath), { recursive: true })
  await writeFile(outputPath, `${JSON.stringify(artifact.document, null, 2)}\n`, 'utf8')
  return outputPath
}

function requiredValue(argv, index, option) {
  const value = argv[index + 1]
  if (!value || value.startsWith('--')) throw new Error(`${option} requires a value`)
  return value
}

export function parseCliArgs(argv) {
  const options = {
    now: null,
    snapshot: null,
    writeArtifact: false,
    help: false,
  }

  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index]
    if (argument === '--help' || argument === '-h') {
      options.help = true
    } else if (argument === '--now') {
      options.now = requiredValue(argv, index, '--now')
      index += 1
    } else if (argument.startsWith('--now=')) {
      options.now = argument.slice('--now='.length)
      if (!options.now) throw new Error('--now requires a value')
    } else if (argument === '--snapshot') {
      options.snapshot = requiredValue(argv, index, '--snapshot')
      index += 1
    } else if (argument.startsWith('--snapshot=')) {
      options.snapshot = argument.slice('--snapshot='.length)
      if (!options.snapshot) throw new Error('--snapshot requires a value')
    } else if (argument === '--write-artifact') {
      const next = argv[index + 1]
      if (next && !next.startsWith('--')) {
        options.writeArtifact = next
        index += 1
      } else {
        options.writeArtifact = true
      }
    } else if (argument.startsWith('--write-artifact=')) {
      options.writeArtifact = argument.slice('--write-artifact='.length)
      if (!options.writeArtifact) throw new Error('--write-artifact path cannot be empty')
    } else {
      throw new Error(`unknown argument: ${argument}`)
    }
  }

  if (options.now !== null) validDate(options.now, '--now')
  return options
}

export function usage() {
  return [
    'Usage: node scripts/fetch_artificial_analysis.mjs [options]',
    '',
    '  --now <ISO-8601>          Freeze collection/report time',
    '  --snapshot <file>         Read the previous snapshot (missing is baseline)',
    '  --write-artifact [path]   Write a changed attachment; default: content/artifacts',
  ].join('\n')
}

export async function runCli(argv = process.argv.slice(2)) {
  const options = parseCliArgs(argv)
  if (options.help) {
    process.stdout.write(`${usage()}\n`)
    return null
  }

  const previousSnapshot = await loadPreviousSnapshot(options.snapshot)
  const output = await collectArtificialAnalysis({
    now: options.now ?? new Date(),
    previousSnapshot,
  })
  if (options.writeArtifact && output.artifact) {
    await writeArtifactFile(output.artifact, options.writeArtifact)
  }
  process.stdout.write(`${JSON.stringify(output, null, 2)}\n`)
  return output
}

const entryUrl = process.argv[1] ? pathToFileURL(process.argv[1]).href : null
if (entryUrl === import.meta.url) {
  try {
    await runCli()
  } catch (error) {
    console.error(`failed to collect Artificial Analysis leaderboard: ${error.message}`)
    process.exitCode = 1
  }
}
