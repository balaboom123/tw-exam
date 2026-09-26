import type { Bundle, BundlePart } from "../types.ts"

export type CompactPart = Omit<BundlePart, "url"> & { tag: string; asset: string }

export type CompactBundle = Omit<Bundle, "url" | "parts"> & {
  tag: string
  asset: string
  parts?: CompactPart[]
}

export interface CompactFeed {
  v: 2
  repo: string
  classes: string[]
  bundles: CompactBundle[]
}
