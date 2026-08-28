import { lazy, Suspense, useCallback, useEffect, useState } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { Command as CommandIcon } from '@phosphor-icons/react'
import { fetchTasks, type TaskSummary } from './api'
import { useTheme } from './theme'
import { MAIN_NAV_ITEMS, MOBILE_TAB_ITEMS, SETTINGS_NAV_ITEMS, type NavItem } from './nav'
import { useEvents, type EventMsg } from './events'
import { ConfirmHost, ToastHost } from './ui'
import ThemeToggle from './components/ThemeToggle'
import CommandPalette from './components/CommandPalette'
import NotifyCenter, { useNotifications } from './components/NotifyCenter'
import WorkbenchPage from './pages/WorkbenchPage'
import VideosPage from './pages/VideosPage'
import ImportWizardPage from './pages/ImportWizardPage'
import PublishPage from './pages/PublishPage'
import TasksPage from './pages/TasksPage'
import AccountsPage from './pages/AccountsPage'
import NotFoundPage from './pages/NotFoundPage'

// 编辑器依赖重型时间线库，按需懒加载
const EditorPage = lazy(() => import('./pages/EditorPage'))

function EditorFallback() {
  return (
    <div className="flex h-[240px] items-center justify-center text-sm text-ink-3">编辑器加载中…</div>
  )
}

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

/** 进行中任务数角标 */
function TaskBadge({ count }: { count: number }) {
  if (count <= 0) return null
  return (
    <span className="tnum ml-auto flex h-4 min-w-4 items-center justify-center rounded-full bg-accent px-1 text-[10px] font-medium text-white">
      {count}
    </span>
  )
}

/** 侧边栏导航项 */
function SideNavLink({
  item,
  active,
  badge,
  onGo,
}: {
  item: NavItem
  active: boolean
  badge?: number
  onGo: (to: string) => void
}) {
  const Icon = item.icon
  return (
    <button
      type="button"
      onClick={() => onGo(item.to)}
      aria-current={active ? 'page' : undefined}
      className={`flex w-full items-center gap-2.5 rounded-[10px] px-3 py-2 text-[13px] transition-colors ${
        active ? 'bg-accent-soft font-medium text-accent' : 'text-ink-3 hover:bg-fill hover:text-ink-1'
      }`}
    >
      <Icon size={16} weight={active ? 'fill' : 'regular'} />
      {item.label}
      {badge !== undefined && <TaskBadge count={badge} />}
    </button>
  )
}

