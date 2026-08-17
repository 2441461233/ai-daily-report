import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'

import {
  ArtificialAnalysisParseError,
  collectArtificialAnalysis,
  compareSnapshots,
  loadPreviousSnapshot,
  normalizeSnapshot,
  parseArtificialAnalysisLeaderboard,
  parseCliArgs,
  parseMethodologyVersion,
  writeArtifactFile,
} from '../scripts/fetch_artificial_analysis.mjs'

const fixture = await readFile(
  new URL('./fixtures/artificial-analysis-leaderboard.html', import.meta.url),
  'utf8',
)
const scriptPath = fileURLToPath(
  new URL('../scripts/fetch_artificial_analysis.mjs', import.meta.url),
)

function htmlResponse(body, init = {}) {
  return new Response(body, {
    status: 200,
    headers: { 'content-type': 'text/html; charset=utf-8' },
    ...init,
  })
}

function previousSnapshot() {
  const allModels = parseArtificialAnalysisLeaderboard(fixture, { limit: 12 }).models
  const bySlug = new Map(allModels.map((model) => [model.slug, model]))
  const order = [
    'atlas-alpha',
    'cobalt-gamma',
    'beacon-beta',
    'delta-prime',
    'ember-epsilon',
    'forge-zeta',
    'glint-eta',
    'halo-theta',
    'ion-iota',
    'kite-lambda',
  ]

  return {
    schemaVersion: 1,
    sourceUrl: 'https://artificialanalysis.ai/leaderboards/models/',
    metric: 'Artificial Analysis Intelligence Index',
    methodologyVersion: '4.1.1',
    limit: 10,
    models: order.map((slug, index) => ({
      ...bySlug.get(slug),
      rank: index + 1,
      score: slug === 'atlas-alpha' ? 90.5 : bySlug.get(slug).score,
    })),
  }
}

test('SSR table parser preserves the displayed Intelligence Index top 10', () => {
  const snapshot = parseArtificialAnalysisLeaderboard(fixture)

  assert.equal(snapshot.schemaVersion, 1)
  assert.equal(snapshot.metric, 'Artificial Analysis Intelligence Index')
  assert.equal(snapshot.limit, 10)
  assert.equal(snapshot.models.length, 10)
  assert.deepEqual(
    snapshot.models.map(({ rank, slug, score }) => ({ rank, slug, score })),
    [
      { rank: 1, slug: 'atlas-alpha', score: 91.13 },
      { rank: 2, slug: 'beacon-beta', score: 88.99 },
      { rank: 3, slug: 'cobalt-gamma', score: 86.5 },
      { rank: 4, slug: 'delta-prime', score: 84 },
      { rank: 5, slug: 'ember-epsilon', score: 82.75 },
      { rank: 6, slug: 'forge-zeta', score: 80.25 },
      { rank: 7, slug: 'glint-eta', score: 79 },
      { rank: 8, slug: 'halo-theta', score: 77.5 },
      { rank: 9, slug: 'ion-iota', score: 75 },
      { rank: 10, slug: 'jade-kappa', score: 73.25 },
    ],
  )
  assert.equal(snapshot.models[0].creator, 'Atlas Research')
  assert.equal(snapshot.models[0].estimated, true)
  assert.equal(
    snapshot.models[0].url,
    'https://artificialanalysis.ai/models/atlas-alpha',
  )
  assert.equal(snapshot.models.some((model) => model.slug === 'retired-zenith'), false)
  assert.equal(snapshot.models.some((model) => model.slug === 'unscored-preview'), false)
})

test('methodology parser extracts the public current Intelligence Index version', () => {
  assert.equal(parseMethodologyVersion(fixture), '4.1.1')
  assert.throws(
    () => parseMethodologyVersion('<h3>Intelligence methodology</h3>'),
    /methodology version/,
  )
})

test('parser fails closed when the official SSR payload is missing or malformed', () => {
  assert.throws(
    () => parseArtificialAnalysisLeaderboard('<html><body>no ranking</body></html>'),
    ArtificialAnalysisParseError,
  )
  assert.throws(
    () => parseArtificialAnalysisLeaderboard(fixture.replace('91.13', 'not-a-score')),
    /invalid Intelligence Index score/,
  )
})

