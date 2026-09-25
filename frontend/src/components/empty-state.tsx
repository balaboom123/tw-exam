import { Stamp } from "@/components/stamp"

export function EmptyState({ onReset }: { onReset: () => void }) {
  return (
    <div className="flex flex-col items-center py-24 text-center">
      <Stamp>查無資料</Stamp>
      <p className="mt-7 font-medium text-ink-950">查無相符的考試類科</p>
      <p className="mt-1.5 text-sm text-ink-500">
        請更換關鍵字，或取消年度篩選
      </p>
      <button
        type="button"
        onClick={onReset}
        className="mt-5 h-10 rounded-sm border border-line-strong px-4 text-sm font-medium text-ink-800 transition-colors hover:bg-cream"
      >
        清除搜尋條件
      </button>
    </div>
  )
}
