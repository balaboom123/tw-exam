import assert from "node:assert/strict"
import test from "node:test"
import { buildPublicData } from "./public-data.ts"
import { buildBundlePage } from "./bundle-pages.ts"
import { expandBundle, parseCompactFeed } from "../src/lib/public-feed.ts"
import { isMaterialSummary, materialDateLabels, matchesPublicationYear } from "../src/lib/source-material.ts"
import { buildSearchQuery, readSearchState } from "../src/lib/search-state.ts"

const workbook = { schema_version: 1, kind: "practice_collection", dates: [{ basis: "edition_year", year_ad: 2012 }] }
const sample = { schema_version: 1, kind: "sample", dates: [{ basis: "undated", year_ad: null }] }
const row = {
  id: "workbook", name: "日語練習問題集", years: [101], fileCount: 2,
  url: "https://github.com/example/archive/releases/download/shard-1/workbook.zip",
  examClass: "語言測驗", examSubclass: "日語能力", sourceMaterial: workbook,
}

test("reviewed editions survive compact projection, browser expansion and search", () => {
  const { feed, searchIndex } = buildPublicData({ schema_version: 3, bundles: [row] })
  assert.equal(feed.v, 3)
  const parsed = parseCompactFeed(JSON.parse(JSON.stringify(feed)))
  const expanded = expandBundle(parsed.bundles[0], parsed.repo)
  assert.deepEqual(expanded.sourceMaterial, workbook)
  assert.equal(expanded.url, row.url)
  assert.match(searchIndex[0], /2012/)
  assert.match(searchIndex[0], /練習問題集/)
  assert.deepEqual(materialDateLabels(expanded.sourceMaterial), ["版次年份：2012 年"])
  assert.equal(matchesPublicationYear(expanded, 115), false)
  assert.equal(matchesPublicationYear(expanded, 101), true)
})

test("undated material remains searchable and has a shareable date filter", () => {
  const input = { ...row, id: "sample", name: "範例試題", years: [], sourceMaterial: sample }
  const { feed, searchIndex } = buildPublicData({ schema_version: 3, bundles: [input] })
  const parsed = parseCompactFeed(feed)
  const expanded = expandBundle(parsed.bundles[0], parsed.repo)
  assert.deepEqual(expanded.years, [])
  assert.match(searchIndex[0], /未標示年份/)
  assert.equal(matchesPublicationYear(expanded, "undated"), true)
  assert.equal(matchesPublicationYear(expanded, 115), false)
  const state = readSearchState("?year=undated")
  assert.equal(state.year, "undated")
  assert.equal(buildSearchQuery(state), "year=undated")
  assert.equal(matchesPublicationYear({ years: [] }, "undated"), false)
})

test("public readers reject unsupported, unresolved, or contradictory material facts", () => {
  for (const sourceMaterial of [
    { ...workbook, schema_version: true },
    { ...workbook, kind: "unknown" },
    { ...workbook, dates: [{ basis: "unknown", year_ad: null }] },
    { ...workbook, dates: [{ basis: "undated", year_ad: 2026 }] },
    { ...workbook, dates: [{ basis: "exam_year", year_ad: 2012 }] },
    { ...workbook, dates: [{ basis: "edition_year", year_ad: true }] },
    { ...workbook, dates: [...workbook.dates, ...workbook.dates] },
    { ...workbook, acquisition_year: 2026 },
  ]) {
    assert.equal(isMaterialSummary(sourceMaterial), false)
    assert.throws(() => buildPublicData({ schema_version: 3, bundles: [{ ...row, sourceMaterial }] }), /Invalid material/)
  }
  assert.throws(() => buildPublicData({ schema_version: 2, bundles: [row] }), /Invalid material/)
  assert.throws(() => buildPublicData({ schema_version: 3, bundles: [{ ...row, years: [115] }] }), /Invalid material/)
  const feed = buildPublicData({ schema_version: 3, bundles: [row] }).feed
  assert.throws(() => parseCompactFeed({ ...feed, v: 2 }), /require compact feed v3/)
  assert.throws(() => parseCompactFeed({ ...feed, v: 4 }), /schema mismatch/)
})

test("landing pages and structured metadata describe editions and undated samples", () => {
  for (const [sourceMaterial, years, label] of [[workbook, [101], "版次年份：2012 年"], [sample, [], "未標示年份"]]) {
    const { feed } = buildPublicData({ schema_version: 3, bundles: [{ ...row, sourceMaterial, years }] })
    const html = buildBundlePage(feed.bundles[0], { repo: feed.repo, root: "https://example.test/archive/" })
    assert.match(html, new RegExp(label))
    assert.doesNotMatch(html, /收錄民國|民國 115|日語練習問題集歷屆試題/)
    const json = JSON.parse(html.match(/<script type="application\/ld\+json">(.*?)<\/script>/s)[1])
    assert.match(json[1].description, new RegExp(label))
    assert.equal(json[1].distribution[0].contentUrl, row.url)
  }
})

test("mixed legacy and reviewed rows preserve order and dated history before the ROC calendar", () => {
  const legacy = { ...row, id: "legacy", sourceMaterial: undefined, years: [115] }
  const { feed } = buildPublicData({ schema_version: 3, bundles: [legacy, row] })
  assert.deepEqual(parseCompactFeed(feed).bundles.map((bundle) => bundle.id), ["legacy", "workbook"])
  for (const year of [0, -111]) assert.equal(readSearchState(buildSearchQuery({ ...readSearchState(""), year })).year, year)
})
