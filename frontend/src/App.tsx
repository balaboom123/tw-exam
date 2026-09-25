import { useState, useMemo, useRef, useEffect } from "react"
import { useBundles } from "@/hooks/use-bundles"
import { useDebouncedValue } from "@/hooks/use-debounce"
import { formatYearRange, siteHref } from "@/lib/utils"
import { Header } from "@/components/header"
import { SearchBar } from "@/components/search-bar"
import { YearFilter } from "@/components/year-filter"
import { SortSelect, type SortKey } from "@/components/sort-select"
import { BundleRow } from "@/components/bundle-row"
import { StatsBar } from "@/components/stats-bar"
import { EmptyState } from "@/components/empty-state"
import { LoadingSkeleton } from "@/components/loading-skeleton"
import { Pagination } from "@/components/pagination"
import { Stamp } from "@/components/stamp"
import { CategoryFilter } from "@/components/category-filter"
import { Footer } from "@/components/footer"
import { PaperGrain } from "@/components/paper-grain"
import { hasSocialAccess } from "@/lib/social-gate"
import { orderExamClasses, orderExamSubclasses } from "@/lib/exam-categories"
import { buildSearchQuery, readSearchState } from "@/lib/search-state"
import type { Bundle } from "@/types"

const PAGE_SIZE = 30
const nameCollator = new Intl.Collator("zh-TW")
const initialSearchState = readSearchState(window.location.search)

