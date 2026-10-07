export type SortKey = "name" | "files-desc" | "years-desc"

interface SortSelectProps {
  value: SortKey
  onChange: (value: SortKey) => void
}

export function SortSelect({ value, onChange }: SortSelectProps) {
  return (
    <div className="sort-control">
      <label htmlFor="exam-sort">排序</label>
      <select id="exam-sort" value={value} onChange={(event) => onChange(event.target.value as SortKey)}>
        <option value="name">名稱</option>
        <option value="files-desc">檔案數最多</option>
        <option value="years-desc">年份數最多</option>
      </select>
    </div>
  )
}
