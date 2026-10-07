import { rocToAd } from "@/lib/utils"
import type { PublicationYearFilter } from "@/lib/search-state"

interface YearFilterProps {
  years: number[]
  selected: PublicationYearFilter
  hasUndated: boolean
  onSelect: (year: PublicationYearFilter) => void
}

export function YearFilter({ years, selected, hasUndated, onSelect }: YearFilterProps) {
  return (
    <select id="exam-year" aria-label="收錄年份" value={selected ?? ""}
      onChange={(event) => onSelect(event.target.value === "undated" ? "undated" : event.target.value ? Number(event.target.value) : null)}
      aria-describedby="year-download-note">
      <option value="">全部年份</option>
      {(hasUndated || selected === "undated") && <option value="undated">未標示年份</option>}
      {typeof selected === "number" && !years.includes(selected) && <option value={selected}>{selected > 0 ? `民國 ${selected}` : rocToAd(selected)} 年（未收錄）</option>}
      {years.map((year) => <option key={year} value={year}>{year > 0 ? `民國 ${year} 年（${rocToAd(year)}）` : `${rocToAd(year)} 年`}</option>)}
    </select>
  )
}
