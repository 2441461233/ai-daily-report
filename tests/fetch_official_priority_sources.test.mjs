import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'

import {
  SOURCE_DEFINITIONS,
  SourceParseError,
  classifyAnnouncement,
  collectOfficialPrioritySources,
  isWithinWindow,
  parseCliArgs,
  parseSpaceXaiNews,
  parseHackerNewsSpaceXaiSignals,
  parseSpaceXaiArticle,
  parseSpaceXaiReleaseNotes,
} from '../scripts/fetch_official_priority_sources.mjs'

const newsFixture = await readFile(
  new URL('./fixtures/spacexai-news.html', import.meta.url),
  'utf8',
)
const releaseNotesFixture = await readFile(
  new URL('./fixtures/spacexai-release-notes.html', import.meta.url),
  'utf8',
)
const articleFixture = await readFile(
  new URL('./fixtures/spacexai-article.html', import.meta.url),
  'utf8',
)

function htmlResponse(html, init = {}) {
  return new Response(html, {
    status: 200,
    headers: { 'content-type': 'text/html; charset=utf-8' },
    ...init,
  })
}

async function successfulOfficialFetch(url) {
  if (url === 'https://x.ai/news') return htmlResponse(newsFixture)
  if (url === 'https://x.ai/news/grok-4-6') return htmlResponse(articleFixture)
  if (url.startsWith('https://hn.algolia.com/')) return htmlResponse('{"hits":[]}')
  return htmlResponse(releaseNotesFixture)
}

test('SpaceXAI news parser extracts item-specific metadata from HTML and JSON-LD', () => {
  const parsed = parseSpaceXaiNews(newsFixture)
  const grok = parsed.items.find((item) => item.url.endsWith('/grok-4-6'))

  assert.deepEqual(parsed.errors, [])
  assert.equal(parsed.items.length, 3)
  assert.equal(grok.title, 'Introducing Grok 4.6')
  assert.equal(grok.url, 'https://x.ai/news/grok-4-6')
  assert.equal(grok.publishedAt, '2026-08-12T00:00:00.000Z')
  assert.equal(grok.precision, 'day')
  assert.match(grok.summary, /long-running agents/)
  assert.deepEqual(grok.evidenceUrls, ['https://x.ai/news/grok-4-6'])
})

test('direct article parser requires matching first-party canonical metadata', () => {
  const parsed = parseSpaceXaiArticle(articleFixture, 'https://x.ai/news/grok-4-6')
  assert.equal(parsed.title, 'Introducing Grok 4.6')
  assert.equal(parsed.publishedAt, '2026-08-12T00:00:00.000Z')
  assert.equal(parsed.url, 'https://x.ai/news/grok-4-6')

  assert.throws(
    () => parseSpaceXaiArticle(articleFixture, 'https://x.ai/news/grok-9-9'),
    /canonical URL/,
  )
})

test('SpaceXAI release notes parser supplies official fallback evidence and details', () => {
  const parsed = parseSpaceXaiReleaseNotes(releaseNotesFixture)

  assert.deepEqual(parsed.errors, [])
  assert.equal(parsed.items.length, 1)
  assert.equal(parsed.items[0].title, 'Grok 4.6')
  assert.equal(parsed.items[0].url, 'https://x.ai/news/grok-4-6')
  assert.equal(parsed.items[0].publishedAt, '2026-08-12T00:00:00.000Z')
  assert.equal(parsed.items[0].precision, 'day')
  assert.match(parsed.items[0].details, /500k context window/)
  assert.deepEqual(parsed.items[0].evidenceUrls, [
    'https://x.ai/news/grok-4-6',
    'https://docs.x.ai/developers/grok-4-6',
    'https://docs.x.ai/developers/release-notes',
  ])
})

test('release notes retain a dated flagship release before its detail link exists', () => {
  const parsed = parseSpaceXaiReleaseNotes(`
    <main>
      <h2>August 2026</h2>
      <div>August 13</div>
      <h3>Grok 4.7</h3>
      <p>Grok 4.7, SpaceXAI's frontier model, is now available on the xAI API.</p>
      <p>Last updated: August 13, 2026</p>
    </main>
  `)

  assert.deepEqual(parsed.errors, [])
  assert.equal(parsed.items.length, 1)
  assert.equal(parsed.items[0].url, 'https://docs.x.ai/developers/release-notes')
  assert.equal(parsed.items[0].stableSlug, 'grok-4-7')
})

