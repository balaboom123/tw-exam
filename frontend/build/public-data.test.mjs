import assert from "node:assert/strict"
import test from "node:test"

import { buildPublicData } from "./public-data.mjs"
import { formatSyncDate } from "../src/lib/provenance.ts"

test("public feed keeps download metadata and defers aliases to the search index", () => {
  const source = {
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
  assert.throws(() => buildPublicData({ bundles: [{ ...bundle, sources: [{ name: "Official", url: "javascript:alert(1)" }] }] }), /Invalid provenance/)
  assert.throws(() => buildPublicData({ bundles: [{ ...bundle, updated: "2026-09-26" }] }), /Invalid sync timestamp/)
  assert.equal(formatSyncDate("2026-09-25T20:00:00Z"), "2026/09/26")
})
