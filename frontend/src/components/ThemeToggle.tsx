import { Moon, Sun } from '@phosphor-icons/react'
import { useTheme } from '../theme'

/** 深色/浅色切换 */
export default function ThemeToggle() {
  const [theme, setTheme] = useTheme()
  const dark = theme === 'dark'
  return (
    <button
      type="button"
      onClick={() => setTheme(dark ? 'light' : 'dark')}
      className="flex h-8 w-8 items-center justify-center rounded-lg text-ink-3 transition-colors hover:bg-fill hover:text-ink-1"
      title={dark ? '切换为浅色' : '切换为深色'}
      aria-label={dark ? '切换为浅色主题' : '切换为深色主题'}
    >
      {dark ? <Sun size={16} /> : <Moon size={16} />}
    </button>
  )
}
