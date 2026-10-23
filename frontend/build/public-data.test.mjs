import assert from "node:assert/strict"
import test from "node:test"

import { buildPublicData } from "./public-data.ts"
import { formatSyncDate } from "../src/lib/provenance.ts"

test("compatibility pages use explicit published identities and stay out of search results", () => {
  const source = {
    schema_version: 2,
    bundles: [{ id: "current", name: "目前", years: [115], fileCount: 1,
      examClass: "證照／檢定", examSubclass: "技能檢定",
      url: "https://github.com/example/exams/releases/download/shard-1/current.zip" }],
    compatibilityPages: [{ id: "old", name: "舊版", successorIds: ["current"] }],
  }
  const data = buildPublicData(source)
  assert.deepEqual(data.compatibilityPages, source.compatibilityPages)
  assert.equal(data.feed.bundles.length, 1)
  assert.equal(data.searchIndex.length, 1)
  for (const page of [
    { id: "../old", name: "舊版", successorIds: ["current"] },
    { id: "old", name: "舊版", successorIds: ["missing"] },
    { id: "current", name: "舊版", successorIds: ["current"] },
    { id: "old", name: "舊版", successorIds: ["current", "current"] },
  ]) assert.throws(() => buildPublicData({ ...source, compatibilityPages: [page] }))
})

test("public feed keeps download metadata and defers aliases to the search index", () => {
  const source = {
    schema_version: 2,
    bundles: [{
      id: "example",
      name: "分科測驗",
      years: [115, 114],
      fileCount: 3,
      examClass: "升學測驗",
      examSubclass: "分科測驗",
      url: "https://github.com/example/exams/releases/download/shard-1/example.zip",
      searchAliases: ["數學甲", "歷屆試題"],
      subjectLabels: ["數學甲"],
      domainId: "admissions",
      sources: [{ name: "大學入學考試中心", url: "https://www.ceec.edu.tw/" }],
      updated: "2026-09-26T01:00:00Z",
    }],
  }
  const { feed, searchIndex } = buildPublicData(source)

  assert.equal(feed.repo, "example/exams")
  assert.deepEqual(feed.classes, ["升學測驗"])
  assert.deepEqual(feed.bundles[0], {
    id: "example",
    name: "分科測驗",
    years: [115, 114],
    fileCount: 3,
    examClass: "升學測驗",
    examSubclass: "分科測驗",
    tag: "shard-1",
    asset: "example.zip",
    subjectLabels: ["數學甲"],
    sources: [{ name: "大學入學考試中心", url: "https://www.ceec.edu.tw/" }],
    updated: "2026-09-26T01:00:00Z",
  })
  assert.equal(searchIndex.length, feed.bundles.length)
  assert.match(searchIndex[0], /歷屆試題/)
  assert.equal("searchAliases" in feed.bundles[0], false)
})

test("provenance rejects unsafe source links and dates without a timezone", () => {
  const bundle = {
    id: "example", name: "測驗", years: [115], fileCount: 1,
    examClass: "升學測驗", examSubclass: "測驗",
    url: "https://github.com/example/exams/releases/download/shard-1/example.zip",
  }
  assert.throws(() => buildPublicData({ schema_version: 2, bundles: [{ ...bundle, sources: [{ name: "Official", url: "javascript:alert(1)" }] }] }), /Invalid provenance/)
  assert.throws(() => buildPublicData({ schema_version: 2, bundles: [{ ...bundle, updated: "2026-09-26" }] }), /Invalid sync timestamp/)
  assert.equal(formatSyncDate("2026-09-25T20:00:00Z"), "2026/09/26")
})

test("public projection rejects years that cannot satisfy the browser contract", () => {
  const bundle = {
    id: "example", name: "測驗", years: [115], fileCount: 1,
    examClass: "升學測驗", examSubclass: "測驗",
    url: "https://github.com/example/exams/releases/download/shard-1/example.zip",
  }
  for (const years of [["115"], [115.5], [null]]) {
    assert.throws(() => buildPublicData({ schema_version: 2, bundles: [{ ...bundle, years }] }), /Invalid frontend bundle/)
  }
})
