import { useState } from "react"
import { ArrowUpRight, ChevronDown, Search, X } from "lucide-react"
import { InfoLayout } from "./info-layout"
import { siteHref } from "@/lib/utils"
import { FAQ_GROUPS } from "@/lib/faq-content"


export function FaqPage() {
  const [query, setQuery] = useState("")
  const search = query.trim().toLowerCase()
  const groups = FAQ_GROUPS.map((group) => ({
    ...group,
    questions: group.questions.filter((question) =>
      `${group.title} ${question.title} ${question.paragraphs.join(" ")}`.toLowerCase().includes(search)),
  })).filter((group) => group.questions.length > 0)
  const count = groups.reduce((total, group) => total + group.questions.length, 0)

  return (
    <InfoLayout title="常見問題" lead="從第一次下載，到年度與檔案說明，在這裡找到答案。" active="faq" prose={false}>
      <div role="search" aria-label="搜尋常見問題" className="faq-search">
        <Search aria-hidden="true" className="pointer-events-none absolute left-4 top-[19px] size-5 text-ink-500" />
        <input type="search" aria-label="搜尋常見問題" placeholder="搜尋問題，例如：下載、年度、答案"
          value={query} onChange={(event) => setQuery(event.target.value)} className="search-input" />
        {query && <button type="button" onClick={() => setQuery("")} aria-label="清除問題搜尋" className="faq-clear"><X aria-hidden="true" className="size-4" /></button>}
      </div>
      <p role="status" className="mt-3 text-xs text-ink-600">{search ? `找到 ${count} 個相關問題` : "選擇問題展開說明，也可以輸入關鍵字搜尋。"}</p>
      {groups.map((group) => (
        <section key={group.title} className="faq-group" aria-label={group.title}>
          <h2>{group.title}</h2>
          <div className="faq-questions">
            {group.questions.map((question) => (
              <details key={question.id} id={question.id} className="faq-question" open={search ? true : undefined}>
                <summary>{question.title}<ChevronDown aria-hidden="true" className="size-4 shrink-0" /></summary>
                <div className="faq-answer">{question.paragraphs.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}</div>
              </details>
            ))}
          </div>
        </section>
      ))}
      {count === 0 && <div className="faq-empty"><h2>沒有找到相關問題</h2><p>試試「下載」、「年度」或「檔案」，也可以直接聯絡我們。</p><button type="button" onClick={() => setQuery("")} className="info-primary-link">查看所有問題</button></div>}
      <div className="info-next-step"><div><h2>還沒找到答案？</h2><p>告訴我們你遇到的問題，並附上相關頁面。</p></div><a href={siteHref("contact.html")} className="info-text-link">聯絡我們<ArrowUpRight aria-hidden="true" className="size-4" /></a></div>
    </InfoLayout>
  )
}
