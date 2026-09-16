import assert from "node:assert/strict"
import { mkdtemp, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import test from "node:test"

import {
  readFrontendBundlesSource,
  resolveAdsenseEnabled,
  resolvePagesBase,
  toFrontendBundles,
} from "./bundles-data.mjs"

test("resolvePagesBase uses the renamed GitHub repository path", () => {
  assert.equal(
    resolvePagesBase({ githubRepository: "balaboom123/tw-exam" }),
    "/tw-exam/",
  )
})

test("resolvePagesBase falls back to the site root outside GitHub Pages builds", () => {
  assert.equal(resolvePagesBase({}), "/")
})

test("resolveAdsenseEnabled disables AdSense for GitHub Pages project-site builds", () => {
  assert.equal(
    resolveAdsenseEnabled({ githubRepository: "balaboom123/tw-exam" }),
    false,
  )
})

test("resolveAdsenseEnabled enables AdSense for root-hosted production builds", () => {
  assert.equal(resolveAdsenseEnabled({ explicitBase: "/" }), true)
})

test("resolveAdsenseEnabled stays off during non-build runs unless explicitly enabled", () => {
  assert.equal(resolveAdsenseEnabled({ isBuild: false }), false)
  assert.equal(resolveAdsenseEnabled({ isBuild: false, explicitEnabled: "true" }), true)
})

test("toFrontendBundles converts generated bundle records into the frontend schema", () => {
  assert.deepEqual(
    toFrontendBundles([
      {
        canonical_id: "nurse",
        canonical_name: "Nurse",
        years: [115, 113],
        file_count: 607,
        download_url: "https://example.com/nurse.zip",
        checksum: "sha-1",
      },
    ]),
    [
      {
        id: "nurse",
        name: "Nurse",
        years: [115, 113],
        fileCount: 607,
        url: "https://example.com/nurse.zip",
      },
    ],
  )
})

test("toFrontendBundles groups multipart physical records into one logical download row", () => {
  assert.deepEqual(
    toFrontendBundles([
      {
        canonical_id: "large",
        canonical_name: "Large",
        years: [115],
        file_count: 4,
        download_url: "https://example.com/large-part-1.zip",
        checksum: "sha-1",
        part_index: 1,
        part_count: 2,
        part_label: "第 1/2 部分",
      },
      {
        canonical_id: "large",
        canonical_name: "Large",
        years: [114],
        file_count: 5,
        download_url: "https://example.com/large-part-2.zip",
        checksum: "sha-2",
        part_index: 2,
        part_count: 2,
        part_label: "第 2/2 部分",
      },
    ]),
    [
      {
        id: "large",
        name: "Large",
        years: [115, 114],
        fileCount: 9,
        url: "https://example.com/large-part-1.zip",
        parts: [
          { label: "第 1/2 部分", url: "https://example.com/large-part-1.zip", fileCount: 4 },
          { label: "第 2/2 部分", url: "https://example.com/large-part-2.zip", fileCount: 5 },
        ],
      },
    ],
  )
})

test("toFrontendBundles preserves structured v2 identity facets", () => {
  assert.deepEqual(
    toFrontendBundles([
      {
        canonical_id: "canonical-general-administration",
        canonical_name: "一般行政",
        bundle_id: "moex-civil-high-grade-3-general-administration",
        years: [115, 114],
        file_count: 120,
        download_url: "https://example.com/general-admin-high.zip",
        checksum: "sha-v2",
        domain_id: "civil-service",
        exam_family_id: "civil-service-exam",
        exam_series_id: "civil-high",
        level_id: "grade-3",
        track_id: "general-administration",
        variant_ids: [],
        stage_id: "not-applicable",
        exam_class: "公職考試",
        exam_subclass: "公職／公務人員",
      },
    ]),
    [
      {
        id: "moex-civil-high-grade-3-general-administration",
        name: "一般行政",
        years: [115, 114],
        fileCount: 120,
        url: "https://example.com/general-admin-high.zip",
        domainId: "civil-service",
        examFamilyId: "civil-service-exam",
        seriesId: "civil-high",
        levelId: "grade-3",
        trackId: "general-administration",
        variantIds: [],
        stageId: "not-applicable",
        examClass: "公職考試",
        examSubclass: "公職／公務人員",
      },
    ],
  )
})

test("toFrontendBundles accepts wrapped site bundles schema", () => {
  assert.deepEqual(
    toFrontendBundles({
      schema_version: 1,
      site_id: "default",
      bundles: [
        {
          canonical_id: "nurse",
          canonical_name: "Nurse",
          years: [115, 113],
          file_count: 607,
          download_url: "https://example.com/nurse.zip",
          checksum: "sha-1",
        },
      ],
    }),
    [
      {
        id: "nurse",
        name: "Nurse",
        years: [115, 113],
        fileCount: 607,
        url: "https://example.com/nurse.zip",
      },
    ],
  )
})

test("readFrontendBundlesSource rejects when the site-scoped bundles file is missing", async () => {
  const tempDir = await mkdtemp(path.join(os.tmpdir(), "bundles-data-"))
  const missingSitePath = path.join(tempDir, "sites", "default", "bundles.json")
  const legacyPath = path.join(tempDir, "bundles.json")

  await writeFile(
    legacyPath,
    JSON.stringify([
      {
        canonical_id: "nurse",
        canonical_name: "Nurse",
        years: [115],
        file_count: 1,
        download_url: "https://example.com/nurse.zip",
        checksum: "sha-1",
      },
    ]),
    "utf8",
  )

  await assert.rejects(
    () => readFrontendBundlesSource([missingSitePath, legacyPath]),
    /ENOENT/,
  )
})