test('HN signal only promotes item-specific x.ai news URLs', () => {
  const parsed = parseHackerNewsSpaceXaiSignals(
    JSON.stringify({
      hits: [
        {
          title: 'Grok 4.6',
          url: 'https://x.ai/news/grok-4-6',
          created_at: '2026-08-12T15:32:50Z',
        },
        {
          title: 'Grok 4.6 analysis',
          url: 'https://example.org/grok-4-6',
          created_at: '2026-08-12T15:40:00Z',
        },
        {
          title: 'Why Grok 4.6 is controversial',
          url: 'https://x.ai/news/grok-4-6',
          created_at: '2026-08-12T15:41:00Z',
        },
        {
          title: 'Grok 9.9',
          url: 'https://x.ai:444/news/grok-9-9',
          created_at: '2026-08-12T15:42:00Z',
        },
      ],
    }),
  )
  assert.equal(parsed.items.length, 1)
  assert.equal(parsed.items[0].url, 'https://x.ai/news/grok-4-6')
  assert.equal(parsed.items[0].publishedAt, '2026-08-12T15:32:50.000Z')
})

test('Grok 4.6 has the fixed required candidate schema and stable id', async () => {
  const output = await collectOfficialPrioritySources({
    fetchImpl: successfulOfficialFetch,
    now: new Date('2026-08-13T02:45:00.000Z'),
  })

  assert.equal(output.schemaVersion, 1)
  assert.equal(output.generatedAt, '2026-08-13T02:45:00.000Z')
  assert.equal(output.windowHours, 72)
  assert.equal(output.sources[0].status, 'ok')
  assert.equal(output.sources[0].coverageSufficient, true)
  assert.equal(output.sources[0].candidateCount, 1)
  assert.deepEqual(output.errors, [])
  assert.equal(output.candidates.length, 1)

  const candidate = output.candidates[0]
  assert.equal(candidate.id, 'spacexai:grok-4-6')
  assert.equal(candidate.title, 'Introducing Grok 4.6')
  assert.equal(candidate.url, 'https://x.ai/news/grok-4-6')
  assert.equal(candidate.publishedAt, '2026-08-12T00:00:00.000Z')
  assert.equal(candidate.precision, 'day')
  assert.equal(candidate.category, 'major_model_release')
  assert.equal(candidate.required, true)
  assert.equal(candidate.officialSource, 'SpaceXAI')
  assert.deepEqual(candidate.matchTerms, ['Grok', '4.6'])
  assert.deepEqual(candidate.evidenceUrls, [
    'https://x.ai/news/grok-4-6',
    'https://docs.x.ai/developers/grok-4-6',
    'https://docs.x.ai/developers/release-notes',
  ])
  assert.match(candidate.summary, /long-running agents/)
  assert.match(candidate.details, /500k context window/)
})

test('the default 72-hour window is configurable and never includes future items', () => {
  const now = new Date('2026-08-13T02:45:00.000Z')
  assert.equal(parseCliArgs([]).windowHours, 72)
  assert.equal(isWithinWindow('2026-08-12T00:00:00.000Z', now, 72, 'day'), true)
  assert.equal(isWithinWindow('2026-08-12T00:00:00.000Z', now, 24, 'day'), true)
  assert.equal(isWithinWindow('2026-08-12T00:00:00.000Z', now, 24, 'instant'), false)
  assert.equal(isWithinWindow('2026-08-14T00:00:00.000Z', now, 72, 'day'), false)
})

test('midnight-only official article metadata is treated as day precision', () => {
  const parsed = parseSpaceXaiArticle(articleFixture, 'https://x.ai/news/grok-4-6')
  assert.equal(parsed.precision, 'day')
  assert.equal(
    isWithinWindow(parsed.publishedAt, new Date('2026-08-15T02:45:00Z'), 72, parsed.precision),
    true,
  )
})

test('ordinary product announcements never enter the required candidate list', async () => {
  const output = await collectOfficialPrioritySources({
    fetchImpl: successfulOfficialFetch,
    now: new Date('2026-08-13T02:45:00.000Z'),
    windowHours: 150,
  })
  assert.deepEqual(output.candidates.map((candidate) => candidate.id), [
    'spacexai:grok-4-6',
  ])
})

