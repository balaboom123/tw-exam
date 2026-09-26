import { useEffect, useRef, useState } from "react"
import type { Bundle, BundleSource } from "@/types"
import { isBundleSource, isSyncTimestamp } from "@/lib/provenance"

interface UseBundlesResult {
  bundles: Bundle[]
  searchIndex: string[] | null
  loading: boolean
  error: string | null
}

interface RawPart {
  label: string
  fileCount: number
  tag: string
  asset: string
}

interface RawBundle {
  id: string
  name: string
  years: number[]
  fileCount: number
  tag: string
  asset: string
  parts?: RawPart[]
  examClass: string
  examSubclass: string
  subjectLabels?: string[]
  sources?: BundleSource[]
  updated?: string
}

interface RawFeed {
  v: 2
  repo: string
  bundles: RawBundle[]
}

const segmentPattern = /^[A-Za-z0-9._-]+$/
const repositoryPattern = /^[A-Za-z0-9._-]+\/[A-Za-z0-9._-]+$/

function isValidPart(value: unknown): value is RawPart {
  if (typeof value !== "object" || value === null) return false
  const part = value as Record<string, unknown>
  return typeof part.label === "string" && part.label.length > 0 &&
    Number.isInteger(part.fileCount) && Number(part.fileCount) > 0 &&
    typeof part.tag === "string" && segmentPattern.test(part.tag) &&
    typeof part.asset === "string" && segmentPattern.test(part.asset) && part.asset.endsWith(".zip")
}

function isValidRawBundle(value: unknown): value is RawBundle {
  if (typeof value !== "object" || value === null) return false
  const item = value as Record<string, unknown>
  return typeof item.id === "string" && item.id.length > 0 &&
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
    (item.parts === undefined || (Array.isArray(item.parts) && item.parts.every(isValidPart)))
}

function parseFeed(data: unknown): RawFeed {
  if (typeof data !== "object" || data === null) throw new Error("Invalid data format")
  const feed = data as Record<string, unknown>
  if (feed.v !== 2 || typeof feed.repo !== "string" || !repositoryPattern.test(feed.repo) ||
    !Array.isArray(feed.bundles) || !feed.bundles.every(isValidRawBundle)) {
    throw new Error("Data schema mismatch")
  }
  return feed as unknown as RawFeed
}

function releaseUrl(repo: string, tag: string, asset: string): string {
  return `https://github.com/${repo}/releases/download/${encodeURIComponent(tag)}/${encodeURIComponent(asset)}`
}

function toBundle(raw: RawBundle, repo: string): Bundle {
  return {
    id: raw.id,
    name: raw.name,
    years: raw.years,
    fileCount: raw.fileCount,
    url: releaseUrl(repo, raw.tag, raw.asset),
    examClass: raw.examClass,
    examSubclass: raw.examSubclass,
    ...(raw.subjectLabels ? { subjectLabels: raw.subjectLabels } : {}),
    ...(raw.sources ? { sources: raw.sources } : {}),
    ...(raw.updated ? { updated: raw.updated } : {}),
    ...(raw.parts ? {
      parts: raw.parts.map((part) => ({
        label: part.label,
        fileCount: part.fileCount,
        url: releaseUrl(repo, part.tag, part.asset),
      })),
    } : {}),
  }
}

export function useBundles(query: string): UseBundlesResult {
  const [bundles, setBundles] = useState<Bundle[]>([])
  const [feedLoading, setFeedLoading] = useState(true)
  const [searchIndex, setSearchIndex] = useState<string[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const searchPromise = useRef<Promise<string[]> | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    fetch(`${import.meta.env.BASE_URL}${import.meta.env.VITE_PUBLIC_BUNDLES_FILE}`, { signal: controller.signal })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json() as Promise<unknown>
      })
      .then((data) => {
        const feed = parseFeed(data)
        setBundles(feed.bundles.map((bundle) => toBundle(bundle, feed.repo)))
        setFeedLoading(false)
      })
      .catch((err: unknown) => {
        if (err instanceof Error && err.name === "AbortError") return
        setError(err instanceof Error ? err.message : "Failed to load bundle data")
        setFeedLoading(false)
      })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (!query.trim() || feedLoading || searchIndex || error) return
    searchPromise.current ??= fetch(`${import.meta.env.BASE_URL}${import.meta.env.VITE_PUBLIC_SEARCH_FILE}`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json() as Promise<unknown>
      })
      .then((data) => {
        if (!Array.isArray(data) || data.length !== bundles.length ||
          !data.every((item) => typeof item === "string")) {
          throw new Error("Search index schema mismatch")
        }
        return data as string[]
      })
    let active = true
    searchPromise.current.then(
      (data) => { if (active) setSearchIndex(data) },
      (err: unknown) => {
        if (active) setError(err instanceof Error ? err.message : "Failed to load search index")
      },
    )
    return () => { active = false }
  }, [query, feedLoading, bundles.length, searchIndex, error])

  return {
    bundles,
    searchIndex,
    loading: feedLoading || (Boolean(query.trim()) && searchIndex === null && error === null),
    error,
  }
}
