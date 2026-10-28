import {
  Broadcast,
  ClockCounterClockwise,
  DownloadSimple,
  FilmSlate,
  Gauge,
  MagicWand,
  PaperPlaneTilt,
  UserCircle,
  VideoCamera,
  type Icon,
} from '@phosphor-icons/react'

export interface NavItem {
  to: string
  label: string
  shortLabel: string
  icon: Icon
  keywords: string
  group: string
}

export const NAV_ITEMS: NavItem[] = [
  { to: '/', label: '工作台概览', shortLabel: '概览', icon: Gauge, keywords: 'dashboard home 首页 统计', group: '概览' },
  { to: '/videos', label: '视频素材库', shortLabel: '素材', icon: VideoCamera, keywords: 'videos library 视频 海报墙', group: '素材' },
  { to: '/download', label: '视频下载', shortLabel: '下载', icon: DownloadSimple, keywords: 'download 下载 抖音 解析', group: '获取' },
  { to: '/record', label: '直播录制', shortLabel: '录制', icon: Broadcast, keywords: 'record live 录制 直播', group: '获取' },
  { to: '/edit', label: '视频剪辑', shortLabel: '剪辑', icon: FilmSlate, keywords: 'edit editor 剪辑 拼接 时间线 timeline', group: '制作' },
  { to: '/narrate', label: 'AI 解说', shortLabel: '解说', icon: MagicWand, keywords: 'narrato ai 解说 配音 字幕 生成', group: '制作' },
  { to: '/publish', label: '发布视频', shortLabel: '发布', icon: PaperPlaneTilt, keywords: 'publish 发布 分发 多平台', group: '分发' },
  { to: '/accounts', label: '平台账号', shortLabel: '账号', icon: UserCircle, keywords: 'accounts 账号 cookie 扫码', group: '分发' },
  { to: '/tasks', label: '任务中心', shortLabel: '任务', icon: ClockCounterClockwise, keywords: 'tasks 任务 队列 进度', group: '运行' },
]

export const NAV_GROUPS = ['概览', '素材', '获取', '制作', '分发', '运行']
export const MOBILE_NAV = NAV_ITEMS.filter((item) => ['/', '/videos', '/download', '/publish'].includes(item.to))
