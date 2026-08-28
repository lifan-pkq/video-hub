import {
  ClockCounterClockwise,
  FilmSlate,
  Gauge,
  PaperPlaneTilt,
  UserCircle,
  VideoCamera,
  type Icon,
} from '@phosphor-icons/react'

export interface NavItem {
  to: string
  label: string
  icon: Icon
  /** 命令面板搜索关键词 */
  keywords: string
  /** 移动端底部 Tab 栏是否展示（≤768px） */
  mobileTab: boolean
  /** 分区：main = 主导航；settings = 侧边栏底部设置区（不进移动端 Tab） */
  section: 'main' | 'settings'
}

/**
 * 新信息架构（IA）：
 * 桌面端左侧边栏 = 主导航 5 项 + 底部设置区 1 项；
 * 移动端底部 Tab 栏 = mobileTab 为 true 的 5 项。
 * 旧路由 /videos → /library、/download → /library/import 由 App.tsx 重定向。
 */
export const NAV_ITEMS: NavItem[] = [
  { to: '/', label: '工作台', icon: Gauge, keywords: 'workbench dashboard home 首页 工作台', mobileTab: true, section: 'main' },
  { to: '/library', label: '素材库', icon: VideoCamera, keywords: 'library videos 素材 视频库 海报墙 导入 import', mobileTab: true, section: 'main' },
  { to: '/edit', label: '视频剪辑', icon: FilmSlate, keywords: 'edit editor 剪辑 拼接 时间线 timeline', mobileTab: true, section: 'main' },
  { to: '/publish', label: '发布', icon: PaperPlaneTilt, keywords: 'publish 发布 分发 多平台', mobileTab: true, section: 'main' },
  { to: '/tasks', label: '任务中心', icon: ClockCounterClockwise, keywords: 'tasks 任务 队列 进度', mobileTab: true, section: 'main' },
  { to: '/accounts', label: '平台账号', icon: UserCircle, keywords: 'accounts 账号 cookie 扫码 设置', mobileTab: false, section: 'settings' },
]

/** 主导航（侧边栏上半区 / 命令面板） */
export const MAIN_NAV_ITEMS = NAV_ITEMS.filter((n) => n.section === 'main')

/** 设置区导航（侧边栏底部） */
export const SETTINGS_NAV_ITEMS = NAV_ITEMS.filter((n) => n.section === 'settings')

/** 移动端底部 Tab（≤768px） */
export const MOBILE_TAB_ITEMS = NAV_ITEMS.filter((n) => n.mobileTab)