test('release notes keep the source group usable when the news index is 403', async () => {
  const output = await collectOfficialPrioritySources({
    fetchImpl: async (url) =>
      url === 'https://x.ai/news'
        ? htmlResponse('forbidden', { status: 403, statusText: 'Forbidden' })
        : url.startsWith('https://hn.algolia.com/')
          ? htmlResponse('{"hits":[]}')
          : htmlResponse(releaseNotesFixture),
    now: new Date('2026-08-13T02:45:00.000Z'),
  })

  assert.equal(output.sources[0].status, 'partial')
  assert.equal(output.sources[0].coverageSufficient, true)
  assert.equal(output.sources[0].endpoints[0].status, 'error')
  assert.equal(output.sources[0].endpoints[1].status, 'ok')
  assert.equal(output.candidates[0].id, 'spacexai:grok-4-6')
  assert.match(output.candidates[0].details, /frontier model/)
  assert.deepEqual(output.errors, [
    {
      source: 'spacexai',
      endpoint: 'news',
      url: 'https://x.ai/news',
      stage: 'fetch',
      message: 'HTTP 403 Forbidden',
    },
  ])
})

test('a linkless first-party release-note entry still becomes required', async () => {
  const linklessNotes = `
    <main>
      <h2>August 2026</h2>
      <div>August 13</div>
      <h3>Grok 4.7</h3>
      <p>Grok 4.7, SpaceXAI's frontier model, is now available on the xAI API.</p>
      <p>Last updated: August 13, 2026</p>
    </main>
  `
  const output = await collectOfficialPrioritySources({
    fetchImpl: async (url) =>
      url === 'https://x.ai/news'
        ? htmlResponse('forbidden', { status: 403, statusText: 'Forbidden' })
        : url.startsWith('https://hn.algolia.com/')
          ? htmlResponse('{"hits":[]}')
          : htmlResponse(linklessNotes),
    now: new Date('2026-08-13T02:45:00.000Z'),
  })

  assert.equal(output.sources[0].coverageSufficient, true)
  assert.equal(output.candidates[0].id, 'spacexai:grok-4-7')
  assert.equal(output.candidates[0].url, 'https://docs.x.ai/developers/release-notes')
  assert.deepEqual(output.candidates[0].matchTerms, ['Grok', '4.7'])
})

test('a healthy fallback can prove a quiet window despite a blocked index', async () => {
  const output = await collectOfficialPrioritySources({
    fetchImpl: async (url) =>
      url === 'https://x.ai/news'
        ? htmlResponse('forbidden', { status: 403, statusText: 'Forbidden' })
        : url.startsWith('https://hn.algolia.com/')
          ? htmlResponse('{"hits":[]}')
          : htmlResponse(releaseNotesFixture),
    now: new Date('2026-08-20T02:45:00.000Z'),
  })

  assert.equal(output.sources[0].status, 'partial')
  assert.equal(output.sources[0].coverageSufficient, true)
  assert.equal(output.sources[0].candidateCount, 0)
  assert.deepEqual(output.candidates, [])
})

test('a healthy but lagging fallback cannot hide an unresolved exact HN signal', async () => {
  const output = await collectOfficialPrioritySources({
    fetchImpl: async (url) => {
      if (url === 'https://x.ai/news') {
        return htmlResponse('forbidden', { status: 403, statusText: 'Forbidden' })
      }
      if (url.startsWith('https://hn.algolia.com/')) {
        return htmlResponse(
          JSON.stringify({
            hits: [
              {
                title: 'Grok 9.9',
                url: 'https://x.ai/news/grok-9-9',
                created_at: '2026-08-20T01:30:00Z',
              },
            ],
          }),
        )
      }
      if (url === 'https://x.ai/news/grok-9-9') {
        return htmlResponse('unavailable', { status: 503 })
      }
      return htmlResponse(releaseNotesFixture)
    },
    now: new Date('2026-08-20T02:45:00.000Z'),
  })

  assert.deepEqual(output.candidates, [])
  assert.equal(output.sources[0].coverageSufficient, false)
  assert.deepEqual(output.sources[0].unresolvedSignals, ['spacexai:grok-9-9'])
})

