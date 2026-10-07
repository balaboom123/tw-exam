export type ShareableSortKey = "name" | "files-desc" | "years-desc"
export type PublicationYearFilter = number | "undated" | null

export interface SearchState {
  query: string
  year: PublicationYearFilter
  examClass: string | null
  subclass: string | null
  sort: ShareableSortKey
  page: number
}

const DEFAULT_STATE: SearchState = {
  query: "",
  year: null,
  examClass: null,
  subclass: null,
  sort: "name",
  page: 1,
}

function positiveInteger(value: string | null): number | null {
  if (!value || !/^\d+$/.test(value)) return null
  const parsed = Number(value)
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null
}

function sortKey(value: string | null): ShareableSortKey {
  if (value === "files-desc" || value === "years-desc") return value
  return "name"
}

function publicationYear(value: string | null): PublicationYearFilter {
  if (value === "undated") return value
  if (!value || !/^-?\d+$/.test(value)) return null
  const parsed = Number(value)
  return Number.isSafeInteger(parsed) ? parsed : null
}

export function readSearchState(search: string): SearchState {
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search)
  return {
    query: params.get("q")?.trim() ?? DEFAULT_STATE.query,
    year: publicationYear(params.get("year")),
    examClass: params.get("class")?.trim() || null,
    subclass: params.get("subclass")?.trim() || null,
    sort: sortKey(params.get("sort")),
    page: positiveInteger(params.get("page")) ?? DEFAULT_STATE.page,
  }
}

export function buildSearchQuery(state: SearchState): string {
  const params = new URLSearchParams()
  if (state.query.trim()) params.set("q", state.query.trim())
  if (state.year !== null) params.set("year", String(state.year))
  if (state.examClass) params.set("class", state.examClass)
  if (state.subclass) params.set("subclass", state.subclass)
  if (state.sort !== "name") params.set("sort", state.sort)
  if (state.page > 1) params.set("page", String(state.page))
  return params.toString()
}
