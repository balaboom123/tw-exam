import { Download, Lock } from "lucide-react"
import { formatYearRange, siteHref } from "@/lib/utils"
import type { Bundle } from "@/types"
import { formatSyncDate } from "@/lib/provenance"
import { withSocialAccess } from "@/lib/social-gate"

const MAX_YEAR_CHIPS = 14

export function BundleRow({
  bundle,
  unlocked,
  joinHref,
}: {
  bundle: Bundle
  unlocked: boolean
  joinHref: string
}) {
  return (
    <li className="flex items-center gap-4 px-4 py-4 transition-colors hover:bg-cream">
      <div className="min-w-0 flex-1">
        <h2 className="font-serif text-[17px] font-semibold leading-snug text-ink-950">
          <a href={withSocialAccess(siteHref(`b/${bundle.id}.html`))} className="underline-offset-4 hover:underline">
            {bundle.name}
          </a>
        </h2>
        <p className="mt-1.5 font-mono text-xs text-ink-500">
          民國 {formatYearRange(bundle.years)} · {bundle.fileCount} 份試題
        </p>
        {bundle.subjectLabels && bundle.subjectLabels.length > 0 && (
          <p className="mt-1.5 text-xs leading-relaxed text-ink-600">
            <span className="font-medium text-ink-700">科目：</span>
            {bundle.subjectLabels.join("、")}
          </p>
        )}
        {bundle.sources?.length ? (
          <p className="mt-1.5 text-xs leading-relaxed text-ink-500">
            來源：{bundle.sources.map((source, index) => (
              <span key={source.url}>
                {index > 0 ? "、" : ""}
                <a href={source.url} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2 hover:text-ink-800">
                  {source.name}
                </a>
              </span>
            ))}
          </p>
        ) : null}
        <p className="mt-1 text-xs leading-relaxed text-ink-500">
          {bundle.updated ? <>最近成功同步：<time dateTime={bundle.updated}>{formatSyncDate(bundle.updated)}</time></> : "同步日期未記錄"}
        </p>
        {bundle.years.length > 2 && (
          <p className="mt-1 hidden flex-wrap gap-x-2 font-mono text-[11px] leading-relaxed text-ink-500 sm:flex">
            {bundle.years.slice(0, MAX_YEAR_CHIPS).map((y) => (
              <span key={y}>{y}</span>
            ))}
            {bundle.years.length > MAX_YEAR_CHIPS && (
              <span>+{bundle.years.length - MAX_YEAR_CHIPS}</span>
            )}
          </p>
        )}
      </div>
      {unlocked ? (
        <div className="flex shrink-0 flex-wrap justify-end gap-2">
          {(bundle.parts ?? [{ label: "ZIP", url: bundle.url, fileCount: bundle.fileCount }]).map((part) => (
            <a
              key={part.url}
              href={part.url}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={`下載 ${bundle.name} ${part.label}`}
              className="flex h-11 items-center justify-center gap-2 rounded-[3px] bg-seal-600 px-3 text-cream transition-all hover:bg-seal-700 active:translate-y-px sm:px-4"
            >
              <Download className="size-4" strokeWidth={2} />
              <span className="font-mono text-xs font-medium tracking-[0.15em]">
                {part.label}
              </span>
            </a>
          ))}
        </div>
      ) : (
        <div className="flex shrink-0 items-center">
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
            className="flex h-11 shrink-0 items-center gap-2 rounded-[3px] border border-line-strong px-3 text-xs font-medium text-ink-800 transition-colors hover:bg-cream active:translate-y-px"
          >
            <Lock className="size-4" strokeWidth={2} />
            加入後下載
          </a>
        </div>
      )}
    </li>
  )
}
