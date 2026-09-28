import { Fragment } from "react"
import { InfoLayout } from "./info-layout"
import { siteHref } from "@/lib/utils"
import { FAQ_ITEMS } from "@/lib/faq-content"

export function FaqPage() {
  return (
    <InfoLayout
      title="常見問題"
      lead="下載方式、年度與檔案格式的簡單說明。"
      active="faq"
    >
      {FAQ_ITEMS.map((question) => (
        <Fragment key={question.id}>
          <h2 id={question.id}>{question.title}</h2>
          <p>{question.answer}</p>
        </Fragment>
      ))}
      <p>其他問題，歡迎<a href={siteHref("contact.html")}>與我們聯絡</a>。</p>
    </InfoLayout>
  )
}
