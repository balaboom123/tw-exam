import assert from "node:assert/strict"
import test from "node:test"
import { buildSearchQuery, readSearchState } from "../src/lib/search-state.ts"

test("search state round-trips filters into a shareable query", () => {
  const state = readSearchState("?q=%E8%AD%B7%E7%90%86%E5%B8%AB&year=114&class=%E5%B0%88%E6%8A%80%E4%BA%BA%E5%93%A1%E8%80%83%E8%A9%A6&subclass=%E9%86%AB%E4%BA%8B%2F%E5%81%A5%E5%BA%B7&sort=files-desc&page=3")

  assert.deepEqual(state, {
    query: "護理師",
    year: 114,
    examClass: "專技人員考試",
    subclass: "醫事/健康",
    sort: "files-desc",
    page: 3,
  })
  assert.deepEqual(
    Object.fromEntries(new URLSearchParams(buildSearchQuery(state))),
    Object.fromEntries(
      new URLSearchParams(
        "q=%E8%AD%B7%E7%90%86%E5%B8%AB&year=114&class=%E5%B0%88%E6%8A%80%E4%BA%BA%E5%93%A1%E8%80%83%E8%A9%A6&subclass=%E9%86%AB%E4%BA%8B%2F%E5%81%A5%E5%BA%B7&sort=files-desc&page=3",
      ),
    ),
  )
})

test("invalid URL values fall back to safe defaults", () => {
  assert.deepEqual(readSearchState("?year=nope&page=0&sort=unknown"), {
    query: "",
    year: null,
    examClass: null,
    subclass: null,
    sort: "name",
    page: 1,
  })
})
