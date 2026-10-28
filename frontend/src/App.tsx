import { Suspense, lazy, useEffect, useRef, useState } from 'react'
import { NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { ClockCounterClockwise, List, MagnifyingGlass, Video, X } from '@phosphor-icons/react'
import { useTheme } from './theme'
import { MOBILE_NAV, NAV_GROUPS, NAV_ITEMS } from './nav'
import { ConfirmHost, ToastHost } from './ui'
import ThemeToggle from './components/ThemeToggle'
import CommandPalette from './components/CommandPalette'
import VerificationDialog from './components/VerificationDialog'
import { TaskCenterProvider, useTaskCenter } from './context/TaskCenter'

const DashboardPage = lazy(() => import('./pages/DashboardPage'))
const VideosPage = lazy(() => import('./pages/VideosPage'))
const EditorPage = lazy(() => import('./pages/EditorPage'))
const DownloadPage = lazy(() => import('./pages/DownloadPage'))
const RecordPage = lazy(() => import('./pages/RecordPage'))
const PublishPage = lazy(() => import('./pages/PublishPage'))
const TasksPage = lazy(() => import('./pages/TasksPage'))
const AccountsPage = lazy(() => import('./pages/AccountsPage'))
const NarratePage = lazy(() => import('./pages/NarratePage'))
const NotFoundPage = lazy(() => import('./pages/NotFoundPage'))

function PageLoader() {
  return <div className="page-loader" role="status"><span className="skeleton" /><span className="skeleton" /><span className="skeleton" /></div>
}

function AppContent() {
  const location = useLocation()
  useTheme()
  const { runningTasks } = useTaskCenter()
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [moreOpen, setMoreOpen] = useState(false)
  const mainRef = useRef<HTMLElement>(null)
  const activeItem = NAV_ITEMS.find((item) => item.to === '/' ? location.pathname === '/' : location.pathname.startsWith(item.to))

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setPaletteOpen((open) => !open)
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    mainRef.current?.scrollTo({ top: 0 })
    setMoreOpen(false)
  }, [location.pathname])

  return (
    <div className="app-shell">
      <a href="#main" className="skip-link">跳到主内容</a>
      <aside className="sidebar">
        <NavLink to="/" className="sidebar-brand" aria-label="Video Hub 工作台概览">
          <span className="brand-mark"><Video size={19} weight="fill" /></span>
          <span><strong>Video Hub</strong><small>视频创作工作台</small></span>
        </NavLink>
        <button className="sidebar-search" type="button" onClick={() => setPaletteOpen(true)}>
          <MagnifyingGlass size={16} /><span>搜索与跳转</span><kbd>⌘ K</kbd>
        </button>
        <nav className="sidebar-nav" aria-label="主导航">
          {NAV_GROUPS.map((group) => (
            <div className="nav-group" key={group}>
              <p>{group}</p>
              {NAV_ITEMS.filter((item) => item.group === group).map((item) => {
                const Icon = item.icon
                return <NavLink key={item.to} to={item.to} end={item.to === '/'} className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''}`}>
                  <Icon size={18} /><span>{item.label}</span>
                  {item.to === '/tasks' && runningTasks > 0 && <b className="tnum">{runningTasks}</b>}
                </NavLink>
              })}
            </div>
          ))}
        </nav>
        <div className="sidebar-footer"><span className="status-dot" />本地数据 · 私密工作区</div>
      </aside>

      <div className="workspace">
        <header className="workspace-header">
          <NavLink to="/" className="mobile-brand" aria-label="工作台概览"><span className="brand-mark"><Video size={17} weight="fill" /></span><strong>Video Hub</strong></NavLink>
          <div className="header-context"><span>工作区</span><i>/</i><strong>{activeItem?.label ?? '页面未找到'}</strong></div>
          <div className="header-actions">
            <button className="header-search" type="button" onClick={() => setPaletteOpen(true)}><MagnifyingGlass size={17} /><span>搜索素材或页面</span><kbd>⌘ K</kbd></button>
            <button className="icon-button mobile-search" type="button" aria-label="搜索" onClick={() => setPaletteOpen(true)}><MagnifyingGlass size={18} /></button>
            <NavLink to="/tasks" className="icon-button task-button" aria-label={`任务中心，${runningTasks} 个进行中`}><ClockCounterClockwise size={18} />{runningTasks > 0 && <b>{runningTasks}</b>}</NavLink>
            <ThemeToggle />
          </div>
        </header>

        <main id="main" ref={mainRef} className="workspace-main" tabIndex={-1}>
          <div key={location.pathname} className={`page-in page-container ${location.pathname === '/narrate' ? 'narrate-container' : ''}`}>
            <Suspense fallback={<PageLoader />}>
              <Routes location={location}>
                <Route path="/" element={<DashboardPage />} />
                <Route path="/videos" element={<VideosPage />} />
                <Route path="/edit" element={<EditorPage />} />
                <Route path="/narrate" element={<NarratePage />} />
                <Route path="/download" element={<DownloadPage />} />
                <Route path="/record" element={<RecordPage />} />
                <Route path="/publish" element={<PublishPage />} />
                <Route path="/tasks" element={<TasksPage />} />
                <Route path="/accounts" element={<AccountsPage />} />
                <Route path="*" element={<NotFoundPage />} />
              </Routes>
            </Suspense>
          </div>
        </main>
      </div>

      <nav className="dock" aria-label="移动端主导航">
        {MOBILE_NAV.map((item) => {
          const Icon = item.icon
          return <NavLink key={item.to} to={item.to} end={item.to === '/'} className={({ isActive }) => `dock-item ${isActive ? 'active' : ''}`}><Icon size={20} /><span>{item.shortLabel}</span></NavLink>
        })}
        <button type="button" className={`dock-item ${moreOpen ? 'active' : ''}`} onClick={() => setMoreOpen(true)} aria-label="更多页面"><List size={20} /><span>更多</span></button>
      </nav>

      {moreOpen && <div className="mobile-more-backdrop" onClick={() => setMoreOpen(false)}>
        <section className="mobile-more" role="dialog" aria-modal="true" aria-label="更多页面" onClick={(event) => event.stopPropagation()}>
          <header><div><p className="eyebrow">WORKSPACE</p><h2>前往其他页面</h2></div><button className="icon-button" onClick={() => setMoreOpen(false)} aria-label="关闭"><X size={20} /></button></header>
          <div className="mobile-more-grid">{NAV_ITEMS.map((item) => { const Icon = item.icon; return <NavLink key={item.to} to={item.to} end={item.to === '/'}><Icon size={21} /><span>{item.label}</span>{item.to === '/tasks' && runningTasks > 0 && <b>{runningTasks}</b>}</NavLink> })}</div>
        </section>
      </div>}
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      <ToastHost /><ConfirmHost />
    </div>
  )
}

export default function App() {
  return (
    <TaskCenterProvider>
      <AppContent />
      <VerificationDialog />
    </TaskCenterProvider>
  )
}
