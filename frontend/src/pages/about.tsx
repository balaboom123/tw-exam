import { ArrowUpRight, Download, Search, Users } from "lucide-react"
import { InfoLayout } from "./info-layout"
import { siteHref } from "@/lib/utils"

export function AboutPage() {
  return (
    <InfoLayout title="關於本站" lead="少一點蒐集試題的時間，多一點準備考試的餘裕。" active="about" prose={false}>
      <section className="about-mission" aria-labelledby="mission-title">
        <span className="info-badge">非官方試題彙整</span>
        <h2 id="mission-title">把分散的歷屆試題，<br />整理成你的練習起點。</h2>
        <p>官方試題常需要逐年、逐科查找。tw-exam 將公開試題依類科彙整，讓你先找到要準備的考試，再一次下載收錄的歷年資料。</p>
        <a href={siteHref("")} className="info-primary-link">尋找我的考試<ArrowUpRight aria-hidden="true" className="size-4" /></a>
      </section>

      <section className="info-section" aria-labelledby="start-title">
        <h2 id="start-title">從找到類科，到開始練習</h2>
        <ol className="getting-started">
          <li><Search aria-hidden="true" /><h3>搜尋你的考試</h3><p>輸入類科或科目，再用分類與年度縮小範圍。</p></li>
          <li><Users aria-hidden="true" /><h3>開啟加入頁</h3><p>首次下載先選擇一個 LINE 社群，再返回試題列表。</p></li>
          <li><Download aria-hidden="true" /><h3>下載並解壓縮</h3><p>取得多年度 ZIP 檔；如有分卷，依需要下載各部分。</p></li>
        </ol>
      </section>

      <section className="info-section" aria-labelledby="collection-scope">
        <h2 id="collection-scope">先確認收錄，再安排練習</h2>
        <div className="about-facts">
          <div><h3>收錄哪些考試？</h3><p>可下載的類科與年度，以<a href={siteHref("")}>試題目錄</a>為準。你可以查看年度範圍、檔案數量，以及各類科的來源說明。</p></div>
          <div><h3>資料從哪裡來？</h3><p>試題取自命題機關公開資料，依類科彙整提供下載。答案是否收錄，依原來源公開的內容而定。</p></div>
          <div><h3>何時會有新試題？</h3><p>資料由自動化程式同步，收錄進度依來源公開情況而定。若有記錄，可在類科的來源資訊查看最近成功同步日期。</p></div>
        </div>
      </section>

      <aside className="info-callout" aria-label="非官方聲明">
        <h2>考試資訊，仍請以官方公告為準</h2>
        <p>本站與考選部及各命題機關無隸屬關係，不保證資料即時或完整。試題權利與使用方式依各來源條款而定。</p>
        <a href={siteHref("privacy.html")} className="info-text-link">隱私權與免責聲明<ArrowUpRight aria-hidden="true" className="size-4" /></a>
      </aside>
      <div className="info-next-step"><p>找不到類科，或發現檔案有問題？</p><a href={siteHref("contact.html")} className="info-text-link">告訴我們<ArrowUpRight aria-hidden="true" className="size-4" /></a></div>
    </InfoLayout>
  )
}