export default function App() {
  const location = useLocation()
  const navigate = useNavigate()
  useTheme()
  const [scrolled, setScrolled] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [tasks, setTasks] = useState<TaskSummary[]>([])
  const [latestEvent, setLatestEvent] = useState<EventMsg | null>(null)

  // 实时事件：WS 优先，失败自动降级轮询（见 events.ts）
  const onEvent = useCallback((msg: EventMsg) => {
    setLatestEvent(msg)
    if (msg.type === 'snapshot') {
      setTasks(msg.tasks)
    } else if (msg.type === 'task_updated') {
      setTasks((prev) => {
        const i = prev.findIndex((t) => t.id === msg.task.id)
        if (i === -1) return [msg.task, ...prev]
        const next = prev.slice()
        next[i] = msg.task
        return next
      })
    }
  }, [])
  useEvents(onEvent)
  const notif = useNotifications(latestEvent)

  // 首屏先拉一次任务列表，保证角标在首个事件到达前就有值
  useEffect(() => {
    fetchTasks()
      .then(setTasks)
      .catch(() => {})
  }, [])

  const runningTasks = tasks.filter((t) => t.status === 'pending' || t.status === 'running').length

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

  // 内容区滚动时顶栏加阴影
  useEffect(() => {
    const onScroll = () => setScrolled((document.getElementById('main')?.scrollTop ?? 0) > 8)
    onScroll()
    window.addEventListener('scroll', onScroll, true)
    return () => window.removeEventListener('scroll', onScroll, true)
  }, [])

  const isActive = useCallback(
    (to: string) => (to === '/' ? location.pathname === '/' : location.pathname.startsWith(to)),
    [location.pathname],
  )
  const go = useCallback((to: string) => navigate(to), [navigate])

  return (
    <div className="flex h-full">
      <a href="#main" className="skip-link">
        跳到主内容
      </a>

      {/* 桌面端左侧边栏（>768px） */}
      <aside className="hidden w-[224px] shrink-0 flex-col border-r border-line-soft bg-card md:flex">
        <button
          type="button"
          onClick={() => navigate('/')}
          className="flex h-14 shrink-0 items-center gap-2.5 border-b border-line-soft px-4"
          aria-label="回到工作台"
        >
          <Logo />
          <span className="font-display text-[15px] font-semibold tracking-tight text-ink-1">Video Hub</span>
        </button>

        <nav className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto p-3" aria-label="主导航">
          {MAIN_NAV_ITEMS.map((item) => (
            <SideNavLink
              key={item.to}
              item={item}
              active={isActive(item.to)}
              badge={item.to === '/tasks' ? runningTasks : undefined}
              onGo={go}
            />
          ))}
        </nav>

        {/* 设置区 */}
        <div className="shrink-0 border-t border-line-soft p-3">
          <div className="px-3 pb-1.5 text-[11px] font-medium text-ink-4">设置</div>
          {SETTINGS_NAV_ITEMS.map((item) => (
            <SideNavLink key={item.to} item={item} active={isActive(item.to)} onGo={go} />
          ))}
        </div>
      </aside>

      {/* 右列：顶栏 + 内容区 */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className={`topbar ${scrolled ? 'scrolled' : ''}`}>
          <div className="flex h-14 items-center gap-2 px-4 md:px-6">
            {/* 移动端品牌（桌面端在侧边栏） */}
            <button
              type="button"
              onClick={() => navigate('/')}
              className="flex shrink-0 items-center gap-2.5 md:hidden"
              aria-label="回到工作台"
            >
              <Logo />
              <span className="font-display text-[15px] font-semibold tracking-tight text-ink-1">Video Hub</span>
            </button>

            <div className="ml-auto flex shrink-0 items-center gap-1.5">
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
              <NotifyCenter notifications={notif.notifications} onRead={notif.markAllRead} onClear={notif.clear} />
              <ThemeToggle />
            </div>
          </div>
        </header>

        {/* 主内容 */}
        <main id="main" className="min-h-0 flex-1 overflow-y-auto">
          <div key={location.pathname} className="page-in mx-auto max-w-[1360px] px-4 pb-24 pt-6 md:px-6 md:pb-10">
            <Routes location={location}>
              <Route path="/" element={<WorkbenchPage />} />
              <Route path="/library" element={<VideosPage />} />
              <Route path="/library/import" element={<ImportWizardPage />} />
              <Route
                path="/edit"
                element={
                  <Suspense fallback={<EditorFallback />}>
                    <EditorPage />
                  </Suspense>
                }
              />
              <Route path="/publish" element={<PublishPage />} />
              <Route path="/tasks" element={<TasksPage />} />
              <Route path="/accounts" element={<AccountsPage />} />
              {/* 旧路由 301 重定向 */}
              <Route path="/videos" element={<Navigate to="/library" replace />} />
              <Route path="/download" element={<Navigate to="/library/import" replace />} />
              <Route path="*" element={<NotFoundPage />} />
            </Routes>
          </div>
        </main>
      </div>

      {/* 移动端底部 Tab 栏（≤768px 固定） */}
      <nav
        className="fixed inset-x-0 bottom-0 z-40 flex border-t border-line bg-card/95 pb-[env(safe-area-inset-bottom)] backdrop-blur-lg md:hidden"
        aria-label="主导航"
      >
        {MOBILE_TAB_ITEMS.map((item) => {
          const Icon = item.icon
          const active = isActive(item.to)
          return (
            <button
              key={item.to}
              type="button"
              onClick={() => navigate(item.to)}
              className={`relative flex flex-1 flex-col items-center gap-0.5 py-2 text-[10px] transition-colors ${
                active ? 'text-accent' : 'text-ink-3'
              }`}
              aria-current={active ? 'page' : undefined}
            >
              <Icon size={19} weight={active ? 'fill' : 'regular'} />
              {item.label}
              {item.to === '/tasks' && runningTasks > 0 && (
                <span className="absolute right-[calc(50%-18px)] top-1 h-1.5 w-1.5 rounded-full bg-accent" />
              )}
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
