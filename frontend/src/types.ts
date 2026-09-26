export interface BundlePart {
  label: string
  url: string
  fileCount: number
}

export interface BundleSource {
  name: string
  url: string
}

export interface Bundle {
  id: string
  name: string
  years: number[]
  fileCount: number
  url: string
  parts?: BundlePart[]
  examClass: string
  examSubclass: string
  subjectLabels?: string[]
  sources?: BundleSource[]
  updated?: string
}
