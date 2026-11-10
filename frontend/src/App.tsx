import { Suspense, lazy, useEffect, useRef, useState } from 'react'
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { ClockCounterClockwise, List, MagnifyingGlass, Video, X } from '@phosphor-icons/react'
import { useTheme } from './theme'
import { MOBILE_NAV, NAV_GROUPS, NAV_ITEMS } from './nav'
import { ConfirmHost, ToastHost } from './ui'
import ThemeToggle from './components/ThemeToggle'
import CommandPalette from './components/CommandPalette'
import TaskFocusBanner from './components/TaskFocusBanner'
import { TaskCenterProvider, useTaskCenter } from './context/TaskCenter'

const DashboardPage = lazy(() => import('./pages/DashboardPage'))
const WorkbenchPage = lazy(() => import('./pages/WorkbenchPage'))
const ImportWizardPage = lazy(() => import('./pages/ImportWizardPage'))
const VideosPage = lazy(() => import('./pages/VideosPage'))
const ImagesPage = lazy(() => import('./pages/ImagesPage'))
const EditPage = lazy(() => import('./pages/EditPage'))
const EditResultPage = lazy(() => import('./pages/EditResultPage'))
const DownloadPage = lazy(() => import('./pages/DownloadPage'))
const RecordPage = lazy(() => import('./pages/RecordPage'))
const ExportPage = lazy(() => import('./pages/ExportPage'))
const TasksPage = lazy(() => import('./pages/TasksPage'))
const MaintenancePage = lazy(() => import('./pages/MaintenancePage'))
const NarratePage = lazy(() => import('./pages/NarratePage'))
const AIMediaPage = lazy(() => import('./pages/AIMediaPage'))
const AIDramaPage = lazy(() => import('./pages/AIDramaPage'))
const AIDramaResultPage = lazy(() => import('./pages/AIDramaResultPage'))
const ProfilesPage = lazy(() => import('./pages/ProfilesPage'))
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
  // AI 短剧结果页（/ai-drama/:projectId）全屏展示：不显示左侧导航、顶栏和移动端 dock，
  // 左上角由结果页自己的「返回 AI 短剧」按钮负责回跳。
  // 智能剪辑结果页（/edit/:taskId、/edit/new）同款：全屏展示时间线，结果页左上角「返回智能剪辑」回跳。
  const isDramaResult = /^\/ai-drama\/[^/]+$/.test(location.pathname) || /^\/edit\/[^/]+$/.test(location.pathname)
  const activeItem = NAV_ITEMS.find((item) => item.to === '/' ? location.pathname === '/' : location.pathname.startsWith(item.to))
  const headerLabel = location.pathname.startsWith('/library/import')
    ? '导入素材'
    : location.pathname.startsWith('/library')
      ? '视频素材库'
      : (activeItem?.label ?? '页面未找到')

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
      {!isDramaResult && <aside className="sidebar">
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
      </aside>}

      <div className="workspace">
        {!isDramaResult && <header className="workspace-header">
          <NavLink to="/" className="mobile-brand" aria-label="工作台概览"><span className="brand-mark"><Video size={17} weight="fill" /></span><strong>Video Hub</strong></NavLink>
          <div className="header-context"><span>工作区</span><i>/</i><strong>{headerLabel}</strong></div>
          <div className="header-actions">
            <button className="header-search" type="button" onClick={() => setPaletteOpen(true)}><MagnifyingGlass size={17} /><span>搜索素材或页面</span><kbd>⌘ K</kbd></button>
            <button className="icon-button mobile-search" type="button" aria-label="搜索" onClick={() => setPaletteOpen(true)}><MagnifyingGlass size={18} /></button>
            <NavLink to="/tasks" className="icon-button task-button" aria-label={`任务中心，${runningTasks} 个进行中`}><ClockCounterClockwise size={18} />{runningTasks > 0 && <b>{runningTasks}</b>}</NavLink>
            <ThemeToggle />
          </div>
        </header>}

        <main id="main" ref={mainRef} className="workspace-main" tabIndex={-1}>
          <div key={location.pathname} className="page-in page-container">
            <Suspense fallback={<PageLoader />}>
              <Routes location={location}>
                <Route path="/" element={<DashboardPage />} />
                <Route path="/workbench" element={<WorkbenchPage />} />
                <Route path="/videos" element={<VideosPage />} />
                <Route path="/images" element={<ImagesPage />} />
                <Route path="/library" element={<VideosPage />} />
                <Route path="/library/import" element={<ImportWizardPage />} />
                <Route path="/edit" element={<EditPage />} />
                <Route path="/edit/new" element={<EditResultPage />} />
                <Route path="/edit/:taskId" element={<EditResultPage />} />
                <Route path="/narrate" element={<NarratePage />} />
                <Route path="/narrato" element={<Navigate to="/narrate" replace />} />
                <Route path="/ai-media" element={<AIMediaPage />} />
                <Route path="/ai-drama" element={<AIDramaPage />} />
                <Route path="/ai-drama/:projectId" element={<AIDramaResultPage />} />
                <Route path="/profiles" element={<ProfilesPage />} />
                <Route path="/download" element={<DownloadPage />} />
                <Route path="/record" element={<RecordPage />} />
                <Route path="/export" element={<ExportPage />} />
                <Route path="/publish" element={<Navigate to="/export" replace />} />
                <Route path="/tasks" element={<TasksPage />} />
                <Route path="/settings/system" element={<MaintenancePage />} />
                <Route path="/maintenance" element={<Navigate to="/settings/system" replace />} />
                <Route path="*" element={<NotFoundPage />} />
              </Routes>
            </Suspense>
          </div>
        </main>
      </div>

      {!isDramaResult && <nav className="dock" aria-label="移动端主导航">
        {MOBILE_NAV.map((item) => {
          const Icon = item.icon
          return <NavLink key={item.to} to={item.to} end={item.to === '/'} className={({ isActive }) => `dock-item ${isActive ? 'active' : ''}`}><Icon size={20} /><span>{item.shortLabel}</span></NavLink>
        })}
        <button type="button" className={`dock-item ${moreOpen ? 'active' : ''}`} onClick={() => setMoreOpen(true)} aria-label="更多页面"><List size={20} /><span>更多</span></button>
      </nav>}

      {moreOpen && <div className="mobile-more-backdrop" onClick={() => setMoreOpen(false)}>
        <section className="mobile-more" role="dialog" aria-modal="true" aria-label="更多页面" onClick={(event) => event.stopPropagation()}>
          <header><div><p className="eyebrow">WORKSPACE</p><h2>前往其他页面</h2></div><button className="icon-button" onClick={() => setMoreOpen(false)} aria-label="关闭"><X size={20} /></button></header>
          <div className="mobile-more-grid">{NAV_ITEMS.map((item) => { const Icon = item.icon; return <NavLink key={item.to} to={item.to} end={item.to === '/'}><Icon size={21} /><span>{item.label}</span>{item.to === '/tasks' && runningTasks > 0 && <b>{runningTasks}</b>}</NavLink> })}</div>
        </section>
      </div>}
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      <TaskFocusBanner />
      <ToastHost /><ConfirmHost />
    </div>
  )
}

export default function App() {
  return <TaskCenterProvider><AppContent /></TaskCenterProvider>
}
