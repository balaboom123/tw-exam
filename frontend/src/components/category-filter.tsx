import { cn } from "@/lib/utils"
interface CategoryFilterProps {
  availableClasses: string[]
  availableSubclasses: string[]
  selectedClass: string | null
  selectedSubclass: string | null
  onClassChange: (cls: string | null) => void
  onSubclassChange: (sub: string | null) => void
  classCounts: Record<string, number>
  subclassCounts: Record<string, number>
}

export function CategoryFilter({
  availableClasses,
  availableSubclasses,
  selectedClass,
  selectedSubclass,
  onClassChange,
  onSubclassChange,
  classCounts,
  subclassCounts,
}: CategoryFilterProps) {
  return (
    <div className="space-y-3">
      <div role="group" aria-label="考試分類" className="mask-fade-x flex items-center gap-1.5 overflow-x-auto pb-1 scrollbar-hide">
        <button
          type="button"
          onClick={() => onClassChange(null)}
          aria-pressed={selectedClass === null}
          className={cn(
            "h-11 shrink-0 rounded-[3px] border px-3.5 text-sm font-medium transition-colors sm:h-9",
            selectedClass === null
              ? "border-ink-950 bg-ink-950 text-cream"
              : "border-line bg-cream text-ink-600 hover:border-line-strong hover:text-ink-950"
          )}
        >
          全部分類
        </button>
        {availableClasses.map((cls) => (
          <button
            type="button"
            key={cls}
            onClick={() => onClassChange(cls === selectedClass ? null : cls)}
            aria-pressed={cls === selectedClass}
            className={cn(
              "h-11 shrink-0 rounded-[3px] border px-3.5 text-sm font-medium transition-colors sm:h-9",
              cls === selectedClass
                ? "border-ink-950 bg-ink-950 text-cream"
                : "border-line bg-cream text-ink-600 hover:border-line-strong hover:text-ink-950"
            )}
          >
            {cls}
            <span className="ml-1.5 text-[11px]">
              {classCounts[cls] ?? 0}
            </span>
          </button>
        ))}
      </div>

      {selectedClass && availableSubclasses.length > 0 && (
        <div role="group" aria-label="考試子分類" className="mask-fade-x flex items-center gap-1.5 overflow-x-auto pb-1 scrollbar-hide">
          <button
            type="button"
            onClick={() => onSubclassChange(null)}
            aria-pressed={selectedSubclass === null}
            className={cn(
              "h-11 shrink-0 rounded-[3px] border px-3 text-xs font-medium transition-colors sm:h-8",
              selectedSubclass === null
                ? "border-seal-600 bg-seal-600 text-cream"
                : "border-line bg-cream text-ink-600 hover:border-line-strong hover:text-ink-950"
            )}
          >
            全部
          </button>
          {availableSubclasses.map((sub) => (
            <button
              type="button"
              key={sub}
              onClick={() =>
                onSubclassChange(sub === selectedSubclass ? null : sub)
              }
              aria-pressed={sub === selectedSubclass}
              className={cn(
                "h-11 shrink-0 rounded-[3px] border px-3 text-xs font-medium transition-colors sm:h-8",
                sub === selectedSubclass
                  ? "border-seal-600 bg-seal-600 text-cream"
                  : "border-line bg-cream text-ink-600 hover:border-line-strong hover:text-ink-950"
              )}
            >
              {sub}
              <span className="ml-1 text-[10px]">
                {subclassCounts[sub] ?? 0}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
