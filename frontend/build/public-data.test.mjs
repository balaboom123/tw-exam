import assert from "node:assert/strict"
import test from "node:test"

import { buildPublicData } from "./public-data.mjs"

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
  })
  assert.equal(searchIndex.length, feed.bundles.length)
  assert.match(searchIndex[0], /歷屆試題/)
  assert.equal("searchAliases" in feed.bundles[0], false)
})
