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
  const total = Object.values(classCounts).reduce((sum, count) => sum + count, 0)
  return (
    <div>
      <div role="group" aria-label="考試分類" className="category-options">
        {[null, ...availableClasses].map((cls) => (
          <button type="button" key={cls ?? "all"} onClick={() => onClassChange(cls === selectedClass ? null : cls)}
            aria-pressed={cls === selectedClass} disabled={cls !== null && cls !== selectedClass && !classCounts[cls]}
            className={cn("category-option", cls === selectedClass && "is-selected")}>
            <span>{cls ?? "全部分類"}</span>
            <span className="category-count">{(cls ? classCounts[cls] ?? 0 : total).toLocaleString()}</span>
          </button>
        ))}
      </div>
      {selectedClass && availableSubclasses.length > 0 && (
        <div role="group" aria-label="考試子分類" className="subclass-options">
          <p className="mb-2 px-2 text-xs font-bold text-ink-600">細分類</p>
          {[null, ...availableSubclasses].map((sub) => (
            <button type="button" key={sub ?? "all"} onClick={() => onSubclassChange(sub === selectedSubclass ? null : sub)}
              aria-pressed={sub === selectedSubclass} disabled={sub !== null && sub !== selectedSubclass && !subclassCounts[sub]}
              className={cn("category-option", sub === selectedSubclass && "is-selected")}>
              <span>{sub ?? "全部細分類"}</span>
              <span className="category-count">{(sub ? subclassCounts[sub] ?? 0 : Object.values(subclassCounts).reduce((sum, count) => sum + count, 0)).toLocaleString()}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
