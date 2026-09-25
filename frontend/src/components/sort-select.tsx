import { cn } from "@/lib/utils"

export type SortKey = "name" | "files-desc" | "years-desc"

interface SortSelectProps {
  value: SortKey
  onChange: (value: SortKey) => void
}

const options: { value: SortKey; label: string }[] = [
  { value: "name", label: "名稱" },
  { value: "files-desc", label: "檔案數" },
  { value: "years-desc", label: "年度數" },
]

export function SortSelect({ value, onChange }: SortSelectProps) {
  return (
    <div className="flex shrink-0 items-center gap-2">
      <span id="sort-label" className="text-xs text-ink-500">
        排序
      </span>
      <div
        role="group"
        aria-labelledby="sort-label"
        className="flex rounded-sm border border-line bg-paper-deep p-0.5"
      >
        {options.map((opt) => (
          <button
            type="button"
            key={opt.value}
            onClick={() => onChange(opt.value)}
            aria-pressed={value === opt.value}
            className={cn(
              "h-11 rounded-[3px] px-3 text-sm transition-colors sm:h-8",
              value === opt.value
                ? "bg-cream text-ink-950 shadow-[0_1px_2px_rgba(0,0,0,0.06)]"
                : "text-ink-500 hover:text-ink-950"
            )}
          >
            {opt.label}
          </button>
        ))}
      </div>
    </div>
  )
}
