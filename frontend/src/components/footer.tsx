import { siteHref } from "@/lib/utils"

const NAV: { href: string; label: string }[] = [
  { href: "about.html", label: "關於本站" },
  { href: "faq.html", label: "常見問題" },
  { href: "contact.html", label: "聯絡我們" },
  { href: "privacy.html", label: "隱私權與免責聲明" },
]

export function Footer() {
  return (
    <footer className="border-t border-line">
      <div className="mx-auto max-w-4xl px-6 py-6">
        <nav
          aria-label="網站資訊"
          className="flex flex-wrap items-center gap-x-6 gap-y-0"
        >
          {NAV.map((item) => (
            <a
              key={item.href}
              href={siteHref(item.href)}
              className="flex min-h-11 items-center text-[13px] text-ink-600 transition-colors hover:text-ink-950"
            >
              {item.label}
            </a>
          ))}
          <a
            href="https://github.com/balaboom123/tw-exam"
            target="_blank"
            rel="noopener noreferrer"
            className="flex min-h-11 items-center font-mono text-xs text-ink-600 transition-colors hover:text-ink-950"
          >
            GitHub
          </a>
        </nav>
        <div className="mt-3 border-t border-line pt-4 text-xs leading-relaxed text-ink-500">
          <p>
            資料來源：考選部、大考中心、國中教育會考、國營事業甄試、技能檢定及各教師甄選單位之公開資料。
          </p>
          <p className="mt-1.5">
            來源明示採用{" "}
            <a href="https://data.gov.tw/license" target="_blank" rel="noopener noreferrer" className="underline underline-offset-2 hover:text-ink-800">
              政府資料開放授權條款
            </a>
            者，使用時請標明原提供機關；其他資料依來源條款使用。
          </p>
          <p className="mt-1.5">
            本站為非官方彙整；試題權利及使用條款依各來源公告。詳見{" "}
            <a
              href="https://github.com/balaboom123/tw-exam/blob/main/DATA-LICENSE.md"
              target="_blank"
              rel="noopener noreferrer"
              className="underline underline-offset-2 hover:text-ink-800"
            >
              資料使用說明
            </a>。
          </p>
        </div>
      </div>
    </footer>
  )
}
