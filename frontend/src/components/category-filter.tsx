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
          <button
            type="button"
            key={cls ?? "all"}
            onClick={() => onClassChange(cls === selectedClass ? null : cls)}
            aria-pressed={cls === selectedClass}
            disabled={cls !== null && cls !== selectedClass && !classCounts[cls]}
            className={cn("category-option", cls === selectedClass && "is-selected")}
          >
            <span>{cls ?? "全部分類"}</span>
            <span className="category-count">{(cls ? classCounts[cls] ?? 0 : total).toLocaleString()}</span>
          </button>
        ))}
      </div>
      <div className="mobile-category-control">
        <label htmlFor="exam-class">考試分類</label>
        <select id="exam-class" value={selectedClass ?? ""} onChange={(event) => onClassChange(event.target.value || null)}>
          <option value="">全部分類（{total.toLocaleString()}）</option>
          {selectedClass && !availableClasses.includes(selectedClass) && <option value={selectedClass}>{selectedClass}（未收錄）</option>}
          {availableClasses.map((cls) => (
            <option key={cls} value={cls} disabled={cls !== selectedClass && !classCounts[cls]}>
              {cls}（{(classCounts[cls] ?? 0).toLocaleString()}）
            </option>
          ))}
        </select>
      </div>
      {selectedClass && availableSubclasses.length > 0 && (
        <div className="subclass-control">
          <label htmlFor="exam-subclass">細分類</label>
          <select id="exam-subclass" value={selectedSubclass ?? ""} onChange={(event) => onSubclassChange(event.target.value || null)}>
            <option value="">全部細分類</option>
            {selectedSubclass && !availableSubclasses.includes(selectedSubclass) && <option value={selectedSubclass}>{selectedSubclass}（未收錄）</option>}
            {availableSubclasses.map((sub) => (
              <option key={sub} value={sub} disabled={sub !== selectedSubclass && !subclassCounts[sub]}>
                {sub}（{(subclassCounts[sub] ?? 0).toLocaleString()}）
              </option>
            ))}
          </select>
        </div>
      )}
    </div>
  )
}