function App() {
  const [query, setQuery] = useState(initialSearchState.query)
  const debouncedQuery = useDebouncedValue(query, 200)
  const { bundles, searchIndex, loading, error } = useBundles(debouncedQuery)
  const [selectedYear, setSelectedYear] = useState<number | null>(initialSearchState.year)
  const [selectedClass, setSelectedClass] = useState<string | null>(initialSearchState.examClass)
  const [selectedSubclass, setSelectedSubclass] = useState<string | null>(initialSearchState.subclass)
  const [sortKey, setSortKey] = useState<SortKey>(initialSearchState.sort)
  const [page, setPage] = useState(initialSearchState.page)
  const [unlocked, setUnlocked] = useState(hasSocialAccess)
  const [shareFeedback, setShareFeedback] = useState<string | null>(null)
  const listTopRef = useRef<HTMLParagraphElement>(null)

  // Pick up access granted in another tab or after a same-tab mobile return.
  useEffect(() => {
    const sync = () => setUnlocked((u) => u || hasSocialAccess())
    window.addEventListener("storage", sync)
    window.addEventListener("focus", sync)
    window.addEventListener("pageshow", sync)
    document.addEventListener("visibilitychange", sync)
    return () => {
      window.removeEventListener("storage", sync)
      window.removeEventListener("focus", sync)
      window.removeEventListener("pageshow", sync)
      document.removeEventListener("visibilitychange", sync)
    }
  }, [])

  useEffect(() => {
    const syncFromLocation = () => {
      const next = readSearchState(window.location.search)
      setQuery(next.query)
      setSelectedYear(next.year)
      setSelectedClass(next.examClass)
      setSelectedSubclass(next.subclass)
      setSortKey(next.sort)
      setPage(next.page)
    }
    window.addEventListener("popstate", syncFromLocation)
    return () => window.removeEventListener("popstate", syncFromLocation)
  }, [])

  useEffect(() => {
    const search = buildSearchQuery({
      query,
      year: selectedYear,
      examClass: selectedClass,
      subclass: selectedSubclass,
      sort: sortKey,
      page,
    })
    const nextUrl = `${window.location.pathname}${search ? `?${search}` : ""}${window.location.hash}`
    const currentUrl = `${window.location.pathname}${window.location.search}${window.location.hash}`
    if (nextUrl !== currentUrl) window.history.replaceState(null, "", nextUrl)
  }, [query, selectedYear, selectedClass, selectedSubclass, sortKey, page])

  const allYears = useMemo(() => {
    const set = new Set<number>()
    for (const b of bundles) {
      for (const y of b.years) set.add(y)
    }
    return Array.from(set).sort((a, b) => b - a)
  }, [bundles])

  const baseFiltered = useMemo(() => {
    let result = bundles
    if (debouncedQuery.trim()) {
      const q = debouncedQuery.trim().toLowerCase()
      result = result.filter((_, index) => searchIndex?.[index]?.includes(q))
    }
    if (selectedYear !== null) {
      result = result.filter((b) => b.years.includes(selectedYear))
    }
    return result
  }, [bundles, searchIndex, debouncedQuery, selectedYear])

  const classCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const b of baseFiltered) {
      counts[b.examClass] = (counts[b.examClass] ?? 0) + 1
    }
    return counts
  }, [baseFiltered])

  const availableClasses = useMemo(
    () => orderExamClasses(Object.keys(classCounts)),
    [classCounts]
  )

  const subclassCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const b of baseFiltered) {
      if (selectedClass && b.examClass !== selectedClass) continue
      counts[b.examSubclass] = (counts[b.examSubclass] ?? 0) + 1
    }
    return counts
  }, [baseFiltered, selectedClass])

  const availableSubclasses = useMemo(
    () => orderExamSubclasses(Object.keys(subclassCounts)),
    [subclassCounts]
  )

  const filtered = useMemo(() => {
    let result = baseFiltered

    if (selectedClass) {
      result = result.filter((b) => b.examClass === selectedClass)
    }

    if (selectedSubclass) {
      result = result.filter((b) => b.examSubclass === selectedSubclass)
    }

    const sorted = [...result]
    switch (sortKey) {
      case "name":
        sorted.sort((a, b) => nameCollator.compare(a.name, b.name))
        break
      case "files-desc":
        sorted.sort((a, b) => b.fileCount - a.fileCount)
        break
      case "years-desc":
        sorted.sort((a, b) => b.years.length - a.years.length)
        break
    }

    return sorted
  }, [baseFiltered, selectedClass, selectedSubclass, sortKey])

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const safePage = Math.min(page, totalPages)
  const paginated = filtered.slice(
    (safePage - 1) * PAGE_SIZE,
    safePage * PAGE_SIZE
  )

  const totalFiles = useMemo(
    () => bundles.reduce((sum, b) => sum + b.fileCount, 0),
    [bundles]
  )

  const yearRange = useMemo(() => formatYearRange(allYears), [allYears])
  const returnSearch = buildSearchQuery({
    query, year: selectedYear, examClass: selectedClass,
    subclass: selectedSubclass, sort: sortKey, page,
  })
  const returnUrl = new URL(
    `${siteHref("")}${returnSearch ? `?${returnSearch}` : ""}${window.location.hash}`,
    window.location.origin,
  )
  const joinHref = `${siteHref("join.html")}?return=${encodeURIComponent(returnUrl.href)}`

  function handleQueryChange(value: string) {
    setQuery(value)
    setPage(1)
  }

  function handleYearChange(year: number | null) {
    setSelectedYear(year)
    setPage(1)
  }

  function handleClassChange(cls: string | null) {
    setSelectedClass(cls)
    setSelectedSubclass(null)
    setPage(1)
  }

  function handleSubclassChange(sub: string | null) {
    setSelectedSubclass(sub)
    setPage(1)
  }

  function handleSortChange(key: SortKey) {
    setSortKey(key)
    setPage(1)
  }

  async function handleShareLink() {
    try {
      if (navigator.share) {
        await navigator.share({ title: "tw-exam", url: window.location.href })
        setShareFeedback("分享完成")
      } else if (navigator.clipboard) {
        await navigator.clipboard.writeText(window.location.href)
        setShareFeedback("連結已複製")
      }
      window.setTimeout(() => setShareFeedback(null), 1800)
    } catch {
      // Closing the native share sheet is an expected user action.
    }
  }

  function handlePageChange(nextPage: number) {
    setPage(nextPage)
    listTopRef.current?.scrollIntoView()
  }

  function handleReset() {
    setQuery("")
    setSelectedYear(null)
    setSelectedClass(null)
    setSelectedSubclass(null)
    setPage(1)
  }

  if (error) {
    return (
      <div className="flex min-h-[100dvh] flex-col">
        <PaperGrain />
        <Header totalBundles={0} />
        <div className="flex flex-1 items-center justify-center px-6">
          <div className="flex flex-col items-center text-center">
            <Stamp>載入失敗</Stamp>
            <h1 className="mt-7 font-medium text-ink-950">資料載入失敗</h1>
            <p className="mt-1.5 text-sm text-ink-500">暫時無法取得試題目錄，請稍後重試。</p>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="mt-5 h-10 rounded-sm border border-line-strong px-4 text-sm font-medium text-ink-800 transition-colors hover:bg-cream"
            >
              重新載入
            </button>
          </div>
        </div>
        <Footer />
      </div>
    )
  }

  return (
    <div className="flex min-h-[100dvh] flex-col">
      <PaperGrain />
      <Header totalBundles={bundles.length} />

      <main id="main" aria-busy={loading} className="mx-auto w-full max-w-4xl flex-1 px-6 pb-10 pt-10">
        <section className="flex items-start justify-between gap-8">
          <div>
            <h1 className="font-serif text-3xl font-black tracking-tight text-ink-950 md:text-[2.5rem] md:leading-[1.15]">
              歷屆試題下載
            </h1>
            <p className="mt-3 max-w-[58ch] text-[15px] leading-relaxed text-ink-600">
              收錄國家考試、國營事業甄試、國中會考及技能檢定歷年試題，依類科彙整為多年度
              ZIP 檔，可直接下載。
            </p>
          </div>
          <span
            aria-hidden="true"
            className="hidden shrink-0 select-none border-l border-line pl-4 pt-1 font-serif text-sm tracking-[0.3em] text-ink-400 [writing-mode:vertical-rl] md:block"
          >
            歷屆試題檔案庫
          </span>
        </section>

        <div className="mt-8 min-h-[70px] sm:min-h-[44px]">
          {loading ? (
            <div aria-hidden="true" className="flex flex-wrap items-center gap-8 border-y border-line py-3">
              <span className="h-3 w-20 animate-pulse rounded-sm bg-paper-deep" />
              <span className="h-3 w-20 animate-pulse rounded-sm bg-paper-deep" />
              <span className="h-3 w-28 animate-pulse rounded-sm bg-paper-deep" />
            </div>
          ) : (
            <StatsBar total={bundles.length} totalFiles={totalFiles} yearRange={yearRange} />
          )}
        </div>

        <div className={selectedClass ? "mt-6 min-h-[108px] sm:min-h-[88px]" : "mt-6 min-h-[48px] sm:min-h-[40px]"}>
          {loading ? (
            <div aria-hidden="true" className="space-y-3">
              <div className="flex h-11 items-center gap-2 sm:h-9">
                <span className="h-8 w-20 animate-pulse rounded-sm bg-paper-deep" />
                <span className="h-8 w-24 animate-pulse rounded-sm bg-paper-deep" />
                <span className="h-8 w-24 animate-pulse rounded-sm bg-paper-deep" />
              </div>
              {selectedClass && <div className="h-11 w-64 animate-pulse rounded-sm bg-paper-deep sm:h-8" />}
            </div>
          ) : (
            <CategoryFilter
              availableClasses={availableClasses}
              availableSubclasses={availableSubclasses}
              selectedClass={selectedClass}
              selectedSubclass={selectedSubclass}
              onClassChange={handleClassChange}
              onSubclassChange={handleSubclassChange}
              classCounts={classCounts}
              subclassCounts={subclassCounts}
            />
          )}
        </div>

        <div className="mt-6 flex flex-col gap-4">
          <div className="flex flex-col gap-2 sm:flex-row">
            <div className="min-w-0 flex-1">
              <SearchBar value={query} onChange={handleQueryChange} />
            </div>
            <button
              type="button"
              onClick={handleShareLink}
              className="h-12 shrink-0 rounded-sm border border-line-strong bg-cream px-4 text-sm font-medium text-ink-700 transition-colors hover:bg-paper-deep hover:text-ink-950"
            >
              {shareFeedback ?? "分享搜尋連結"}
            </button>
          </div>

          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <YearFilter
              years={allYears}
              selected={selectedYear}
              onSelect={handleYearChange}
            />
            <SortSelect value={sortKey} onChange={handleSortChange} />
          </div>
        </div>

        <div className="mt-8">
          {!loading && !unlocked && (
            <p className="mb-3 text-xs leading-relaxed text-ink-600">
              首次下載前，請先在加入頁選擇一個 LINE 社群；返回後即可下載試題。
            </p>
          )}
          {loading ? (
            <LoadingSkeleton />
          ) : filtered.length === 0 ? (
            <EmptyState onReset={handleReset} />
          ) : (
            <div className="animate-fade-in">
              <p
                ref={listTopRef}
                className="mb-2 scroll-mt-24 font-mono text-xs text-ink-500"
                aria-live="polite"
              >
                第 {(safePage - 1) * PAGE_SIZE + 1}–
                {Math.min(safePage * PAGE_SIZE, filtered.length)} 筆 · 共{" "}
                {filtered.length.toLocaleString()} 筆
              </p>
              {/* Safari needs an explicit list role when markers are removed. */}
              {/* eslint-disable-next-line jsx-a11y-x/no-redundant-roles */}
              <ul
                role="list"
                className="-mx-4 divide-y divide-line border-y border-line"
              >
                {paginated.map((bundle: Bundle) => (
                  <BundleRow
                    key={bundle.id}
                    bundle={bundle}
                    unlocked={unlocked}
                    joinHref={joinHref}
                  />
                ))}
              </ul>
              <Pagination
                current={safePage}
                total={totalPages}
                onChange={handlePageChange}
              />
            </div>
          )}
        </div>
      </main>

      <Footer />
    </div>
  )
}

export default App
