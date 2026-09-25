import { cn, siteHref } from "@/lib/utils"

export type NavKey = "about" | "faq" | "contact" | "privacy"

const NAV: { key: NavKey; href: string; label: string }[] = [
  { key: "about", href: "about.html", label: "關於" },
  { key: "faq", href: "faq.html", label: "常見問題" },
  { key: "contact", href: "contact.html", label: "聯絡" },
]

export function Header({
  totalBundles,
  active,
}: {
  totalBundles?: number
  active?: NavKey
}) {
  return (
    <header className="sticky top-0 z-10 border-t-[3px] border-b border-t-ink-950 border-b-line bg-paper/95 backdrop-blur-sm">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded-[3px] focus:bg-ink-950 focus:px-4 focus:py-2.5 focus:text-sm focus:text-cream"
      >
        跳至主要內容
      </a>
      <div className="mx-auto flex h-16 max-w-4xl items-center gap-3 px-6">
        <a href={siteHref("")} className="flex min-w-0 items-center gap-3">
          <span
            aria-hidden="true"
            className="flex size-9 shrink-0 select-none items-center justify-center rounded-[4px] bg-seal-600 font-serif text-lg font-bold text-cream"
          >
            試
          </span>
          <div className="min-w-0">
            <span className="font-serif text-[17px] font-bold leading-tight tracking-wide text-ink-950">
              tw-exam
            </span>
            <p className="text-[10px] leading-tight tracking-[0.22em] text-ink-500">
              台灣歷屆試題庫
            </p>
          </div>
        </a>
        <nav
          aria-label="網站導覽"
          className="ml-auto hidden items-center gap-1 sm:flex"
        >
          {NAV.map((item) => (
            <a
              key={item.key}
              href={siteHref(item.href)}
              aria-current={active === item.key ? "page" : undefined}
              className={cn(
                "flex h-11 items-center px-2.5 text-[13px] transition-colors",
                active === item.key
                  ? "font-medium text-ink-950 underline decoration-line-strong underline-offset-8"
                  : "text-ink-600 hover:text-ink-950"
              )}
            >
              {item.label}
            </a>
          ))}
        </nav>
        {totalBundles !== undefined && totalBundles > 0 && (
          <span className="ml-auto shrink-0 font-mono text-xs text-ink-500 sm:ml-3 sm:border-l sm:border-line sm:pl-3">
            {totalBundles.toLocaleString()} 類科
          </span>
        )}
      </div>
    </header>
  )
}
