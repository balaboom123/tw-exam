import { InfoLayout } from "./info-layout"
import { SOCIAL_CHANNELS } from "@/lib/social-gate"

const REPO_ISSUES_URL =
  "https://github.com/balaboom123/tw-exam/issues"
const CONTACT_EMAIL = "onlineedu666 [at] gmail [dot] com"

export function ContactPage() {
  return (
    <InfoLayout
      title="聯絡我們"
      lead="回報缺漏、建議新增類科，或加入考生社群，都可以透過以下管道。"
      ornament="聯絡我們"
      active="contact"
    >
      <h2>GitHub Issues</h2>
      <p>
        回報試題缺漏、連結失效或網站問題，最快的方式是在 GitHub 開一個
        Issue，處理進度公開可追蹤：
      </p>
      <p>
        <a href={REPO_ISSUES_URL} target="_blank" rel="noopener noreferrer">
          github.com/balaboom123/tw-exam/issues
        </a>
      </p>

      <h2>電子郵件</h2>
      <p>
        其他事項（包含著作權相關的移除請求）請來信：
        <span className="font-mono">{CONTACT_EMAIL}</span>。
        請將 [at] 改為 @、[dot] 改為 .。
      </p>

      <h2>LINE 社群</h2>
      <p>與其他考生交流考試資訊、讀書心得，歡迎加入：</p>
      <ul>
        {SOCIAL_CHANNELS.map((channel) => (
          <li key={channel.id}>
            <a href={channel.url} target="_blank" rel="noopener noreferrer">
              {channel.label}
            </a>
          </li>
        ))}
      </ul>
    </InfoLayout>
  )
}
