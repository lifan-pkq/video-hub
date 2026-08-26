import { useCallback, useEffect, useRef, useState } from 'react'
import { Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { Command as CommandIcon } from '@phosphor-icons/react'
import { fetchTasks } from './api'
import { useTheme } from './theme'
import { NAV_ITEMS } from './nav'
import { ConfirmHost, ToastHost } from './ui'
import ThemeToggle from './components/ThemeToggle'
import CommandPalette from './components/CommandPalette'
import DashboardPage from './pages/DashboardPage'
import VideosPage from './pages/VideosPage'
import EditorPage from './pages/EditorPage'
import DownloadPage from './pages/DownloadPage'
import RecordPage from './pages/RecordPage'
import PublishPage from './pages/PublishPage'
import TasksPage from './pages/TasksPage'
import AccountsPage from './pages/AccountsPage'
import NotFoundPage from './pages/NotFoundPage'

function Logo() {
  return (
    <span
      className="flex h-8 w-8 items-center justify-center rounded-[10px]"
      style={{ background: 'linear-gradient(135deg, var(--accent-hover), var(--accent-active))' }}
    >
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden>
        <path d="M8 5.5v13l11-6.5-11-6.5z" fill="#fff" />
      </svg>
    </span>
  )
}

export default function App() {
  const location = useLocation()
  const navigate = useNavigate()
  useTheme()
  const [scrolled, setScrolled] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [runningTasks, setRunningTasks] = useState(0)
  const timerRef = useRef<number | null>(null)

  // Cmd/Ctrl+K 打开命令面板
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPaletteOpen((v) => !v)
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  // 滚动时顶栏加阴影
  useEffect(() => {
    const onScroll = () =>
      setScrolled(document.documentElement.scrollTop > 8 || (document.getElementById('main')?.scrollTop ?? 0) > 8)
    onScroll()
    window.addEventListener('scroll', onScroll, true)
    return () => window.removeEventListener('scroll', onScroll, true)
  }, [])

  // 轮询进行中任务数（任务中心角标）
  useEffect(() => {
    let dead = false
    const tick = async () => {
      try {
        const tasks = await fetchTasks()
        if (!dead) setRunningTasks(tasks.filter((t) => t.status === 'pending' || t.status === 'running').length)
      } catch {
        // 静默重试
      }
    }
    tick()
    timerRef.current = window.setInterval(tick, 5000)
    return () => {
      dead = true
      if (timerRef.current) window.clearInterval(timerRef.current)
    }
  }, [])

  const isActive = useCallback(
    (to: string) => (to === '/' ? location.pathname === '/' : location.pathname.startsWith(to)),
    [location.pathname],
  )

  return (
    <div className="flex h-full flex-col">
      <a href="#main" className="skip-link">
        跳到主内容
      </a>

      {/* 顶部悬浮栏 */}
      <header className={`topbar ${scrolled ? 'scrolled' : ''}`}>
        <div className="mx-auto flex h-14 max-w-[1360px] items-center gap-3 px-4 lg:px-6">
          <button
            type="button"
            onClick={() => navigate('/')}
            className="flex shrink-0 items-center gap-2.5 rounded-lg pr-1"
            aria-label="回到仪表盘"
          >
            <Logo />
            <span className="font-display hidden text-[15px] font-semibold tracking-tight text-ink-1 sm:block">
              Video Hub
            </span>
          </button>

          {/* 桌面胶囊导航 */}
          <nav className="nav-pill-wrap mx-auto hidden lg:flex" aria-label="主导航">
            {NAV_ITEMS.map((item) => {
              const Icon = item.icon
              const active = isActive(item.to)
              return (
                <button
                  key={item.to}
                  type="button"
                  onClick={() => navigate(item.to)}
                  className={`nav-pill ${active ? 'active' : ''}`}
                  aria-current={active ? 'page' : undefined}
                >
                  <Icon size={14} weight={active ? 'fill' : 'regular'} />
                  {item.label}
                  {item.to === '/tasks' && runningTasks > 0 && (
                    <span className="tnum flex h-4 min-w-4 items-center justify-center rounded-full bg-accent px-1 text-[10px] font-medium text-white">
                      {runningTasks}
                    </span>
                  )}
                </button>
              )
            })}
          </nav>

          <div className="ml-auto flex shrink-0 items-center gap-1.5 lg:ml-0">
            {/* 命令面板入口 */}
            <button
              type="button"
              onClick={() => setPaletteOpen(true)}
              className="hidden h-8 items-center gap-2 rounded-[10px] border border-line bg-fill px-2.5 text-xs text-ink-3 transition-colors hover:text-ink-1 sm:flex"
            >
              <CommandIcon size={13} />
              <span>K</span>
              <span className="text-ink-4">搜索 / 跳转</span>
            </button>
            <button
              type="button"
              onClick={() => setPaletteOpen(true)}
              className="flex h-8 w-8 items-center justify-center rounded-[10px] border border-line bg-fill text-ink-3 transition-colors hover:text-ink-1 sm:hidden"
              aria-label="搜索"
            >
              <CommandIcon size={14} />
            </button>
            <ThemeToggle />
          </div>
        </div>
      </header>

      {/* 主内容 */}
      <main id="main" className="min-h-0 flex-1 overflow-y-auto">
        <div key={location.pathname} className="page-in mx-auto max-w-[1360px] px-4 pb-24 pt-6 lg:px-6 lg:pb-10">
          <Routes location={location}>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/videos" element={<VideosPage />} />
            <Route path="/edit" element={<EditorPage />} />
            <Route path="/download" element={<DownloadPage />} />
            <Route path="/record" element={<RecordPage />} />
            <Route path="/publish" element={<PublishPage />} />
            <Route path="/tasks" element={<TasksPage />} />
            <Route path="/accounts" element={<AccountsPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Routes>
        </div>
      </main>

      {/* 移动端底部 Dock */}
      <nav className="dock lg:hidden" aria-label="主导航">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon
          const active = isActive(item.to)
          return (
            <button
              key={item.to}
              type="button"
              onClick={() => navigate(item.to)}
              className={`dock-item ${active ? 'active' : ''}`}
              aria-current={active ? 'page' : undefined}
              aria-label={item.label}
            >
              <Icon size={18} weight={active ? 'fill' : 'regular'} />
            </button>
          )
        })}
      </nav>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      <ToastHost />
      <ConfirmHost />
    </div>
  )
}
