import { useState, useMemo, useRef, useEffect } from "react"
import { ArrowUpRight, Check, ListFilter, Share2, X } from "lucide-react"
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
import { hasSocialAccess, withSocialAccess } from "@/lib/social-gate"
import { orderExamClasses, orderExamSubclasses } from "@/lib/exam-categories"
import { buildSearchQuery, readSearchState } from "@/lib/search-state"
import { orderBundles, selectOrderedBundles } from "@/lib/bundle-order"
import type { Bundle } from "@/types"

const PAGE_SIZE = 30
const initialSearchState = readSearchState(window.location.search)

function App() {
  const [query, setQuery] = useState(initialSearchState.query)
  const debouncedQuery = useDebouncedValue(query, 200)
  const { bundles, searchIndex, loading, error, searchLoading, searchError, retrySearch } = useBundles(debouncedQuery)
  const [selectedYear, setSelectedYear] = useState<number | null>(initialSearchState.year)
  const [selectedClass, setSelectedClass] = useState<string | null>(initialSearchState.examClass)
  const [selectedSubclass, setSelectedSubclass] = useState<string | null>(initialSearchState.subclass)
  const [sortKey, setSortKey] = useState<SortKey>(initialSearchState.sort)
  const [page, setPage] = useState(initialSearchState.page)
  const [unlocked, setUnlocked] = useState(hasSocialAccess)
  const [shareFeedback, setShareFeedback] = useState<string | null>(null)
  const listTopRef = useRef<HTMLHeadingElement>(null)
  const shareTimeoutRef = useRef<number | undefined>(undefined)
  const [filtersOpen, setFiltersOpen] = useState(false)

  useEffect(() => () => window.clearTimeout(shareTimeoutRef.current), [])

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
    const nextUrl = withSocialAccess(`${window.location.pathname}${search ? `?${search}` : ""}${window.location.hash}`)
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
      result = result.filter((bundle, index) => (searchIndex?.[index] ?? bundle.name.toLowerCase()).includes(q))
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
    () => orderExamClasses([...new Set(bundles.map((bundle) => bundle.examClass))]),
    [bundles]
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
    () => orderExamSubclasses([...new Set(bundles.filter((bundle) => bundle.examClass === selectedClass).map((bundle) => bundle.examSubclass))]),
    [bundles, selectedClass]
  )

  const orderedBundles = useMemo(() => orderBundles(bundles, sortKey), [bundles, sortKey])
  const filtered = useMemo(
    () => selectOrderedBundles(orderedBundles, baseFiltered, selectedClass, selectedSubclass),
    [orderedBundles, baseFiltered, selectedClass, selectedSubclass],
  )

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
    window.clearTimeout(shareTimeoutRef.current)
    try {
      if (navigator.share) {
        await navigator.share({ title: "tw-exam", url: window.location.href })
        setShareFeedback("分享完成")
      } else if (navigator.clipboard) {
        await navigator.clipboard.writeText(window.location.href)
        setShareFeedback("連結已複製")
      } else {
        setShareFeedback("請複製網址列的連結")
      }
    } catch (error) {
      if (error instanceof Error && error.name === "AbortError") return
      setShareFeedback("請複製網址列的連結")
    }
    shareTimeoutRef.current = window.setTimeout(() => setShareFeedback(null), 4000)
  }

  function handlePageChange(nextPage: number) {
    setPage(nextPage)
    listTopRef.current?.focus({ preventScroll: true })
    listTopRef.current?.scrollIntoView({ block: "start" })
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
        <Header totalBundles={0} />
        <main id="main" className="flex flex-1 items-center justify-center px-6">
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
        </main>
        <Footer />
      </div>
    )
  }

  const activeFilters = [
    ...(query.trim() ? [{ label: `搜尋：${query}`, clear: () => handleQueryChange("") }] : []),
    ...(selectedClass ? [{ label: selectedClass, clear: () => handleClassChange(null) }] : []),
    ...(selectedSubclass ? [{ label: selectedSubclass, clear: () => handleSubclassChange(null) }] : []),
    ...(selectedYear ? [{ label: `民國 ${selectedYear} 年`, clear: () => handleYearChange(null) }] : []),
  ]

  return (
    <div className="flex min-h-[100dvh] flex-col">
      <Header totalBundles={bundles.length} />
      <main id="main" aria-busy={loading} className="workspace-shell flex-1">
        <section className="collection-intro" aria-labelledby="collection-title">
          <div>
            <h1 id="collection-title" className="collection-title">歷屆試題，一次整理好。</h1>
            <p className="mt-4 max-w-[40em] text-sm leading-7 text-ink-600 sm:text-[15px]">
              找到你的考試類科，收齊歷年試題。<br />
              依類科彙整多年度 ZIP 檔，讓準備更有方向。
            </p>
          </div>
          {loading ? (
            <div aria-hidden="true" className="collection-stats"><div className="skeleton h-20 w-full" /></div>
          ) : (
            <StatsBar total={bundles.length} totalFiles={totalFiles} yearRange={yearRange} />
          )}
        </section>

        <section aria-label="搜尋試題" className="search-panel">
          <div className="min-w-0 flex-1"><SearchBar value={query} onChange={handleQueryChange} /></div>
          <button type="button" onClick={handleShareLink} aria-label="分享搜尋連結" className="share-button">
            <Share2 aria-hidden="true" className="size-4" />
            <span>分享搜尋</span>
          </button>
          <span role="status" className={shareFeedback ? "share-feedback" : "sr-only"}>{shareFeedback}</span>
        </section>

        <div className="catalog-layout">
          <aside className="filter-rail" aria-label="篩選試題">
            <button type="button" className="mobile-filter-toggle" aria-expanded={filtersOpen} aria-controls="catalog-filters" onClick={() => setFiltersOpen(!filtersOpen)}>
              <ListFilter aria-hidden="true" className="size-4" />
              篩選考試{activeFilters.length > 0 ? `（${activeFilters.length}）` : ""}
              <span className="ml-auto text-xs">{filtersOpen ? "收起" : "展開"}</span>
            </button>
            <div id="catalog-filters" className={`filter-content ${filtersOpen ? "is-open" : ""}`}>
              <h2 className="mb-4 hidden items-center gap-2 text-sm font-bold text-ink-950 lg:flex"><ListFilter aria-hidden="true" className="size-4" />考試分類</h2>
              {loading ? <div aria-hidden="true" className="skeleton h-64 w-full" /> : (
                <CategoryFilter
                  availableClasses={availableClasses} availableSubclasses={availableSubclasses}
                  selectedClass={selectedClass} selectedSubclass={selectedSubclass}
                  onClassChange={handleClassChange} onSubclassChange={handleSubclassChange}
                  classCounts={classCounts} subclassCounts={subclassCounts}
                />
              )}
              <div className="mt-6 border-t border-line pt-5">
                <label htmlFor="exam-year" className="mb-3 block text-sm font-bold text-ink-950">考試年度</label>
                <YearFilter years={allYears} selected={selectedYear} onSelect={handleYearChange} />
                <p className="mt-2 text-xs leading-5 text-ink-500">年度以民國紀年，下載檔包含該類科收錄的所有年度。</p>
              </div>
            </div>
            <div className="study-note">
              <h2 className="text-sm font-bold text-ink-950">第一次下載？</h2>
              <p className="mt-2 text-xs leading-6 text-ink-600">選擇一個 LINE 社群，返回後即可下載。試題來源與收錄年度可在各類科中查看。</p>
              <a href={siteHref("faq.html")} className="mt-3 inline-flex min-h-11 items-center gap-1 text-xs font-bold text-seal-600">查看常見問題<ArrowUpRight aria-hidden="true" className="size-4" /></a>
            </div>
          </aside>

          <section className="min-w-0" aria-labelledby="results-title">
            <div className="results-toolbar">
              <div>
                <h2 ref={listTopRef} id="results-title" tabIndex={-1} className="scroll-mt-24 text-lg font-bold text-ink-950">{selectedSubclass ?? selectedClass ?? "全部試題"}</h2>
                <p className="mt-1 text-xs text-ink-500" role="status">
                  {loading ? "正在載入試題目錄…" : `共 ${filtered.length.toLocaleString()} 個類科${filtered.length > 0 ? `，顯示 ${(safePage - 1) * PAGE_SIZE + 1}–${Math.min(safePage * PAGE_SIZE, filtered.length)} 筆` : ""}`}
                </p>
              </div>
              <SortSelect value={sortKey} onChange={handleSortChange} />
            </div>

            {activeFilters.length > 0 && (
              <div className="active-filters" aria-label="目前篩選條件">
                {activeFilters.map(({ label, clear }) => (
                  <button type="button" key={label} onClick={clear} aria-label={`移除篩選：${label}`} className="filter-chip">
                    <span className="min-w-0 break-words">{label}</span><X aria-hidden="true" className="size-3.5 shrink-0" />
                  </button>
                ))}
                <button type="button" onClick={handleReset} className="min-h-11 px-2 text-xs text-ink-600 underline underline-offset-4">清除全部</button>
              </div>
            )}

            {Boolean(debouncedQuery.trim()) && (searchLoading || searchError) && (
              <p role="status" className="mb-3 text-xs leading-relaxed text-ink-600">
                {searchError ? "完整搜尋暫時無法載入，目前僅搜尋試題名稱。" : "目前搜尋試題名稱，完整搜尋載入中。"}
                {searchError && <button type="button" onClick={retrySearch} className="ml-2 min-h-11 underline underline-offset-4">重試完整搜尋</button>}
              </p>
            )}
            {!loading && (
              <div className="download-notice">
                {unlocked ? <Check aria-hidden="true" className="size-4 shrink-0" /> : <ArrowUpRight aria-hidden="true" className="size-4 shrink-0" />}
                <p>{unlocked ? "已解鎖下載，選擇類科即可取得 ZIP 試題檔。" : <>首次下載請先<a href={joinHref} className="font-bold underline underline-offset-4">加入 LINE 社群</a>，返回後即可下載試題。</>}</p>
              </div>
            )}
            {loading ? <LoadingSkeleton /> : filtered.length === 0 ? <EmptyState onReset={handleReset} /> : (
              <>
                {/* Safari needs an explicit list role when markers are removed. */}
                {/* eslint-disable-next-line jsx-a11y-x/no-redundant-roles */}
                <ul role="list" className="bundle-list">
                  {paginated.map((bundle: Bundle) => <BundleRow key={bundle.id} bundle={bundle} unlocked={unlocked} joinHref={joinHref} />)}
                </ul>
                <Pagination current={safePage} total={totalPages} onChange={handlePageChange} />
              </>
            )}
          </section>
        </div>
      </main>
      <Footer />
    </div>
  )
}

export default App
