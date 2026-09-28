import type { ReactNode } from "react"
import { ArrowLeft, ArrowUpRight } from "lucide-react"
import { Header, type NavKey } from "@/components/header"
import { Footer } from "@/components/footer"
import { cn, siteHref } from "@/lib/utils"

interface InfoLayoutProps {
  title: string
  lead?: string
  active?: NavKey
  prose?: boolean
  children: ReactNode
}

const INFO_NAV = [
  { key: "about", label: "關於本站", href: "about.html" },
  { key: "faq", label: "常見問題", href: "faq.html" },
  { key: "contact", label: "聯絡我們", href: "contact.html" },
  { key: "privacy", label: "隱私權與免責聲明", href: "privacy.html" },
] as const

export function InfoLayout({ title, lead, active, prose = true, children }: InfoLayoutProps) {
  return (
    <div className="flex min-h-[100dvh] flex-col">
      <Header active={active} />
      <main id="main" className="workspace-shell info-shell flex-1">
        <a href={siteHref("")} className="info-back-link">
          <ArrowLeft aria-hidden="true" className="size-4" />返回試題目錄
        </a>
        <div className="info-intro">
          <h1 className="collection-title">{title}</h1>
          {lead && <p className="mt-3 max-w-[48em] text-sm leading-7 text-ink-600 sm:text-[15px]">{lead}</p>}
        </div>
        <div className="info-layout">
          <aside className="info-sidebar">
            <nav aria-label="使用指南" className="info-nav">
              {INFO_NAV.map((item) => (
                <a key={item.key} href={siteHref(item.href)}
                  aria-current={active === item.key ? "page" : undefined}
                  className={cn("info-nav-link", active === item.key && "is-active")}>
                  {item.label}
                </a>
              ))}
            </nav>
            <div className="info-sidebar-note">
              <p className="text-sm font-bold text-ink-950">準備好開始練習？</p>
              <p className="mt-2 text-xs leading-6 text-ink-600">依類科與年度找試題，將歷年資料一起帶走。</p>
              <a href={siteHref("")} className="info-text-link mt-2">前往試題目錄<ArrowUpRight aria-hidden="true" className="size-4" /></a>
            </div>
          </aside>
          <article className={cn("info-content", prose && "prose-info info-prose-panel")}>{children}</article>
        </div>
      </main>
      <Footer />
    </div>
  )
}
