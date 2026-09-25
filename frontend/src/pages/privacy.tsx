import { InfoLayout } from "./info-layout"
import { siteHref } from "@/lib/utils"

export function PrivacyPage() {
  return (
    <InfoLayout
      title="隱私權與免責聲明"
      lead="本站如何處理你的資料，以及使用本站前應了解的事項。"
      ornament="隱私權政策"
      active="privacy"
    >
      <h2>個人資料</h2>
      <p>
        本站不需註冊、不設帳號，也不主動蒐集任何個人資料。搜尋與篩選皆在你的瀏覽器內完成，不會回傳伺服器。
      </p>

      <h2>瀏覽器儲存（localStorage）</h2>
      <p>
        為記錄「已開啟社群連結、可直接下載」的解鎖狀態，本站會在你的瀏覽器
        localStorage
        寫入一筆標記。該標記只存在你的裝置上，不含個人資訊，清除瀏覽器資料即可移除。
      </p>

      <h2>廣告與 Cookie</h2>
      <p>本站目前沒有載入廣告程式，也不使用廣告 Cookie。</p>

      <h2>外部連結</h2>
      <p>
        本站包含連往 GitHub、LINE
        及各命題機關網站的外部連結，各服務有其自身的隱私權政策，請自行參閱。
      </p>

      <h2>免責聲明</h2>
      <ul>
        <li>
          本站為<strong>非官方</strong>
          彙整，與考選部及各命題機關無任何隸屬或合作關係。
        </li>
        <li>試題及其他來源檔案的權利依各原權利人與來源條款而定；本站提供公開來源的彙整與鏡像。</li>
        <li>
          本站力求資料完整與正確，但不保證其即時性或完整性；應考資訊請以官方公告為準。
        </li>
        <li>
          若你是權利人並認為本站內容侵害你的權益，請
          <a href={siteHref("contact.html")}>與我們聯絡</a>
          ，我們將儘速下架相關內容。
        </li>
      </ul>

      <h2>政策修訂</h2>
      <p>本頁內容如有變更，將直接於本頁公告，不另行通知。</p>
    </InfoLayout>
  )
}
