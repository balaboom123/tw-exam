import type { Plugin } from "vite"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"
import path from "path"
import { resolvePagesBase } from "./build/site-config.ts"
import { readPublicData } from "./build/public-data.ts"
import { buildBundlePage, buildSitemap, bundlePagesCss, siteRoot } from "./build/bundle-pages.ts"

const repoRoot = path.resolve(__dirname, "..")
const frontendSourcePath = path.resolve(repoRoot, "data", "sites", "default", "frontend-bundles.json")

type PublicData = Awaited<ReturnType<typeof readPublicData>>

function servedBundlesPlugin(publicData: PublicData, root: string): Plugin {
  return {
    name: "served-bundles",
    buildStart() {
      this.addWatchFile(frontendSourcePath)
    },
    configureServer(server) {
      const feedPath = `${server.config.base}${publicData.feedFile}`.replace(/\/{2,}/g, "/")
      const searchPath = `${server.config.base}${publicData.searchFile}`.replace(/\/{2,}/g, "/")
      const pagePrefix = `${server.config.base}b/`.replace(/\/{2,}/g, "/")
      const cssPath = `${server.config.base}assets/bundle-pages.css`.replace(/\/{2,}/g, "/")
      const reloadServedBundles = (file: string) => {
        if (path.resolve(file) === frontendSourcePath) {
          server.ws.send({ type: "full-reload" })
        }
      }

      server.watcher.add(frontendSourcePath)
      server.watcher.on("add", reloadServedBundles)
      server.watcher.on("change", reloadServedBundles)
      server.watcher.on("unlink", reloadServedBundles)

      server.middlewares.use(async (req, res, next) => {
        const requestPath = req.url?.split("?")[0] ?? ""
        const isPage = requestPath.startsWith(pagePrefix) && requestPath.endsWith(".html")
        if (requestPath !== feedPath && requestPath !== searchPath && requestPath !== cssPath && !isPage) {
          next()
          return
        }

        try {
          if (requestPath === cssPath) {
            res.setHeader("Content-Type", "text/css; charset=utf-8")
            res.end(bundlePagesCss)
            return
          }
          const current = await readPublicData(frontendSourcePath)
          if (isPage) {
            const id = requestPath.slice(pagePrefix.length, -5)
            const currentFeed = current.feed
            const bundle = currentFeed.bundles.find((item) => item.id === id)
            if (!bundle) { next(); return }
            res.setHeader("Content-Type", "text/html; charset=utf-8")
            res.end(buildBundlePage(bundle, { repo: currentFeed.repo, root }))
            return
          }
          res.setHeader("Content-Type", "application/json; charset=utf-8")
          res.setHeader("Cache-Control", "no-store")
          res.end(requestPath === feedPath ? current.feedText : current.searchText)
        } catch (error) {
          res.statusCode = 500
          res.setHeader("Content-Type", "application/json; charset=utf-8")
          res.end(JSON.stringify({ error: error instanceof Error ? error.message : "Failed to load bundle data" }))
        }
      })
    },
    async generateBundle() {
      const current = await readPublicData(frontendSourcePath)
      if (current.feedFile !== publicData.feedFile || current.searchFile !== publicData.searchFile) {
        throw new Error("Frontend bundle source changed while building; restart the build")
      }
      this.emitFile({
        type: "asset",
        fileName: current.feedFile,
        source: current.feedText,
      })
      this.emitFile({
        type: "asset",
        fileName: current.searchFile,
        source: current.searchText,
      })
      const currentFeed = current.feed
      this.emitFile({ type: "asset", fileName: "assets/bundle-pages.css", source: bundlePagesCss })
      for (const bundle of currentFeed.bundles) {
        this.emitFile({
          type: "asset",
          fileName: `b/${bundle.id}.html`,
          source: buildBundlePage(bundle, { repo: currentFeed.repo, root }),
        })
      }
      this.emitFile({ type: "asset", fileName: "sitemap.xml", source: buildSitemap(currentFeed.bundles, root) })
    },
  }
}

