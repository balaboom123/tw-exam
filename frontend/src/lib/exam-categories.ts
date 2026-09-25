// Display order only. Class and subclass membership comes from the published feed.
export const CLASS_ORDER = [
  "公職考試",
  "專技人員考試",
  "國營／就業甄試",
  "教師考試",
  "升學測驗",
  "證照／檢定",
] as const

const classPosition = new Map<string, number>(
  CLASS_ORDER.map((label, index) => [label, index]),
)
const collator = new Intl.Collator("zh-TW")

export function orderExamClasses(labels: Iterable<string>): string[] {
  return [...new Set(labels)].sort((a, b) => {
    const aPosition = classPosition.get(a) ?? CLASS_ORDER.length
    const bPosition = classPosition.get(b) ?? CLASS_ORDER.length
    return aPosition - bPosition || collator.compare(a, b)
  })
}

export function orderExamSubclasses(labels: Iterable<string>): string[] {
  return [...new Set(labels)].sort(collator.compare)
}
