import { createHash } from "node:crypto"
import { readFile } from "node:fs/promises"

function releaseLocation(rawUrl) {
  const url = new URL(rawUrl)
  const parts = url.pathname.split("/").filter(Boolean)
  if (
    url.protocol !== "https:" || url.hostname !== "github.com" ||
    url.search || url.hash || parts.length !== 6 ||
    parts[2] !== "releases" || parts[3] !== "download" ||
    !parts[5].endsWith(".zip")
  ) {
    throw new TypeError(`Expected a GitHub Release ZIP URL: ${rawUrl}`)
  }
  return {
    repo: `${parts[0]}/${parts[1]}`,
    tag: decodeURIComponent(parts[4]),
    asset: decodeURIComponent(parts[5]),
  }
}

export function buildPublicData(source) {
  if (!source || typeof source !== "object" || !Array.isArray(source.bundles) || source.bundles.length === 0) {
    throw new TypeError("Expected a nonempty site frontend bundle feed")
  }

  const bundles = []
  const searchIndex = []
  const classes = new Set()
  let repo = ""
  for (const [index, item] of source.bundles.entries()) {
    if (
      !item || typeof item.id !== "string" || !item.id ||
      typeof item.name !== "string" || !Array.isArray(item.years) ||
      !Number.isInteger(item.fileCount) || item.fileCount < 1 ||
      typeof item.examClass !== "string" || !item.examClass ||
      typeof item.examSubclass !== "string" || !item.examSubclass
    ) {
      throw new TypeError(`Invalid frontend bundle at index ${index}`)
    }
    const location = releaseLocation(item.url)
    if (repo && location.repo !== repo) {
      throw new TypeError(`Bundle ${item.id} belongs to ${location.repo}, expected ${repo}`)
    }
    repo = location.repo
    const subjectLabels = item.subjectLabels ?? []
    const searchAliases = item.searchAliases ?? []
    if (!Array.isArray(subjectLabels) || !Array.isArray(searchAliases)) {
      throw new TypeError(`Invalid search metadata for bundle ${item.id}`)
    }
    if (![...subjectLabels, ...searchAliases].every((value) => typeof value === "string")) {
      throw new TypeError(`Invalid search metadata for bundle ${item.id}`)
    }
    const bundle = {
      id: item.id,
      name: item.name,
      years: item.years,
      fileCount: item.fileCount,
      examClass: item.examClass,
      examSubclass: item.examSubclass,
      tag: location.tag,
      asset: location.asset,
    }
    if (subjectLabels.length) bundle.subjectLabels = subjectLabels
    if (item.parts !== undefined && !Array.isArray(item.parts)) {
      throw new TypeError(`Invalid parts for bundle ${item.id}`)
    }
    if (item.parts?.length) {
      bundle.parts = item.parts.map((part) => {
        if (!part || typeof part.label !== "string" || !Number.isInteger(part.fileCount) || part.fileCount < 1) {
          throw new TypeError(`Invalid part for bundle ${item.id}`)
        }
        const partLocation = releaseLocation(part.url)
        if (partLocation.repo !== repo) throw new TypeError(`Bundle part belongs to ${partLocation.repo}, expected ${repo}`)
        return { label: part.label, fileCount: part.fileCount, tag: partLocation.tag, asset: partLocation.asset }
      })
    }
    bundles.push(bundle)
    classes.add(item.examClass)
    searchIndex.push([item.name, ...searchAliases, ...subjectLabels, item.examClass, item.examSubclass].join(" ").toLowerCase())
  }
  return { feed: { v: 2, repo, classes: [...classes], bundles }, searchIndex }
}

function hashedName(prefix, source) {
  const hash = createHash("sha256").update(source).digest("hex").slice(0, 12)
  return `data/${prefix}-${hash}.json`
}

export async function readPublicData(sourcePath) {
  const source = JSON.parse(await readFile(sourcePath, "utf8"))
  const { feed, searchIndex } = buildPublicData(source)
  const feedText = JSON.stringify(feed)
  const searchText = JSON.stringify(searchIndex)
  return {
    feedText,
    searchText,
    feedFile: hashedName("bundles", feedText),
    searchFile: hashedName("search-index", searchText),
  }
}