test('snapshot comparison reports membership, rank, and score changes in stable order', () => {
  const current = parseArtificialAnalysisLeaderboard(fixture)
  current.methodologyVersion = '4.1.1'
  const changes = compareSnapshots(previousSnapshot(), current)

  assert.deepEqual(changes.map((change) => change.type), [
    'entered_top_10',
    'exited_top_10',
    'rank_changed',
    'rank_changed',
    'score_changed',
  ])
  assert.deepEqual(changes[0], {
    type: 'entered_top_10',
    slug: 'jade-kappa',
    name: 'Jade Kappa',
    creator: 'Jade AI',
    currentRank: 10,
    currentScore: 73.25,
  })
  assert.deepEqual(changes[1], {
    type: 'exited_top_10',
    slug: 'kite-lambda',
    name: 'Kite Lambda',
    creator: 'Kite Research',
    previousRank: 10,
    previousScore: 72,
  })
  assert.equal(changes[2].slug, 'beacon-beta')
  assert.equal(changes[2].rankDelta, 1)
  assert.equal(changes[2].direction, 'up')
  assert.equal(changes[3].slug, 'cobalt-gamma')
  assert.equal(changes[3].rankDelta, -1)
  assert.equal(changes[3].direction, 'down')
  assert.deepEqual(changes[4], {
    type: 'score_changed',
    slug: 'atlas-alpha',
    name: 'Atlas Alpha',
    creator: 'Atlas Research',
    currentRank: 1,
    previousScore: 90.5,
    currentScore: 91.13,
    scoreDelta: 0.63,
    direction: 'up',
  })
})

test('name, creator, and estimated changes produce stable metadata events', () => {
  const current = parseArtificialAnalysisLeaderboard(fixture)
  current.methodologyVersion = '4.1.1'
  const previous = structuredClone(current)
  previous.models[0].name = 'Atlas Alpha Preview'
  previous.models[1].creator = 'Beacon Research'
  previous.models[2].estimated = true

  const changes = compareSnapshots(previous, current)
  assert.deepEqual(changes.map((change) => change.type), [
    'metadata_changed',
    'metadata_changed',
    'metadata_changed',
  ])
  assert.deepEqual(changes[0], {
    type: 'metadata_changed',
    slug: 'atlas-alpha',
    name: 'Atlas Alpha',
    creator: 'Atlas Research',
    currentRank: 1,
    changedFields: ['name'],
    previousMetadata: {
      name: 'Atlas Alpha Preview',
      creator: 'Atlas Research',
      estimated: true,
    },
    currentMetadata: {
      name: 'Atlas Alpha',
      creator: 'Atlas Research',
      estimated: true,
    },
  })
  assert.deepEqual(changes[1].changedFields, ['creator'])
  assert.equal(changes[1].previousMetadata.creator, 'Beacon Research')
  assert.equal(changes[1].currentMetadata.creator, 'Beacon Labs')
  assert.deepEqual(changes[2].changedFields, ['estimated'])
  assert.equal(changes[2].previousMetadata.estimated, true)
  assert.equal(changes[2].currentMetadata.estimated, false)
})

test('first collection is a baseline with no changes or attachment', async () => {
  const requestedUrls = []
  const output = await collectArtificialAnalysis({
    fetchImpl: async (url) => {
      requestedUrls.push(url)
      return htmlResponse(fixture)
    },
    now: new Date('2026-08-17T01:15:00.000Z'),
  })

  assert.deepEqual(requestedUrls.sort(), [
    'https://artificialanalysis.ai/leaderboards/models/',
    'https://artificialanalysis.ai/methodology/intelligence-benchmarking',
  ])
  assert.equal(output.reportDate, '2026-08-17')
  assert.equal(output.generatedAt, '2026-08-17T01:15:00.000Z')
  assert.equal(output.status, 'baseline')
  assert.equal(output.previousSnapshot, null)
  assert.equal(output.previous, null)
  assert.deepEqual(output.changes, [])
  assert.equal(output.artifact, null)
  assert.deepEqual(output.current, output.currentSnapshot)
  assert.equal(Object.hasOwn(output.currentSnapshot, 'generatedAt'), false)
  assert.equal(output.currentSnapshot.methodologyVersion, '4.1.1')
})

