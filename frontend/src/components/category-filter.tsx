import { useId, useState } from "react"
import { ChevronDown, ChevronRight } from "lucide-react"
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
  const [collapsedClass, setCollapsedClass] = useState<string | null>(null)
  const submenuId = useId()
  const total = Object.values(classCounts).reduce((sum, count) => sum + count, 0)
  // Keep filters from shared URLs visible even if they are no longer in the feed.
  const classes = selectedClass && !availableClasses.includes(selectedClass)
    ? [...availableClasses, selectedClass] : availableClasses
  const subclasses = selectedSubclass && !availableSubclasses.includes(selectedSubclass)
    ? [...availableSubclasses, selectedSubclass] : availableSubclasses

  function handleClassClick(cls: string | null) {
    if (cls && cls === selectedClass) {
      setCollapsedClass(collapsedClass === cls ? null : cls)
    } else {
      setCollapsedClass(null)
      onClassChange(cls)
    }
  }

  return (
    <div role="group" aria-label="考試分類" className="category-options">
      {[null, ...classes].map((cls) => {
        const selected = cls === selectedClass
        const hasSubmenu = Boolean(cls && selected && subclasses.length)
        const expanded = hasSubmenu && collapsedClass !== cls
        const Chevron = expanded ? ChevronDown : ChevronRight
        return (
          <div key={cls ?? "all"}>
            <button
              type="button"
              onClick={() => handleClassClick(cls)}
              aria-pressed={selected}
              aria-expanded={cls ? expanded : undefined}
              aria-controls={hasSubmenu ? submenuId : undefined}
              disabled={cls !== null && !selected && !classCounts[cls]}
              className={cn("category-option", selected && (cls ? "is-parent-selected" : "is-selected"))}
            >
              <span className="category-label">{cls ?? "全部分類"}</span>
              <span className="category-trailing">
                <span className="category-count">{(cls ? classCounts[cls] ?? 0 : total).toLocaleString()}</span>
                {cls ? <Chevron aria-hidden="true" className="size-3" /> : <span aria-hidden="true" className="size-3" />}
              </span>
            </button>
            {hasSubmenu && (
              <div id={submenuId} role="group" aria-label={`${cls}細分類`} className="subcategory-options" hidden={!expanded}>
                {[null, ...subclasses].map((sub) => (
                  <button
                    type="button"
                    key={sub ?? "all"}
                    onClick={() => onSubclassChange(sub)}
                    aria-pressed={sub === selectedSubclass}
                    disabled={sub !== null && sub !== selectedSubclass && !subclassCounts[sub]}
                    className={cn("category-option", sub === selectedSubclass && "is-selected")}
                  >
                    <span className="category-label">{sub ?? "全部"}</span>
                    <span className="category-count">{(sub ? subclassCounts[sub] ?? 0 : classCounts[cls!] ?? 0).toLocaleString()}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
