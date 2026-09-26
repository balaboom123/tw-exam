import { formatSyncDate, isBundleSource, isSyncTimestamp } from "../src/lib/provenance.ts"

const safeSegment = /^[A-Za-z0-9._-]+$/

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character])
}

function safeJson(value) {
  return JSON.stringify(value).replace(/</g, "\\u003c")
}

function zipUrl(repo, tag, asset) {
  return `https://github.com/${repo}/releases/download/${encodeURIComponent(tag)}/${encodeURIComponent(asset)}`
}

export const bundlePagesCss = `
:root{color-scheme:light;font-family:system-ui,"Noto Sans TC",sans-serif;background:#f9f6ee;color:#242b2b}
*{box-sizing:border-box}body{margin:0}a{color:#8b3829;text-underline-offset:3px}a:focus-visible{outline:3px solid #8b3829;outline-offset:3px}
main{max-width:760px;margin:auto;padding:32px 24px 72px}.eyebrow{font-size:.85rem;letter-spacing:.08em;color:#695d55}
h1{font-family:"Noto Serif TC",serif;font-size:clamp(1.8rem,5vw,2.8rem);line-height:1.3;margin:.65em 0 .4em}
p,li{line-height:1.8}.meta{color:#615b57}.subjects{padding-left:1.4em}.actions{display:flex;flex-wrap:wrap;gap:12px;margin:32px 0}
.button{display:inline-block;background:#a8432b;color:#fff;text-decoration:none;padding:12px 20px;border-radius:3px;font-weight:700}
.secondary{background:transparent;color:#8b3829;border:1px solid #a8432b}footer{border-top:1px solid #d6cfc0;padding-top:20px;color:#615b57;font-size:.85rem}
`

export function siteRoot({ base, origin }) {
  const url = new URL(base, origin)
  if (url.protocol !== "https:" && url.protocol !== "http:") throw new TypeError("Expected an HTTP site origin")
  return url.href.endsWith("/") ? url.href : `${url.href}/`
}

export function buildBundlePage(bundle, { repo, root }) {
  if (!safeSegment.test(bundle.id)) throw new TypeError(`Unsafe bundle ID: ${bundle.id}`)
  if (bundle.sources && (!Array.isArray(bundle.sources) || !bundle.sources.every(isBundleSource))) {
    throw new TypeError(`Invalid provenance sources for bundle ${bundle.id}`)
  }
  if (bundle.updated && !isSyncTimestamp(bundle.updated)) throw new TypeError(`Invalid sync timestamp for bundle ${bundle.id}`)
  const canonical = new URL(`b/${bundle.id}.html`, root).href
  const categoryQuery = new URLSearchParams({ class: bundle.examClass, subclass: bundle.examSubclass })
  const categoryUrl = `../?${categoryQuery}`
  const joinUrl = `../join.html?return=${encodeURIComponent(canonical)}`
  const parts = bundle.parts?.length ? bundle.parts : [bundle]
  const downloads = parts.map((part) => zipUrl(repo, part.tag, part.asset))
  const downloadButtons = parts.map((part, index) =>
    `<a class="button download" href="${escapeHtml(joinUrl)}" data-zip="${escapeHtml(downloads[index])}">加入後下載 ${escapeHtml(part.label ?? "ZIP")}</a>`,
  ).join("")
  const title = `${bundle.name} 歷屆試題 ZIP 下載 | tw-exam`
  const description = `${bundle.name}歷屆試題，收錄民國 ${bundle.years.join("、")} 年，共 ${bundle.fileCount} 份檔案。`
  const structuredData = [
    {
      "@context": "https://schema.org", "@type": "BreadcrumbList",
      itemListElement: [
        { "@type": "ListItem", position: 1, name: "歷屆試題", item: root },
        { "@type": "ListItem", position: 2, name: bundle.name, item: canonical },
      ],
    },
    {
      "@context": "https://schema.org", "@type": "Dataset",
      name: bundle.name, description, url: canonical,
      ...(bundle.sources?.length ? { isBasedOn: bundle.sources.map((source) => source.url) } : {}),
      keywords: [bundle.examClass, bundle.examSubclass, ...(bundle.subjectLabels ?? [])],
      distribution: downloads.map((url) => ({
        "@type": "DataDownload", contentUrl: url, encodingFormat: "application/zip",
      })),
    },
  ]
  const yearItems = bundle.years.map((year) => `<li>民國 ${escapeHtml(year)} 年</li>`).join("")
  const subjectItems = (bundle.subjectLabels ?? []).map((subject) => `<li>${escapeHtml(subject)}</li>`).join("")
  const cssUrl = "../assets/bundle-pages.css"
  const sources = (bundle.sources ?? []).map((source) => `<a href="${escapeHtml(source.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(source.name)}</a>`).join("、")
  const syncDate = bundle.updated
    ? `最近成功同步：<time datetime="${escapeHtml(bundle.updated)}">${escapeHtml(formatSyncDate(bundle.updated))}</time>`
    : "同步日期未記錄"
  return `<!doctype html>
<html lang="zh-Hant"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${escapeHtml(title)}</title><meta name="description" content="${escapeHtml(description)}">
<link rel="canonical" href="${escapeHtml(canonical)}"><link rel="stylesheet" href="${escapeHtml(cssUrl)}">
<meta property="og:type" content="article"><meta property="og:title" content="${escapeHtml(title)}"><meta property="og:description" content="${escapeHtml(description)}"><meta property="og:url" content="${escapeHtml(canonical)}"><meta property="og:image" content="${escapeHtml(new URL('og-card.png', root).href)}"><meta name="twitter:card" content="summary_large_image">
<script type="application/ld+json">${safeJson(structuredData)}</script></head>
<body><main><nav aria-label="麵包屑"><a href="../">歷屆試題</a> / ${escapeHtml(bundle.name)}</nav>
<p class="eyebrow">${escapeHtml(bundle.examClass)} · ${escapeHtml(bundle.examSubclass)}</p><h1>${escapeHtml(bundle.name)}</h1>
<p class="meta">${escapeHtml(description)}</p>
${sources ? `<p class="meta">來源：${sources}</p>` : ""}<p class="meta">${syncDate}</p>
<h2>收錄年度</h2><ul>${yearItems}</ul>
${subjectItems ? `<h2>科目</h2><ul class="subjects">${subjectItems}</ul>` : ""}
<div class="actions">${downloadButtons}
<a class="button secondary" href="${escapeHtml(categoryUrl)}">瀏覽同類試題</a></div>
<footer>資料來自官方公開考試來源；試題權利及使用條款依各來源公告。<a href="https://github.com/${escapeHtml(repo)}/blob/main/DATA-LICENSE.md">資料使用說明</a> · <a href="../">返回 tw-exam</a></footer></main>
<script>const keys=["taiwan-exam-download-access","taiwan-exam-download-access:public-service","taiwan-exam-download-access:cap","taiwan-exam-download-access:gsat-ast"];let unlocked=false;for(const name of ["localStorage","sessionStorage"]){try{if(keys.some(k=>window[name].getItem(k)==="1"))unlocked=true}catch{}}if(unlocked)for(const a of document.querySelectorAll(".download")){a.href=a.dataset.zip;a.textContent=a.textContent.replace("加入後下載","下載")}</script>
</body></html>`
}

export function buildSitemap(bundles, root) {
  const paths = ["", "about.html", "contact.html", "faq.html", "privacy.html", ...bundles.map((bundle) => `b/${bundle.id}.html`)]
  return `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${paths.map((path) => `<url><loc>${escapeHtml(new URL(path, root).href)}</loc></url>`).join("\n")}\n</urlset>\n`
}
