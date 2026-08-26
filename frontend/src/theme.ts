import { useCallback, useEffect, useState } from 'react'

export type Theme = 'dark' | 'light'

const KEY = 'video-hub-theme'

export function getTheme(): Theme {
  try {
    const v = localStorage.getItem(KEY)
    if (v === 'light' || v === 'dark') return v
  } catch {
    // localStorage 不可用时用默认
  }
  return 'dark' // 工作台默认深色
}

/** 主题 = html.dark class，全局唯一数据源 */
export function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle('dark', theme === 'dark')
  try {
    localStorage.setItem(KEY, theme)
  } catch {
    // 忽略持久化失败
  }
}

export function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(() => getTheme())
  useEffect(() => {
    applyTheme(theme)
  }, [theme])
  const set = useCallback((t: Theme) => setTheme(t), [])
  return [theme, set]
}
