/** 平台品牌色标识：圆角方块 + 首字，替代 emoji */
const BRAND: Record<string, { char: string; bg: string; fg: string }> = {
  douyin: { char: '抖', bg: '#161823', fg: '#25f4ee' },
  xiaohongshu: { char: '红', bg: '#ff2442', fg: '#ffffff' },
  kuaishou: { char: '快', bg: '#ff7e00', fg: '#ffffff' },
  bilibili: { char: 'B', bg: '#00a1d6', fg: '#ffffff' },
  local: { char: '本', bg: 'var(--fill-deep)', fg: 'var(--ink-2)' },
  direct: { char: '链', bg: 'var(--fill-deep)', fg: 'var(--ink-2)' },
}

interface Props {
  platform: string | null | undefined
  size?: number
}

export default function PlatformIcon({ platform, size = 22 }: Props) {
  let key = platform ?? 'local'
  // 直播录制来源沿用原平台品牌色
  if (key.startsWith('live_')) key = key.slice(5)
  const b = BRAND[key] ?? { char: (key[0] ?? '?').toUpperCase(), bg: 'var(--fill-deep)', fg: 'var(--ink-2)' }
  return (
    <span
      aria-hidden
      className="inline-flex shrink-0 items-center justify-center rounded-md font-medium"
      style={{
        width: size,
        height: size,
        fontSize: size * 0.55,
        lineHeight: 1,
        backgroundColor: b.bg,
        color: b.fg,
        boxShadow: 'inset 0 0 0 1px rgba(255,255,255,0.08)',
      }}
    >
      {b.char}
    </span>
  )
}
