import { InfoLayout } from "./info-layout"
import { siteHref } from "@/lib/utils"

export function AboutPage() {
  return (
    <InfoLayout
      title="關於本站"
      lead="一個非官方的歷屆試題庫，讓你少花時間找資料，多留時間準備考試。"
      active="about"
    >
      <h2>本站宗旨</h2>
      <p>
        官方試題常需要逐年、逐科查找。本站將公開試題依類科彙整為多年度
        ZIP 檔，方便考生搜尋、下載與練習；部分類科會分成多個檔案提供。
      </p>

      <h2>收錄範圍</h2>
      <p>
        可下載的類科與年度，以<a href={siteHref("")}>試題目錄</a>為準。
        你可以依分類與年度篩選，並查看各類科的檔案數量、收錄年份及來源資訊。
      </p>

      <h2>資料來源與更新</h2>
      <p>
        資料由自動化程式同步各命題機關公開的試題，再依類科彙整。
        更新進度及答案是否收錄，依原來源公開情況而定；若有記錄，可在類科資訊查看最近成功同步日期。
      </p>
      <p>
        若發現缺漏或希望新增類科，歡迎<a href={siteHref("contact.html")}>與我們聯絡</a>。
      </p>

      <h2>非官方聲明</h2>
      <p>
        本站與考選部及各命題機關無隸屬關係，不保證資料即時或完整，應考資訊請以官方公告為準。
        試題權利與使用方式依各來源條款而定，詳見<a href={siteHref("privacy.html")}>隱私權與免責聲明</a>。
      </p>
    </InfoLayout>
  )
}
