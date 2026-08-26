import {
  Broadcast,
  ClockCounterClockwise,
  DownloadSimple,
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
}

export const NAV_ITEMS: NavItem[] = [
  { to: '/', label: '仪表盘', icon: Gauge, keywords: 'dashboard home 首页 统计' },
  { to: '/videos', label: '视频库', icon: VideoCamera, keywords: 'videos library 视频 海报墙' },
  { to: '/edit', label: '视频剪辑', icon: FilmSlate, keywords: 'edit editor 剪辑 拼接 时间线 timeline' },
  { to: '/download', label: '视频下载', icon: DownloadSimple, keywords: 'download 下载 抖音 解析' },
  { to: '/record', label: '直播录制', icon: Broadcast, keywords: 'record live 录制 直播' },
  { to: '/publish', label: '发布视频', icon: PaperPlaneTilt, keywords: 'publish 发布 分发 多平台' },
  { to: '/tasks', label: '任务中心', icon: ClockCounterClockwise, keywords: 'tasks 任务 队列 进度' },
  { to: '/accounts', label: '平台账号', icon: UserCircle, keywords: 'accounts 账号 cookie 扫码' },
]
