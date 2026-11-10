export function FailBanner({ message, action }: { message?: string | null; action?: string | null }) {
  if (!message) return null
  return (
    <div className="rounded-md border border-drop/40 bg-drop/10 px-3 py-2 text-sm text-[#ffd9d3]">
      <p>{message}</p>
      {action ? <p className="mt-1 text-ink/80">{action}</p> : null}
    </div>
  )
}
