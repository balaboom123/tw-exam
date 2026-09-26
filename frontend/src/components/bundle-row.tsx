import { Download, Lock } from "lucide-react"
import { formatYearRange, siteHref } from "@/lib/utils"
import type { Bundle } from "@/types"

const MAX_YEAR_CHIPS = 14
const SOURCE_NOTES: Record<string, string> = {
  "teacher-qual": "來源：教育部教師資格考試歷屆試題，全國資格考試。",
  "teacher-recruit-newtaipei": "來源：新北市教育人員聯合甄選公告 API，目前收錄 115 學年度。",
  "teacher-recruit-taoyuan-elementary": "來源：桃園市國小教師甄選網，目前收錄 115 學年度。",
  "teacher-recruit-kaohsiung": "來源：高雄市國小與特教教師甄選官方網站，目前收錄 115 學年度。",
  "teacher-recruit-central-alliance": "來源：中區策略聯盟試題疑義網站，官方縣市甄選系統指向此站，目前收錄 115 學年度。",
  "teacher-recruit-tainan": "來源：臺南市國小教師甄選網，目前僅 115 學年度公開 ZIP。",
  "teacher-recruit-taipei-junior": "來源：臺北市教育局公告，目前僅 113–114 學年度國中聯甄。",
}

export function BundleRow({
  bundle,
  unlocked,
  joinHref,
}: {
  bundle: Bundle
  unlocked: boolean
  joinHref: string
}) {
  const sourceNote = SOURCE_NOTES[bundle.id]

  return (
    <li className="flex items-center gap-4 px-4 py-4 transition-colors hover:bg-cream">
      <div className="min-w-0 flex-1">
        <h3 className="font-serif text-[17px] font-semibold leading-snug text-ink-950">
          <a href={siteHref(`b/${bundle.id}.html`)} className="underline-offset-4 hover:underline">
            {bundle.name}
          </a>
        </h3>
        <p className="mt-1.5 font-mono text-xs text-ink-500">
          民國 {formatYearRange(bundle.years)} · {bundle.fileCount} 份試題
        </p>
        {bundle.subjectLabels && bundle.subjectLabels.length > 0 && (
          <p className="mt-1.5 text-xs leading-relaxed text-ink-600">
            <span className="font-medium text-ink-700">科目：</span>
            {bundle.subjectLabels.join("、")}
          </p>
        )}
        {sourceNote && (
          <p className="mt-1.5 text-xs leading-relaxed text-ink-500">
            {sourceNote}
          </p>
        )}
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
            aria-label={`加入 LINE 社群，解鎖 ${bundle.name} 試題下載`}
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