test('a changed collection returns the exact daily attachment contract', async () => {
  const output = await collectArtificialAnalysis({
    fetchImpl: async () => htmlResponse(fixture),
    now: '2026-08-17T23:30:00+08:00',
    previousSnapshot: previousSnapshot(),
  })

  assert.equal(output.status, 'changed')
  assert.equal(output.reportDate, '2026-08-17')
  assert.equal(output.changes.length, 5)
  assert.equal(
    output.artifact.path,
    'content/artifacts/artificial-analysis-20260817-233000.json',
  )
  assert.equal(output.artifact.document.date, '2026-08-17')
  assert.equal(output.artifact.document.attachTo, '2026-08-17')
  assert.equal(output.artifact.document.label, 'Artificial Analysis 排名变化')
  assert.equal(output.artifact.document.generatedAt, '2026-08-17T23:30:00+08:00')
  assert.equal(
    output.artifact.document.sections[0].title,
    '📊 Artificial Analysis 模型排名',
  )
  assert.equal(output.artifact.document.sections[0].items.length, 5)
  assert.match(output.artifact.document.sections[0].items[0].headline, /Jade Kappa/)
  assert.deepEqual(output.artifact.document.sections[0].items[0].sources, [
    {
      name: 'Artificial Analysis（单一来源）',
      url: 'https://artificialanalysis.ai/leaderboards/models/',
    },
  ])

  const oneSecondLater = await collectArtificialAnalysis({
    fetchImpl: async () => htmlResponse(fixture),
    now: '2026-08-17T23:30:01+08:00',
    previousSnapshot: previousSnapshot(),
  })
  assert.equal(
    oneSecondLater.artifact.path,
    'content/artifacts/artificial-analysis-20260817-233001.json',
  )
  assert.notEqual(oneSecondLater.artifact.path, output.artifact.path)
})

test('an unchanged prior snapshot produces no changes and no attachment', async () => {
  const current = parseArtificialAnalysisLeaderboard(fixture)
  current.methodologyVersion = '4.1.1'
  const output = await collectArtificialAnalysis({
    fetchImpl: async () => htmlResponse(fixture),
    now: '2026-08-17T09:00:00+08:00',
    previousSnapshot: current,
  })

  assert.equal(output.status, 'unchanged')
  assert.deepEqual(output.changes, [])
  assert.equal(output.artifact, null)
  assert.deepEqual(output.previousSnapshot, output.currentSnapshot)
})

test('metadata-only changes render an official leaderboard attachment item', async () => {
  const previous = parseArtificialAnalysisLeaderboard(fixture)
  previous.methodologyVersion = '4.1.1'
  previous.models[0].name = 'Atlas Alpha Preview'
  previous.models[0].creator = 'Atlas Preview Lab'
  previous.models[0].estimated = false
  const output = await collectArtificialAnalysis({
    fetchImpl: async () => htmlResponse(fixture),
    now: '2026-08-17T09:00:00+08:00',
    previousSnapshot: previous,
  })

  assert.equal(output.status, 'changed')
  assert.equal(output.changes.length, 1)
  assert.equal(output.changes[0].type, 'metadata_changed')
  assert.deepEqual(output.changes[0].changedFields, [
    'name',
    'creator',
    'estimated',
  ])
  const item = output.artifact.document.sections[0].items[0]
  assert.match(item.headline, /榜单信息更新：Atlas Alpha/)
  assert.match(item.summary, /名称.*开发者.*分数标记/)
  assert.deepEqual(item.sources, [
    {
      name: 'Artificial Analysis（单一来源）',
      url: 'https://artificialanalysis.ai/leaderboards/models/',
    },
  ])
})

test('methodology version changes are first-class changes and render an attachment item', async () => {
  const previous = parseArtificialAnalysisLeaderboard(fixture)
  previous.methodologyVersion = '4.1.0'
  const output = await collectArtificialAnalysis({
    fetchImpl: async () => htmlResponse(fixture),
    now: '2026-08-17T09:00:00+08:00',
    previousSnapshot: previous,
  })

  assert.deepEqual(output.changes[0], {
    type: 'methodology_changed',
    previousVersion: '4.1.0',
    currentVersion: '4.1.1',
  })
  assert.equal(output.changes.length, 1)
  assert.match(output.artifact.document.sections[0].items[0].headline, /v4\.1\.0 → v4\.1\.1/)
  assert.equal(
    output.artifact.document.sections[0].items[0].sources[0].url,
    'https://artificialanalysis.ai/methodology/intelligence-benchmarking',
  )

  previous.methodologyVersion = null
  const legacyChanges = compareSnapshots(previous, output.currentSnapshot)
  assert.deepEqual(legacyChanges, [
    {
      type: 'methodology_changed',
      previousVersion: null,
      currentVersion: '4.1.1',
    },
  ])
})

