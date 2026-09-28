import { Search, X } from "lucide-react"

interface SearchBarProps {
  value: string
  onChange: (value: string) => void
}

export function SearchBar({ value, onChange }: SearchBarProps) {
  return (
    <div className="relative">
      <Search
        className="pointer-events-none absolute left-4 top-1/2 size-[18px] -translate-y-1/2 text-ink-400"
        strokeWidth={1.8}
      />
      <input
        type="search"
        autoComplete="off"
        spellCheck={false}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="搜尋類科或科目，例如：護理師、英文"
        aria-label="搜尋考試類科"
        className="search-input"
      />
      {value && (
        <button
          type="button"
          onClick={() => onChange("")}
          className="absolute right-1 top-1/2 flex size-11 -translate-y-1/2 items-center justify-center rounded-sm text-ink-500 transition-colors hover:bg-paper-deep hover:text-ink-950"
          aria-label="清除搜尋"
        >
          <X className="size-4" strokeWidth={2} />
        </button>
      )}
    </div>
  )
}
