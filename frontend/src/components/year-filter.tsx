import { rocToAd } from "@/lib/utils"

interface YearFilterProps {
  years: number[]
  selected: number | null
  onSelect: (year: number | null) => void
}

export function YearFilter({ years, selected, onSelect }: YearFilterProps) {
  return (
    <select id="exam-year" aria-label="考試年度" value={selected ?? ""}
      onChange={(event) => onSelect(event.target.value ? Number(event.target.value) : null)}
      aria-describedby="year-download-note">
      <option value="">全部年度</option>
      {selected !== null && !years.includes(selected) && <option value={selected}>民國 {selected} 年（未收錄）</option>}
      {years.map((year) => <option key={year} value={year}>民國 {year} 年（{rocToAd(year)}）</option>)}
    </select>
  )
}