test('a conclusive official 404 rejects a fake HN signal without poisoning coverage', async () => {
  const output = await collectOfficialPrioritySources({
    fetchImpl: async (url) => {
      if (url === 'https://x.ai/news') {
        return htmlResponse('forbidden', { status: 403, statusText: 'Forbidden' })
      }
      if (url.startsWith('https://hn.algolia.com/')) {
        return htmlResponse(
          JSON.stringify({
            hits: [
              {
                title: 'Grok 9.9',
                url: 'https://x.ai/news/grok-9-9',
                created_at: '2026-08-20T01:30:00Z',
              },
            ],
          }),
        )
      }
      if (url === 'https://x.ai/news/grok-9-9') {
        return htmlResponse('not found', { status: 404, statusText: 'Not Found' })
      }
      return htmlResponse(releaseNotesFixture)
    },
    now: new Date('2026-08-20T02:45:00.000Z'),
  })

  assert.deepEqual(output.candidates, [])
  assert.equal(output.sources[0].coverageSufficient, true)
  assert.deepEqual(output.sources[0].unresolvedSignals, [])
})

test('a newer fake URL cannot hide an earlier real URL for the same model', async () => {
  const output = await collectOfficialPrioritySources({
    fetchImpl: async (url) => {
      if (url === 'https://x.ai/news') {
        return htmlResponse('forbidden', { status: 403, statusText: 'Forbidden' })
      }
      if (url.startsWith('https://hn.algolia.com/')) {
        return htmlResponse(
          JSON.stringify({
            hits: [
              {
                title: 'Grok 4.6',
                url: 'https://x.ai/news/grok-4-6-fake',
                created_at: '2026-08-12T16:00:00Z',
              },
              {
                title: 'Grok 4.6',
                url: 'https://x.ai/news/grok-4-6',
                created_at: '2026-08-12T15:32:50Z',
              },
            ],
          }),
        )
      }
      if (url === 'https://x.ai/news/grok-4-6-fake') {
        return htmlResponse('not found', { status: 404, statusText: 'Not Found' })
      }
      if (url === 'https://x.ai/news/grok-4-6') return htmlResponse(articleFixture)
      return htmlResponse(releaseNotesFixture)
    },
    now: new Date('2026-08-13T02:45:00.000Z'),
  })

  assert.equal(output.sources[0].coverageSufficient, true)
  assert.deepEqual(output.sources[0].unresolvedSignals, [])
  assert.deepEqual(output.candidates.map((candidate) => candidate.id), [
    'spacexai:grok-4-6',
  ])
})

test('a model mismatch on a real official article conclusively rejects the HN signal', async () => {
  const output = await collectOfficialPrioritySources({
    fetchImpl: async (url) => {
      if (url === 'https://x.ai/news') {
        return htmlResponse('forbidden', { status: 403, statusText: 'Forbidden' })
      }
      if (url.startsWith('https://hn.algolia.com/')) {
        return htmlResponse(
          JSON.stringify({
            hits: [
              {
                title: 'Grok 9.9',
                url: 'https://x.ai/news/grok-4-6',
                created_at: '2026-08-13T01:30:00Z',
              },
            ],
          }),
        )
      }
      if (url === 'https://x.ai/news/grok-4-6') return htmlResponse(articleFixture)
      return htmlResponse(releaseNotesFixture)
    },
    now: new Date('2026-08-13T02:45:00.000Z'),
  })

  assert.equal(output.sources[0].coverageSufficient, true)
  assert.deepEqual(output.sources[0].unresolvedSignals, [])
  assert.deepEqual(output.candidates.map((candidate) => candidate.id), [
    'spacexai:grok-4-6',
  ])
})

test('a 200 page with a different official canonical conclusively rejects the HN signal', async () => {
  const output = await collectOfficialPrioritySources({
    fetchImpl: async (url) => {
      if (url === 'https://x.ai/news') {
        return htmlResponse('forbidden', { status: 403, statusText: 'Forbidden' })
      }
      if (url.startsWith('https://hn.algolia.com/')) {
        return htmlResponse(
          JSON.stringify({
            hits: [
              {
                title: 'Grok 9.9',
                url: 'https://x.ai/news/grok-9-9',
                created_at: '2026-08-20T01:30:00Z',
              },
            ],
          }),
        )
      }
      if (url === 'https://x.ai/news/grok-9-9') return htmlResponse(articleFixture)
      return htmlResponse(releaseNotesFixture)
    },
    now: new Date('2026-08-20T02:45:00.000Z'),
  })

  assert.deepEqual(output.candidates, [])
  assert.equal(output.sources[0].coverageSufficient, true)
  assert.deepEqual(output.sources[0].unresolvedSignals, [])
})

