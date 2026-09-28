import { InfoLayout } from "./info-layout"
import { SOCIAL_CHANNELS } from "@/lib/social-gate"

const REPO_ISSUES_URL = "https://github.com/balaboom123/tw-exam/issues"
const CONTACT_EMAIL = "onlineedu666@gmail.com"

export function ContactPage() {
  return (
    <InfoLayout
      title="聯絡我們"
      lead="回報試題缺漏、提出建議，或加入考生社群。"
      active="contact"
    >
      <h2>GitHub Issues</h2>
      <p>
        試題缺漏、連結失效或網站問題，歡迎在 GitHub 回報，處理進度公開可追蹤。
        請附上考試名稱、年度及相關頁面連結。
      </p>
      <p>
        <a href={REPO_ISSUES_URL} target="_blank" rel="noopener noreferrer">前往 GitHub Issues</a>
      </p>

      <h2>電子郵件</h2>
      <p>其他事項（包含著作權相關的移除請求）請來信：</p>
      <p><a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a></p>

      <h2>LINE 社群</h2>
      <p>與其他考生交流考試資訊、用書與讀書心得：</p>
      <ul>
        {SOCIAL_CHANNELS.map((channel) => (
          <li key={channel.id}>
            <a href={channel.url} target="_blank" rel="noopener noreferrer">{channel.label}</a>
          </li>
        ))}
      </ul>
    </InfoLayout>
  )
}
