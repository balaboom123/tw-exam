interface StatsBarProps {
  total: number
  totalFiles: number
  yearRange: string
}

export function StatsBar({ total, totalFiles, yearRange }: StatsBarProps) {
  return (
    <dl className="collection-stats">
      <div><dt>收錄類科</dt><dd>{total.toLocaleString()}<span> 類</span></dd></div>
      <div><dt>試題檔案</dt><dd>{totalFiles.toLocaleString()}<span> 份</span></dd></div>
      <div className="stats-years"><dt>民國年度</dt><dd>{yearRange}</dd></div>
    </dl>
  )
}
