#!/usr/bin/env node

/** Fetch the public Follow Builders feeds used by the daily-report agent.
 *
 * This keeps the cloud runner independent from a user-level Claude/Kimi skill
 * installation. The feeds are curated upstream and require no API key.
 */

const FEEDS = {
  x: 'https://raw.githubusercontent.com/zarazhangrui/follow-builders/main/feed-x.json',
  podcasts:
    'https://raw.githubusercontent.com/zarazhangrui/follow-builders/main/feed-podcasts.json',
  blogs: 'https://raw.githubusercontent.com/zarazhangrui/follow-builders/main/feed-blogs.json',
}

async function fetchJson(name, url) {
  const response = await fetch(url, {
    headers: { 'user-agent': 'ai-daily-report/1.0' },
    signal: AbortSignal.timeout(30_000),
  })
  if (!response.ok) throw new Error(`${name}: HTTP ${response.status}`)
  return response.json()
}

try {
  const entries = await Promise.all(
    Object.entries(FEEDS).map(async ([name, url]) => [name, await fetchJson(name, url)]),
  )
  const feeds = Object.fromEntries(entries)
  const output = {
    generatedAt: new Date().toISOString(),
    feedGeneratedAt:
      feeds.x?.generatedAt ?? feeds.podcasts?.generatedAt ?? feeds.blogs?.generatedAt ?? null,
    x: feeds.x?.x ?? [],
    podcasts: feeds.podcasts?.podcasts ?? [],
    blogs: feeds.blogs?.blogs ?? [],
    upstreamErrors: [
      ...(feeds.x?.errors ?? []),
      ...(feeds.podcasts?.errors ?? []),
      ...(feeds.blogs?.errors ?? []),
    ],
  }
  process.stdout.write(`${JSON.stringify(output, null, 2)}\n`)
} catch (error) {
  console.error(`failed to fetch builder feeds: ${error.message}`)
  process.exitCode = 1
}
