import type { ReactNode } from "react"
import { Header, type NavKey } from "@/components/header"
import { Footer } from "@/components/footer"
import { PaperGrain } from "@/components/paper-grain"

interface InfoLayoutProps {
  title: string
  lead?: string
  ornament: string
  active?: NavKey
  children: ReactNode
}

export function InfoLayout({
  title,
  lead,
  ornament,
  active,
  children,
}: InfoLayoutProps) {
  return (
    <div className="flex min-h-[100dvh] flex-col">
      <PaperGrain />
      <Header active={active} />
      <main id="main" className="mx-auto w-full max-w-4xl flex-1 px-6 pb-16 pt-10">
        <div className="flex items-start justify-between gap-8">
          <div>
            <h1 className="font-serif text-3xl font-black tracking-tight text-ink-950 md:text-[2.5rem] md:leading-[1.15]">
              {title}
            </h1>
            {lead && (
              <p className="mt-3 max-w-[58ch] text-[15px] leading-relaxed text-ink-600">
                {lead}
              </p>
            )}
          </div>
          <span
            aria-hidden="true"
            className="hidden shrink-0 select-none border-l border-line pl-4 pt-1 font-serif text-sm tracking-[0.3em] text-ink-400 [writing-mode:vertical-rl] md:block"
          >
            {ornament}
          </span>
        </div>
        <article className="prose-info mt-10 animate-fade-in">{children}</article>
      </main>
      <Footer />
    </div>
  )
}
