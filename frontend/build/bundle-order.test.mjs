import assert from "node:assert/strict"
import test from "node:test"
import { orderBundles, selectOrderedBundles } from "../src/lib/bundle-order.ts"

const bundles = Object.freeze([
  { id: "b", name: "Alpha", years: [115], fileCount: 2, examClass: "升學測驗", examSubclass: "學測" },
  { id: "a", name: "Omega", years: [115, 114], fileCount: 5, examClass: "升學測驗", examSubclass: "分科" },
  { id: "c", name: "Alpha", years: [115], fileCount: 2, examClass: "教師考試", examSubclass: "教師資格" },
])

test("changing sort preserves ties and leaves the original feed order intact", () => {
  const ids = (rows) => rows.map((row) => row.id)
  assert.deepEqual(ids(orderBundles(bundles, "name")), ["b", "c", "a"])
  assert.deepEqual(ids(orderBundles(bundles, "files-desc")), ["a", "b", "c"])
  assert.deepEqual(ids(orderBundles(bundles, "years-desc")), ["a", "b", "c"])
  assert.deepEqual(ids(bundles), ["b", "a", "c"])
})

test("query candidates and category filters select from the cached order by identity", () => {
  const ordered = orderBundles(bundles, "name")
  const candidates = [{ ...bundles[1] }, { ...bundles[0] }]
  assert.deepEqual(selectOrderedBundles(ordered, candidates, null, null).map((b) => b.id), ["b", "a"])
  assert.deepEqual(selectOrderedBundles(ordered, candidates, "升學測驗", "分科").map((b) => b.id), ["a"])
  assert.deepEqual(selectOrderedBundles(ordered, candidates, "教師考試", null), [])
  assert.deepEqual(selectOrderedBundles(ordered, [], null, null), [])
  assert.deepEqual(ordered.map((b) => b.id), ["b", "c", "a"])
})
