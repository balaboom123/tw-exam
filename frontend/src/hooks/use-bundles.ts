import { useEffect, useRef, useState } from "react"
import type { Bundle } from "@/types"
import { expandBundle, parseCompactFeed } from "@/lib/public-feed"

interface UseBundlesResult {
  bundles: Bundle[]
  searchIndex: string[] | null
  loading: boolean
  error: string | null
  searchLoading: boolean
  searchError: boolean
  retrySearch: () => void
}

export function useBundles(query: string): UseBundlesResult {
  const [bundles, setBundles] = useState<Bundle[]>([])
  const [feedLoading, setFeedLoading] = useState(true)
  const [searchIndex, setSearchIndex] = useState<string[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [searchError, setSearchError] = useState(false)
  const [searchAttempt, setSearchAttempt] = useState(0)
  const searchPromise = useRef<Promise<string[]> | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    fetch(`${import.meta.env.BASE_URL}${import.meta.env.VITE_PUBLIC_BUNDLES_FILE}`, { signal: controller.signal })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json() as Promise<unknown>
      })
      .then((data) => {
        const feed = parseCompactFeed(data)
        setBundles(feed.bundles.map((bundle) => expandBundle(bundle, feed.repo)))
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
    if (!query.trim() || feedLoading || searchIndex || error || searchError) return
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
      () => {
        if (active) setSearchError(true)
      },
    )
    return () => { active = false }
  }, [query, feedLoading, bundles.length, searchIndex, error, searchError, searchAttempt])

  return {
    bundles,
    searchIndex,
    loading: feedLoading,
    error,
    searchLoading: Boolean(query.trim()) && !feedLoading && searchIndex === null && !error && !searchError,
    searchError,
    retrySearch: () => {
      searchPromise.current = null
      setSearchError(false)
      setSearchAttempt((attempt) => attempt + 1)
    },
  }
}
