import schema from "../../../schemas/source-material-v1.schema.json" with { type: "json" }

export interface SourceDate {
  basis: string
  year_ad: number | null
}

export interface MaterialSummary {
  schema_version: 1
  kind: string
  dates: SourceDate[]
}

const kindLabels: Record<string, string> = schema.$defs.kind["x-display-labels"]
const dateLabels: Record<string, string> = schema.$defs.date["x-display-labels"]

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function hasFields(value: Record<string, unknown>, fields: string[]): boolean {
  return Object.keys(value).length === fields.length && fields.every((field) => Object.hasOwn(value, field))
}

export function isMaterialSummary(value: unknown): value is MaterialSummary {
  if (!isRecord(value) || !hasFields(value, ["schema_version", "kind", "dates"]) ||
    value.schema_version !== 1 || typeof value.kind !== "string" ||
    value.kind === "unknown" || !Object.hasOwn(kindLabels, value.kind) ||
    !Array.isArray(value.dates) || !value.dates.length) return false
  const seen = new Set<string>()
  for (const date of value.dates) {
    if (!isRecord(date) || !hasFields(date, ["basis", "year_ad"]) ||
      typeof date.basis !== "string" || date.basis === "unknown" || !Object.hasOwn(dateLabels, date.basis)) return false
    if (date.basis === "undated") {
      if (date.year_ad !== null) return false
    } else if (!Number.isInteger(date.year_ad) || Number(date.year_ad) < 1600 || Number(date.year_ad) > 9999) {
      return false
    }
    if (date.basis === "exam_year" && value.kind !== "administered") return false
    const key = `${date.basis}:${date.year_ad}`
    if (seen.has(key)) return false
    seen.add(key)
  }
  return true
}

export function materialYears(material: MaterialSummary): number[] {
  return [...new Set(material.dates.flatMap((date) => date.year_ad === null ? [] : [date.year_ad - 1911]))].sort((a, b) => b - a)
}

export function materialLabel(material: MaterialSummary): string {
  return kindLabels[material.kind]
}

export function materialDateLabels(material: MaterialSummary): string[] {
  const grouped = new Map<string, number[]>()
  for (const date of material.dates) {
    if (!grouped.has(date.basis)) grouped.set(date.basis, [])
    if (date.year_ad !== null) grouped.get(date.basis)!.push(date.year_ad)
  }
  return [...grouped].map(([basis, years]) => years.length
    ? `${dateLabels[basis]}：${years.sort((a, b) => b - a).join("、")} 年`
    : dateLabels[basis])
}

export function materialMatchesYears(material: MaterialSummary, years: number[]): boolean {
  const expected = materialYears(material)
  return expected.length === years.length && expected.every((year, index) => year === years[index])
}

export function matchesPublicationYear(bundle: { years: number[]; sourceMaterial?: MaterialSummary }, selected: number | "undated" | null): boolean {
  if (selected === null) return true
  if (selected === "undated") return bundle.sourceMaterial?.dates.some((date) => date.basis === "undated") ?? false
  return bundle.years.includes(selected)
}
