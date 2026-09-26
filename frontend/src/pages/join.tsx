import { useEffect } from "react"
import { ExternalLink } from "lucide-react"
import { PaperGrain } from "@/components/paper-grain"
import { SOCIAL_CHANNELS, grantSocialAccess, withSocialAccess } from "@/lib/social-gate"
import { siteHref } from "@/lib/utils"

/** Splits "群組名— 說明文字" into its description half. */
function channelNote(label: string) {
  return label.replace(/^[^—-]+[—-]\s*/, "")
}

function returnHref(): string {
  const siteRoot = siteHref("")
  const requested = new URLSearchParams(window.location.search).get("return")
  if (!requested) return siteRoot
  try {
    const url = new URL(requested, window.location.origin)
    if (url.origin === window.location.origin && url.pathname.startsWith(siteRoot)) {
      return `${url.pathname}${url.search}${url.hash}`
    }
  } catch {
    // Ignore malformed return URLs.
  }
  return siteRoot
}

/** Linktree-style gate page: landing here unlocks downloads site-wide. */
export function JoinPage() {
  useEffect(() => {
    grantSocialAccess()
  }, [])

  return (
    <div className="flex min-h-[100dvh] flex-col items-center px-6 py-16">
      <PaperGrain />
      <main className="w-full max-w-sm animate-fade-in text-center">
        <span className="mx-auto flex size-14 items-center justify-center rounded-[4px] bg-seal-600 font-serif text-3xl font-bold text-cream">
          試
        </span>
        <h1 className="mt-5 font-serif text-2xl font-black tracking-wide text-ink-950">
          加入 LINE 社群
        </h1>
        <p className="mt-3 text-sm leading-relaxed text-ink-600">
          加入任一個社群即可解鎖全站試題下載，
          也歡迎在社群交流考試資訊與讀書心得。
        </p>
        <ul className="mt-8 space-y-3">
          {SOCIAL_CHANNELS.map((channel) => (
            <li key={channel.id}>
              <a
                href={channel.url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex min-h-14 w-full flex-col items-center justify-center gap-0.5 rounded-[3px] border border-line-strong bg-cream px-4 py-3 transition-colors hover:bg-paper-deep active:translate-y-px"
              >
                <span className="flex items-center gap-1.5 text-sm font-bold text-ink-950">
                  {channel.shortLabel}
                  <ExternalLink
                    aria-hidden="true"
                    className="size-3.5 text-ink-400"
                    strokeWidth={2}
                  />
                </span>
                <span className="text-xs text-ink-500">
                  {channelNote(channel.label)}
                </span>
              </a>
            </li>
          ))}
        </ul>
        <p className="mt-8 border-t border-line pt-5 text-xs leading-relaxed text-ink-500">
          已加入？返回試題列表即可直接下載。
        </p>
        <a
          href={returnHref()}
          onClick={(event) => {
            if (!grantSocialAccess()) {
              event.currentTarget.href = withSocialAccess(returnHref(), true)
            }
          }}
          className="mt-2 inline-block text-xs font-medium text-ink-800 underline decoration-line-strong underline-offset-4 transition-colors hover:text-ink-950"
        >
          返回試題列表
        </a>
        <details className="mt-5 text-xs text-ink-500">
          <summary className="cursor-pointer">返回後仍顯示鎖定？</summary>
          <p className="mt-2 leading-relaxed">
            請點選上方返回連結；即使瀏覽器阻止儲存網站資料，也能繼續下載。
          </p>
        </details>
      </main>
    </div>
  )
}
