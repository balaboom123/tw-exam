import type { Bundle, BundlePart } from "../types.ts"
import { isBundleSource, isSyncTimestamp } from "./provenance.ts"
import { isMaterialSummary, materialMatchesYears } from "./source-material.ts"

export type CompactPart = Omit<BundlePart, "url"> & { tag: string; asset: string }

export type CompactBundle = Omit<Bundle, "url" | "parts"> & {
  tag: string
  asset: string
  parts?: CompactPart[]
}

export interface CompactFeed {
  v: 2 | 3
  repo: string
  classes: string[]
  bundles: CompactBundle[]
}

type ReadableFeed = Pick<CompactFeed, "v" | "repo" | "bundles">
const segmentPattern = /^[A-Za-z0-9._-]+$/
const repositoryPattern = /^[A-Za-z0-9._-]+\/[A-Za-z0-9._-]+$/

function isValidPart(value: unknown): value is CompactPart {
  if (typeof value !== "object" || value === null) return false
  const part = value as Record<string, unknown>
  return typeof part.label === "string" && part.label.length > 0 &&
    Number.isInteger(part.fileCount) && Number(part.fileCount) > 0 &&
    typeof part.tag === "string" && segmentPattern.test(part.tag) &&
    typeof part.asset === "string" && segmentPattern.test(part.asset) && part.asset.endsWith(".zip")
}

function isValidBundle(value: unknown): value is CompactBundle {
  if (typeof value !== "object" || value === null) return false
  const item = value as Record<string, unknown>
  if (!(typeof item.id === "string" && item.id.length > 0 &&
    typeof item.name === "string" && item.name.length > 0 &&
    Array.isArray(item.years) && item.years.every(Number.isInteger) &&
    Number.isInteger(item.fileCount) && Number(item.fileCount) > 0 &&
    typeof item.tag === "string" && segmentPattern.test(item.tag) &&
    typeof item.asset === "string" && segmentPattern.test(item.asset) && item.asset.endsWith(".zip") &&
    typeof item.examClass === "string" && item.examClass.length > 0 &&
    typeof item.examSubclass === "string" && item.examSubclass.length > 0 &&
    (item.subjectLabels === undefined || (Array.isArray(item.subjectLabels) && item.subjectLabels.every((label) => typeof label === "string"))) &&
    (item.sources === undefined || (Array.isArray(item.sources) && item.sources.length > 0 && item.sources.every(isBundleSource))) &&
    (item.updated === undefined || isSyncTimestamp(item.updated)) &&
    (item.parts === undefined || (Array.isArray(item.parts) && item.parts.length > 0 && item.parts.every(isValidPart))))) return false
  if (item.sourceMaterial === undefined) return item.years.length > 0
  return isMaterialSummary(item.sourceMaterial) && materialMatchesYears(item.sourceMaterial, item.years)
}

export function parseCompactFeed(data: unknown): ReadableFeed {
  if (typeof data !== "object" || data === null) throw new Error("Invalid data format")
  const feed = data as Record<string, unknown>
  if ((feed.v !== 2 && feed.v !== 3) || typeof feed.repo !== "string" || !repositoryPattern.test(feed.repo) ||
    !Array.isArray(feed.bundles) || !feed.bundles.every(isValidBundle)) throw new Error("Data schema mismatch")
  const hasMaterial = feed.bundles.some((bundle) => bundle.sourceMaterial !== undefined)
  if ((feed.v === 3) !== hasMaterial) throw new Error("Material facts require compact feed v3")
  return feed as unknown as ReadableFeed
}

export function expandBundle(raw: CompactBundle, repo: string): Bundle {
  const releaseUrl = (tag: string, asset: string) => `https://github.com/${repo}/releases/download/${encodeURIComponent(tag)}/${encodeURIComponent(asset)}`
  return {
    id: raw.id,
    name: raw.name,
    years: raw.years,
    fileCount: raw.fileCount,
    url: releaseUrl(raw.tag, raw.asset),
    examClass: raw.examClass,
    examSubclass: raw.examSubclass,
    ...(raw.subjectLabels ? { subjectLabels: raw.subjectLabels } : {}),
    ...(raw.sources ? { sources: raw.sources } : {}),
    ...(raw.updated ? { updated: raw.updated } : {}),
    ...(raw.sourceMaterial ? { sourceMaterial: raw.sourceMaterial } : {}),
    ...(raw.parts ? {
      parts: raw.parts.map((part) => ({
        label: part.label,
        fileCount: part.fileCount,
        url: releaseUrl(part.tag, part.asset),
      })),
    } : {}),
  }
}
