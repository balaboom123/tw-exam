import { useState } from "react"
import { Download, Lock } from "lucide-react"
import { formatYearRange, siteHref } from "@/lib/utils"
import type { Bundle } from "@/types"
import { formatSyncDate } from "@/lib/provenance"
import { withSocialAccess } from "@/lib/social-gate"

export function BundleRow({
  bundle,
  unlocked,
  joinHref,
}: {
  bundle: Bundle
  unlocked: boolean
  joinHref: string
}) {
  const [detailsOpen, setDetailsOpen] = useState(false)

  return (
    <li className="bundle-row">
      <div className="min-w-0 flex-1">
        <h3 className="text-base font-bold leading-relaxed text-ink-950">
          <a href={withSocialAccess(siteHref(`b/${bundle.id}.html`))} className="underline-offset-4 hover:underline">
            {bundle.name}
          </a>
        </h3>
        <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-600">
          <span>民國 {formatYearRange(bundle.years)}</span><span>{bundle.fileCount.toLocaleString()} 份試題</span>
        </p>
        <details className="bundle-details" onToggle={(event) => setDetailsOpen(event.currentTarget.open)}>
          <summary>科目、來源與收錄年度</summary>
          {detailsOpen && <div className="pb-2 text-xs leading-6 text-ink-600">
            <p>分類：{bundle.examClass}／{bundle.examSubclass}</p>
            {bundle.subjectLabels && bundle.subjectLabels.length > 0 && <p>科目：{bundle.subjectLabels.join("、")}</p>}
            {bundle.sources?.length ? <p>來源：{bundle.sources.map((source, index) => (
              <span key={source.url}>{index > 0 ? "、" : ""}<a href={source.url} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2 hover:text-ink-950">{source.name}</a></span>
            ))}</p> : null}
            <p>{bundle.updated ? <>最近成功同步：<time dateTime={bundle.updated}>{formatSyncDate(bundle.updated)}</time></> : "同步日期未記錄"}</p>
            <p>收錄年度（民國）：{bundle.years.join("、")}</p>
          </div>}
        </details>
      </div>
      {unlocked ? (
        <div className="bundle-actions">
          {(bundle.parts ?? [{ label: "ZIP", url: bundle.url, fileCount: bundle.fileCount }]).map((part) => (
            <a
              key={part.url}
              href={part.url}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={`下載 ${bundle.name} ${part.label}`}
              className="flex h-11 items-center justify-center gap-2 rounded-lg bg-seal-600 px-3 text-cream transition-all hover:bg-seal-700 active:translate-y-px sm:px-4"
            >
              <Download className="size-4" strokeWidth={2} />
              <span className="text-xs font-bold">
                {part.label}
              </span>
            </a>
          ))}
        </div>
      ) : (
        <div className="bundle-actions">
          <a
            href={joinHref}
            target="_blank"
            rel="noopener"
            aria-label={`加入後下載 ${bundle.name} 試題（加入 LINE 社群解鎖）`}
            onClick={(event) => {
              if (window.matchMedia("(max-width: 639px)").matches) {
                event.preventDefault()
                window.location.assign(event.currentTarget.href)
              }
            }}
            className="flex h-11 shrink-0 items-center gap-2 rounded-lg border border-line-strong bg-cream px-3 text-xs font-medium text-ink-800 transition-colors hover:bg-cream active:translate-y-px"
          >
            <Lock className="size-4" strokeWidth={2} />
            加入後下載
          </a>
        </div>
      )}
    </li>
  )
}
