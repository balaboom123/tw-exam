import type { ReactNode } from "react"
import { Header, type NavKey } from "@/components/header"
import { Footer } from "@/components/footer"
import { siteHref } from "@/lib/utils"

interface InfoLayoutProps {
  title: string
  lead?: string
  active?: NavKey
  children: ReactNode
}

export function InfoLayout({ title, lead, active, children }: InfoLayoutProps) {
  return (
    <div className="flex min-h-[100dvh] flex-col">
      <Header active={active} />
      <main id="main" className="mx-auto w-full max-w-3xl flex-1 px-5 py-8 sm:px-8 sm:py-12">
        <h1 className="font-serif text-3xl font-bold leading-snug text-ink-950 sm:text-4xl">{title}</h1>
        {lead && <p className="mt-3 text-[15px] leading-7 text-ink-600">{lead}</p>}
        <article className="prose-info mt-8">{children}</article>
        <a href={siteHref("")} className="mt-8 inline-flex min-h-11 items-center text-sm text-seal-600 underline underline-offset-4">
          返回試題目錄
        </a>
      </main>
      <Footer />
    </div>
  )
}
