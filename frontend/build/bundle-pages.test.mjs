import assert from "node:assert/strict"
import test from "node:test"

import { buildBundlePage, buildSitemap, siteRoot } from "./bundle-pages.ts"

const bundle = {
  id: "exam-one",
  name: "測驗 <甲>",
  years: [115, 114],
  fileCount: 2,
  examClass: "升學測驗",
  examSubclass: "分科測驗",
  tag: "shard-1",
  asset: "exam-one.zip",
  subjectLabels: ["數學甲"],
}

test("bundle page has a canonical URL, safe metadata, ZIP distribution, and a return path", () => {
  const root = siteRoot({ base: "/tw-exam/", origin: "https://example.github.io" })
  const html = buildBundlePage(bundle, { repo: "example/tw-exam", root })
  assert.match(html, /<link rel="canonical" href="https:\/\/example\.github\.io\/tw-exam\/b\/exam-one\.html">/)
  assert.match(html, /測驗 &lt;甲&gt;/)
  assert.doesNotMatch(html, /<title>測驗 <甲>/)
  assert.match(html, /https:\/\/github\.com\/example\/tw-exam\/releases\/download\/shard-1\/exam-one\.zip/)
  assert.match(html, /join\.html\?return=/)
  assert.match(html, /application\/ld\+json/)
  assert.match(html, /href="\.\.\/assets\/bundle-pages\.css"/)
  assert.match(html, /href="\.\.\/join\.html\?return=/)
  assert.match(html, /href="\.\.\/\?class=/)
})

test("sitemap includes every bundle page under the site base", () => {
  const root = siteRoot({ base: "/tw-exam/", origin: "https://example.github.io" })
  const sitemap = buildSitemap([bundle], root)
  assert.match(sitemap, /https:\/\/example\.github\.io\/tw-exam\/b\/exam-one\.html/)
  assert.match(sitemap, /https:\/\/example\.github\.io\/tw-exam\/about\.html/)
  assert.equal((sitemap.match(/<url>/g) ?? []).length, 6)
})

test("bundle pages attribute official sources and display a recorded sync date in Taiwan time", () => {
  const root = "https://example.github.io/tw-exam/"
  const html = buildBundlePage({
    ...bundle, sources: [{ name: "官方 <來源>", url: "https://official.example/exams" }],
    updated: "2026-09-25T20:00:00Z",
  }, { repo: "example/tw-exam", root })
  assert.match(html, /官方 &lt;來源&gt;/)
  assert.match(html, /href="https:\/\/official.example\/exams"/)
  assert.match(html, /<time datetime="2026-09-25T20:00:00Z">2026\/09\/26<\/time>/)
  const metadata = JSON.parse(html.match(/<script type="application\/ld\+json">(.*?)<\/script>/s)[1])
  assert.deepEqual(metadata[1].isBasedOn, ["https://official.example/exams"])
  const withoutDate = buildBundlePage(bundle, { repo: "example/tw-exam", root })
  assert.match(withoutDate, /同步日期未記錄/)
  assert.doesNotMatch(withoutDate, /<time /)
})
