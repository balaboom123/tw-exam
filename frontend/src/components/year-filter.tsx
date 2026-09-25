import { cn, rocToAd } from "@/lib/utils"

interface YearFilterProps {
  years: number[]
  selected: number | null
  onSelect: (year: number | null) => void
}

export function YearFilter({ years, selected, onSelect }: YearFilterProps) {
  return (
    <>
      <select
        aria-label="考試年度"
        value={selected ?? ""}
        onChange={(event) => onSelect(event.target.value ? Number(event.target.value) : null)}
        className="hidden h-9 min-w-36 rounded-sm border border-line-strong bg-cream px-3 text-sm text-ink-800 md:block"
      >
        <option value="">全部年度</option>
        {years.map((year) => (
          <option key={year} value={year}>
            民國{year}年（西元{rocToAd(year)}年）
          </option>
        ))}
      </select>
      <div role="group" aria-label="考試年度" className="mask-fade-x flex items-center gap-1.5 overflow-x-auto pb-1 scrollbar-hide md:hidden">
        <button
          type="button"
          onClick={() => onSelect(null)}
          aria-pressed={selected === null}
          className={cn(
            "h-11 shrink-0 rounded-[3px] border px-3.5 text-sm font-medium transition-colors sm:h-9",
            selected === null
              ? "border-ink-950 bg-ink-950 text-cream"
              : "border-line bg-cream text-ink-600 hover:border-line-strong hover:text-ink-950"
          )}
        >
          全部年度
        </button>
        {years.map((year) => (
          <button
            type="button"
            key={year}
            onClick={() => onSelect(year === selected ? null : year)}
            aria-pressed={year === selected}
            className={cn(
              "h-11 shrink-0 rounded-[3px] border px-3 font-mono text-sm transition-colors sm:h-9",
              year === selected
                ? "border-ink-950 bg-ink-950 text-cream"
                : "border-line bg-cream text-ink-600 hover:border-line-strong hover:text-ink-950"
            )}
          >
            {year}
            <span className="ml-1 text-[10px]">{rocToAd(year)}</span>
          </button>
        ))}
      </div>
    </>
  )
}
