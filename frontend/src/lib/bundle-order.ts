import type { Bundle } from "@/types"
import type { ShareableSortKey } from "./search-state"

const nameCollator = new Intl.Collator("zh-TW")

export function orderBundles(bundles: readonly Bundle[], sortKey: ShareableSortKey): Bundle[] {
  const ordered = [...bundles]
  switch (sortKey) {
    case "name":
      return ordered.sort((a, b) => nameCollator.compare(a.name, b.name))
    case "files-desc":
      return ordered.sort((a, b) => b.fileCount - a.fileCount)
    case "years-desc":
      return ordered.sort((a, b) => b.years.length - a.years.length)
  }
}

export function selectOrderedBundles(
  ordered: readonly Bundle[],
  candidates: readonly Bundle[],
  examClass: string | null,
  subclass: string | null,
): Bundle[] {
  const included = new Set(candidates.map((bundle) => bundle.id))
  return ordered.filter((bundle) => included.has(bundle.id) &&
    (examClass === null || bundle.examClass === examClass) &&
    (subclass === null || bundle.examSubclass === subclass))
}