test('HN signal retains a required candidate when both first-party indexes fail', async () => {
  const output = await collectOfficialPrioritySources({
    fetchImpl: async (url) => {
      if (url.startsWith('https://hn.algolia.com/')) {
        return htmlResponse(
          JSON.stringify({
            hits: [
              {
                title: 'Grok 4.6',
                url: 'https://x.ai/news/grok-4-6',
                created_at: '2026-08-12T15:32:50Z',
              },
            ],
          }),
        )
      }
      if (url === 'https://x.ai/news/grok-4-6') return htmlResponse(articleFixture)
      return htmlResponse('unavailable', { status: 503 })
    },
    now: new Date('2026-08-13T02:45:00.000Z'),
  })

  assert.equal(output.sources[0].coverageSufficient, true)
  assert.equal(output.sources[0].status, 'partial')
  assert.equal(output.candidates[0].id, 'spacexai:grok-4-6')
  assert.equal(output.candidates[0].required, true)
  assert.deepEqual(output.candidates[0].evidenceUrls, [
    'https://x.ai/news/grok-4-6',
  ])
})

test('an unverified HN submission can never become a required candidate', async () => {
  const output = await collectOfficialPrioritySources({
    fetchImpl: async (url) => {
      if (url.startsWith('https://hn.algolia.com/')) {
        return htmlResponse(
          JSON.stringify({
            hits: [
              {
                title: 'Grok 9.9',
                url: 'https://x.ai/news/grok-9-9',
                created_at: '2026-08-12T15:32:50Z',
              },
            ],
          }),
        )
      }
      return htmlResponse('unavailable', { status: 503 })
    },
    now: new Date('2026-08-13T02:45:00.000Z'),
  })

  assert.deepEqual(output.candidates, [])
  assert.equal(output.sources[0].coverageSufficient, false)
  assert.ok(output.errors.some((error) => error.stage === 'verify'))
})

test('parser warnings cannot falsely certify a quiet critical window', async () => {
  const source = {
    id: 'warning-lab',
    name: 'Warning Lab',
    officialSource: 'Warning Lab',
    critical: true,
    endpoints: [
      {
        id: 'news',
        name: 'Warning Lab News',
        url: 'https://warning.test/news',
        parser: () => ({
          items: [],
          errors: ['recent entry was missing publishedAt'],
        }),
      },
    ],
  }
  const output = await collectOfficialPrioritySources({
    sources: [source],
    fetchImpl: async () => htmlResponse('<html></html>'),
    now: new Date('2026-08-13T02:45:00.000Z'),
  })

  assert.equal(output.sources[0].status, 'partial')
  assert.equal(output.sources[0].coverageSufficient, false)
  assert.equal(output.sources[0].candidateCount, 0)
})

test('changed release-note heading or launch wording cannot certify a quiet window', async () => {
  const changedMarkup = `
    <main>
      <h2>August 2026</h2>
      <div>August 13</div>
      <h4>Grok 5</h4>
      <p>Now live in API for coding and knowledge work.</p>
      <p>Last updated: August 13, 2026</p>
    </main>
  `
  const output = await collectOfficialPrioritySources({
    fetchImpl: async (url) =>
      url === 'https://x.ai/news'
        ? htmlResponse('forbidden', { status: 403, statusText: 'Forbidden' })
        : url.startsWith('https://hn.algolia.com/')
          ? htmlResponse('{"hits":[]}')
          : htmlResponse(changedMarkup),
    now: new Date('2026-08-13T02:45:00.000Z'),
  })

  assert.deepEqual(output.candidates, [])
  assert.equal(output.sources[0].coverageSufficient, false)
  assert.ok(output.errors.some((error) => /not classified/.test(error.message)))
})

test('unrecognized or blocked HTML fails explicitly instead of looking empty', () => {
  assert.throws(
    () => parseSpaceXaiNews('<html><body>new layout</body></html>'),
    SourceParseError,
  )
  assert.throws(
    () => parseSpaceXaiNews('<title>Attention Required!</title><p>You have been blocked</p>'),
    /Cloudflare block page/,
  )
  assert.throws(
    () => parseSpaceXaiReleaseNotes('<html><body>new layout</body></html>'),
    /no complete major model releases/,
  )
})