const favicon = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='10' fill='%23a8432b'/%3E%3Ctext x='32' y='45' font-family='serif' font-size='38' font-weight='700' fill='%23faf7f0' text-anchor='middle'%3E%E8%A9%A6%3C/text%3E%3C/svg%3E"

function sharedHeadPlugin(root: string): Plugin {
  return {
    name: "shared-site-head",
    transformIndexHtml(html, context) {
      const title = html.match(/<title>([^<]+)<\/title>/)?.[1]
      const description = html.match(/<meta name="description" content="([^"]*)"\s*\/>/)?.[1]
      if (!title || !description) throw new Error(`Missing title or description in ${context.filename}`)
      const page = path.basename(context.filename)
      const canonical = new URL(page === "index.html" ? "" : page, root).href
      const tags: Array<{ tag: string; attrs?: Record<string, string>; children?: string; injectTo: "head" }> = [
        { tag: "meta", attrs: { name: "theme-color", content: "#f7f5ec" }, injectTo: "head" },
        { tag: "link", attrs: { rel: "icon", type: "image/svg+xml", href: favicon }, injectTo: "head" },
      ]
      if (page !== "404.html") {
        tags.push(
          { tag: "link", attrs: { rel: "canonical", href: canonical }, injectTo: "head" },
          { tag: "meta", attrs: { property: "og:type", content: "website" }, injectTo: "head" },
          { tag: "meta", attrs: { property: "og:locale", content: "zh_TW" }, injectTo: "head" },
          { tag: "meta", attrs: { property: "og:url", content: canonical }, injectTo: "head" },
          { tag: "meta", attrs: { property: "og:title", content: title }, injectTo: "head" },
          { tag: "meta", attrs: { property: "og:description", content: description }, injectTo: "head" },
          { tag: "meta", attrs: { property: "og:image", content: `${root}og-card.png` }, injectTo: "head" },
          { tag: "meta", attrs: { name: "twitter:card", content: "summary_large_image" }, injectTo: "head" },
        )
      }
      if (page === "index.html") {
        tags.push({
          tag: "script",
          attrs: { type: "application/ld+json" },
          children: JSON.stringify({
            "@context": "https://schema.org", "@type": "WebSite", name: "tw-exam",
            url: root,
            potentialAction: {
              "@type": "SearchAction", target: `${root}?q={search_term_string}`,
              "query-input": "required name=search_term_string",
            },
          }),
          injectTo: "head",
        })
      }
      return { html, tags }
    },
  }
}

export default defineConfig(async ({ command }) => {
  const publicData = await readPublicData(frontendSourcePath)
  const repo = publicData.feed.repo
  const explicitBase = process.env.VITE_BASE_PATH
  const base = resolvePagesBase({
    githubRepository: process.env.GITHUB_REPOSITORY,
    explicitBase,
  })
  const origin = process.env.VITE_SITE_ORIGIN || `https://${repo.split("/")[0]}.github.io`
  const canonicalBase = process.env.VITE_SITE_ORIGIN ? base : resolvePagesBase({ githubRepository: repo })
  const root = siteRoot({ base: canonicalBase, origin })
  return {
    base,
    logLevel: command === "build" ? ("warn" as const) : ("info" as const),
    define: {
      "import.meta.env.VITE_PUBLIC_BUNDLES_FILE": JSON.stringify(publicData.feedFile),
      "import.meta.env.VITE_PUBLIC_SEARCH_FILE": JSON.stringify(publicData.searchFile),
    },
    build: {
      reportCompressedSize: false,
      rollupOptions: {
        input: {
          main: path.resolve(__dirname, "index.html"),
          notFound: path.resolve(__dirname, "404.html"),
          about: path.resolve(__dirname, "about.html"),
          contact: path.resolve(__dirname, "contact.html"),
          faq: path.resolve(__dirname, "faq.html"),
          join: path.resolve(__dirname, "join.html"),
          privacy: path.resolve(__dirname, "privacy.html"),
        },
      },
    },
    plugins: [
      servedBundlesPlugin(publicData, root),
      sharedHeadPlugin(root),
      react(),
      tailwindcss(),
    ],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
  }
})
