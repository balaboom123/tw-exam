export interface BundlePageData {
  id: string
  name: string
  years: number[]
  fileCount: number
  examClass: string
  examSubclass: string
  tag: string
  asset: string
  subjectLabels?: string[]
  parts?: Array<{ label: string; tag: string; asset: string; fileCount: number }>
}

export const bundlePagesCss: string
export function siteRoot(options: { base: string; origin: string }): string
export function buildBundlePage(bundle: BundlePageData, options: { repo: string; root: string }): string
export function buildSitemap(bundles: BundlePageData[], root: string): string
