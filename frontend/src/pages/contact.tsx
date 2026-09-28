import { useEffect, useRef, useState } from "react"
import { ArrowUpRight, Check, Copy, Mail, MessageSquare } from "lucide-react"
import { InfoLayout } from "./info-layout"
import { SOCIAL_CHANNELS } from "@/lib/social-gate"

const REPO_ISSUES_URL = "https://github.com/balaboom123/tw-exam/issues"
const CONTACT_EMAIL = "onlineedu666@gmail.com"
const ISSUE_TEMPLATE = "考試名稱：\n年度：\n頁面或官方來源連結：\n\n問題描述與重現步驟：\n"
const NEW_ISSUE_URL = `${REPO_ISSUES_URL}/new?body=${encodeURIComponent(ISSUE_TEMPLATE)}`

export function ContactPage() {
  const [copyStatus, setCopyStatus] = useState("")
  const timeoutRef = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timeoutRef.current), [])

  async function copyEmail() {
    window.clearTimeout(timeoutRef.current)
    try {
      await navigator.clipboard.writeText(CONTACT_EMAIL)
      setCopyStatus("電子郵件已複製")
    } catch {
      setCopyStatus("請選取上方電子郵件地址並複製")
    }
    timeoutRef.current = window.setTimeout(() => setCopyStatus(""), 4000)
  }

  return (
    <InfoLayout title="聯絡我們" lead="回報試題缺漏、提出建議，或找到一起準備考試的同伴。" active="contact" prose={false}>
      <section className="contact-report" aria-labelledby="report-title">
        <div className="contact-section-heading"><MessageSquare aria-hidden="true" className="size-5" /><h2 id="report-title">試題或網站出了問題？</h2></div>
        <p>使用 GitHub Issues 回報缺漏、下載失敗或標示錯誤，處理進度可以公開追蹤。</p>
        <div className="contact-report-fields">
          <h3>附上這些資訊，更容易確認問題</h3>
          <ul><li>考試名稱與民國年度</li><li>有問題的頁面或官方來源連結</li><li>錯誤訊息與重現步驟</li></ul>
          <p>回報表單已準備上述欄位，開啟後填寫即可。</p>
        </div>
        <div className="contact-actions">
          <a href={NEW_ISSUE_URL} target="_blank" rel="noopener noreferrer" className="info-primary-link">在 GitHub 回報<ArrowUpRight aria-hidden="true" className="size-4" /></a>
          <a href={REPO_ISSUES_URL} target="_blank" rel="noopener noreferrer" className="info-text-link">查看已有回報<ArrowUpRight aria-hidden="true" className="size-4" /></a>
        </div>
        <p className="contact-caption">需要 GitHub 帳號；沒有帳號也可以使用電子郵件。</p>
      </section>

      <section className="info-section" aria-labelledby="email-title">
        <div className="contact-section-heading"><Mail aria-hidden="true" className="size-5" /><h2 id="email-title">其他事項，用電子郵件聯絡</h2></div>
        <p className="info-body-copy">一般建議或著作權相關的移除請求，請寄信並附上相關頁面與說明。</p>
        <div className="contact-email">
          <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>
          <button type="button" onClick={copyEmail} aria-label="複製電子郵件地址" className="copy-email">
            {copyStatus === "電子郵件已複製" ? <Check aria-hidden="true" className="size-4" /> : <Copy aria-hidden="true" className="size-4" />}複製
          </button>
        </div>
        <p role="status" className="min-h-6 pt-1 text-xs text-ink-600">{copyStatus}</p>
      </section>

      <section className="info-section" aria-labelledby="community-title">
        <h2 id="community-title">找到一起準備的同伴</h2>
        <p className="info-body-copy">加入 LINE 社群，交流考試資訊、用書與讀書心得。</p>
        <ul className="community-links">
          {SOCIAL_CHANNELS.map((channel) => (
            <li key={channel.id}>
              <a href={channel.url} target="_blank" rel="noopener noreferrer" aria-label={`加入 ${channel.shortLabel} LINE 社群`}>
                <span><strong>{channel.shortLabel}</strong><span>{channel.label}</span></span>
                <ArrowUpRight aria-hidden="true" className="size-5 shrink-0" />
              </a>
            </li>
          ))}
        </ul>
      </section>
    </InfoLayout>
  )
}