test('fetch and parse failures reject collection', async () => {
  await assert.rejects(
    collectArtificialAnalysis({
      fetchImpl: async () => htmlResponse('unavailable', {
        status: 503,
        statusText: 'Service Unavailable',
      }),
    }),
    /HTTP 503 Service Unavailable/,
  )
  await assert.rejects(
    collectArtificialAnalysis({
      fetchImpl: async () => htmlResponse('<html>challenge page</html>'),
    }),
    ArtificialAnalysisParseError,
  )
  await assert.rejects(
    collectArtificialAnalysis({
      fetchImpl: async (url) =>
        url.includes('/methodology/')
          ? htmlResponse('<html>methodology challenge</html>')
          : htmlResponse(fixture),
    }),
    /methodology version/,
  )
})

test('snapshot files accept standalone/currentSnapshot forms and missing means baseline', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'artificial-analysis-test-'))
  try {
    const missing = path.join(directory, 'missing.json')
    assert.equal(await loadPreviousSnapshot(missing), null)

    const wrapped = path.join(directory, 'wrapped.json')
    const snapshot = parseArtificialAnalysisLeaderboard(fixture)
    await writeFile(wrapped, JSON.stringify({ currentSnapshot: snapshot }), 'utf8')
    assert.deepEqual(await loadPreviousSnapshot(wrapped), snapshot)

    const corrupt = path.join(directory, 'corrupt.json')
    await writeFile(corrupt, '{not json', 'utf8')
    await assert.rejects(loadPreviousSnapshot(corrupt), /is not valid JSON/)
  } finally {
    await rm(directory, { recursive: true, force: true })
  }
})

test('the committed leaderboard baseline stays in the canonical snapshot shape', async () => {
  const serialized = await readFile(
    new URL('../content/artificial-analysis-snapshot.json', import.meta.url),
    'utf8',
  )
  const snapshot = JSON.parse(serialized)
  assert.deepEqual(normalizeSnapshot(snapshot), snapshot)
})

test('writeArtifactFile materializes the structured document in a chosen directory', async () => {
  const output = await collectArtificialAnalysis({
    fetchImpl: async () => htmlResponse(fixture),
    now: '2026-08-17T09:00:00+08:00',
    previousSnapshot: previousSnapshot(),
  })
  const directory = await mkdtemp(path.join(tmpdir(), 'artificial-analysis-artifact-'))
  try {
    const written = await writeArtifactFile(output.artifact, directory)
    assert.equal(path.basename(written), 'artificial-analysis-20260817-090000.json')
    assert.deepEqual(JSON.parse(await readFile(written, 'utf8')), output.artifact.document)
  } finally {
    await rm(directory, { recursive: true, force: true })
  }
})

test('CLI options are deterministic and fatal argument errors exit non-zero', () => {
  assert.deepEqual(
    parseCliArgs([
      '--now=2026-08-17T09:00:00+08:00',
      '--snapshot',
      'content/artificial-analysis-snapshot.json',
      '--write-artifact',
    ]),
    {
      now: '2026-08-17T09:00:00+08:00',
      snapshot: 'content/artificial-analysis-snapshot.json',
      writeArtifact: true,
      help: false,
    },
  )
  assert.equal(
    parseCliArgs(['--write-artifact', '/tmp/output']).writeArtifact,
    '/tmp/output',
  )
  assert.throws(() => parseCliArgs(['--snapshot']), /requires a value/)

  const child = spawnSync(process.execPath, [scriptPath, '--now', 'not-a-date'], {
    encoding: 'utf8',
  })
  assert.equal(child.status, 1)
  assert.match(child.stderr, /failed to collect Artificial Analysis leaderboard/)
})