test('all endpoint failures make the official source group fail with explicit errors', async () => {
  const output = await collectOfficialPrioritySources({
    fetchImpl: async () => htmlResponse('unavailable', { status: 503 }),
    now: new Date('2026-08-13T02:45:00.000Z'),
  })

  assert.deepEqual(output.candidates, [])
  assert.equal(output.sources[0].status, 'error')
  assert.equal(output.sources[0].coverageSufficient, false)
  assert.equal(output.errors.length, 3)
  assert.ok(output.errors.every((error) => error.stage === 'fetch'))
})

test('future source dates are excluded with an explicit filter error', async () => {
  const source = {
    id: 'future-lab',
    name: 'Future Lab',
    officialSource: 'Future Lab',
    endpoints: [
      {
        id: 'news',
        name: 'Future Lab News',
        url: 'https://future.test/news',
        parser: () => ({
          items: [
            {
              title: 'Introducing GPT 9.1',
              url: 'https://future.test/news/gpt-9-1',
              publishedAt: '2026-08-14T00:00:00.000Z',
              precision: 'day',
              summary: 'Introducing GPT 9.1.',
              details: 'Introducing GPT 9.1.',
              evidenceUrls: ['https://future.test/news/gpt-9-1'],
            },
          ],
          errors: [],
        }),
      },
    ],
  }
  const output = await collectOfficialPrioritySources({
    sources: [source],
    fetchImpl: async () => htmlResponse('<html></html>'),
    now: new Date('2026-08-13T02:45:00.000Z'),
  })

  assert.deepEqual(output.candidates, [])
  assert.equal(output.sources[0].status, 'partial')
  assert.equal(output.errors[0].stage, 'filter')
  assert.match(output.errors[0].message, /future publishedAt/)
})

test('model classification requires an actual flagship-style launch', () => {
  assert.deepEqual(classifyAnnouncement('Introducing Grok 4.6'), {
    category: 'major_model_release',
    required: true,
  })
  assert.deepEqual(
    classifyAnnouncement(
      'Grok 4.6',
      "SpaceXAI's frontier model is now available on the xAI API.",
    ),
    { category: 'major_model_release', required: true },
  )
  assert.deepEqual(classifyAnnouncement('Grok 4.5 in GitHub Copilot'), {
    category: 'official_announcement',
    required: false,
  })
})

test('another lab source group can be added without changing collector logic', async () => {
  const source = {
    id: 'example-lab',
    name: 'Example Lab official releases',
    officialSource: 'Example Lab',
    endpoints: [
      {
        id: 'news',
        name: 'Example Lab News',
        url: 'https://example.test/news',
        parser: () => ({
          items: [
            {
              title: 'Announcing GPT 6.1',
              url: 'https://example.test/news/gpt-6-1',
              publishedAt: '2026-08-13T01:00:00.000Z',
              precision: 'instant',
              summary: 'Announcing GPT 6.1.',
              details: 'Announcing GPT 6.1.',
              evidenceUrls: ['https://example.test/news/gpt-6-1'],
            },
          ],
          errors: [],
        }),
      },
    ],
  }

  const output = await collectOfficialPrioritySources({
    sources: [source],
    fetchImpl: async () => htmlResponse('<html></html>'),
    now: new Date('2026-08-13T02:45:00.000Z'),
  })
  assert.equal(output.sources[0].id, 'example-lab')
  assert.equal(output.candidates[0].id, 'example-lab:gpt-6-1')
  assert.equal(output.candidates[0].officialSource, 'Example Lab')
  assert.deepEqual(output.candidates[0].matchTerms, ['GPT', '6.1'])
})

test('the shipped SpaceXAI group always includes direct news plus official docs fallback', () => {
  assert.deepEqual(
    SOURCE_DEFINITIONS[0].endpoints.map((endpoint) => endpoint.url),
    [
      'https://x.ai/news',
      'https://docs.x.ai/developers/release-notes',
      'https://hn.algolia.com/api/v1/search_by_date?tags=story&query=x.ai&hitsPerPage=1000',
    ],
  )
})
