import { InfoLayout } from "./info-layout"
import { siteHref } from "@/lib/utils"

export function NotFoundPage() {
  return (
    <InfoLayout title="找不到頁面" ornament="找不到頁面">
      <p>這個頁面不存在，或網址已經變更。</p>
      <p>
        <a href={siteHref("")}>返回試題目錄</a>
      </p>
    </InfoLayout>
  )
}
