import type { BundleSource } from "../types"

const dateFormatter = new Intl.DateTimeFormat("zh-TW", {
  timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit",
})

export function isBundleSource(value: unknown): value is BundleSource {
  if (!value || typeof value !== "object") return false
  const source = value as Record<string, unknown>
  if (typeof source.name !== "string" || !source.name.trim() || typeof source.url !== "string") return false
  try {
    const url = new URL(source.url)
    return url.protocol === "https:" && !url.username && !url.password
  } catch {
    return false
  }
}

export function isSyncTimestamp(value: unknown): value is string {
  return typeof value === "string" &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value) &&
    Number.isFinite(Date.parse(value))
}

export function formatSyncDate(timestamp: string): string {
  return dateFormatter.format(new Date(timestamp))
}
