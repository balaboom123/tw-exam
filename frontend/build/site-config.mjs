function normalizeBasePath(basePath) {
  if (!basePath || basePath === "/") return "/"
  const trimmed = String(basePath).trim()
  if (!trimmed) return "/"
  const withLeadingSlash = trimmed.startsWith("/") ? trimmed : `/${trimmed}`
  return withLeadingSlash.endsWith("/") ? withLeadingSlash : `${withLeadingSlash}/`
}

export function resolvePagesBase({ githubRepository, explicitBase } = {}) {
  if (explicitBase) return normalizeBasePath(explicitBase)
  const repoName = githubRepository?.split("/")[1]
  return repoName ? normalizeBasePath(repoName) : "/"
}
