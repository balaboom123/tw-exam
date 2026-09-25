import { ChevronLeft, ChevronRight } from "lucide-react"
import { cn } from "@/lib/utils"

interface PaginationProps {
  current: number
  total: number
  onChange: (page: number) => void
}

export function Pagination({ current, total, onChange }: PaginationProps) {
  if (total <= 1) return null

  const pages: (number | "...")[] = []
  if (total <= 7) {
    for (let i = 1; i <= total; i++) pages.push(i)
  } else {
    pages.push(1)
    if (current > 3) pages.push("...")
    for (
      let i = Math.max(2, current - 1);
      i <= Math.min(total - 1, current + 1);
      i++
    ) {
      pages.push(i)
    }
    if (current < total - 2) pages.push("...")
    pages.push(total)
  }

  return (
    <nav
      className="flex items-center justify-center gap-1 pb-4 pt-8"
      aria-label="分頁"
    >
      <button
        type="button"
        onClick={() => onChange(current - 1)}
        disabled={current === 1}
        className="flex size-11 items-center justify-center rounded-[3px] border border-line text-ink-600 transition-colors hover:bg-cream hover:text-ink-950 disabled:pointer-events-none disabled:opacity-30"
        aria-label="上一頁"
      >
        <ChevronLeft className="size-4" strokeWidth={2} />
      </button>
      {pages.map((p, i) =>
        p === "..." ? (
          <span
            key={`dots-${i}`}
            className="flex size-11 items-center justify-center font-mono text-sm text-ink-400"
          >
            …
          </span>
        ) : (
          <button
            type="button"
            key={p}
            onClick={() => onChange(p)}
            aria-current={p === current ? "page" : undefined}
            className={cn(
              "size-11 rounded-[3px] font-mono text-sm transition-colors",
              p === current
                ? "bg-ink-950 text-cream"
                : "text-ink-600 hover:bg-paper-deep hover:text-ink-950"
            )}
          >
            {p}
          </button>
        )
      )}
      <button
        type="button"
        onClick={() => onChange(current + 1)}
        disabled={current === total}
        className="flex size-11 items-center justify-center rounded-[3px] border border-line text-ink-600 transition-colors hover:bg-cream hover:text-ink-950 disabled:pointer-events-none disabled:opacity-30"
        aria-label="下一頁"
      >
        <ChevronRight className="size-4" strokeWidth={2} />
      </button>
    </nav>
  )
}
