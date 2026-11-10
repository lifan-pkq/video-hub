import {
  Broadcast,
  Broom,
  ClockCounterClockwise,
  DownloadSimple,
  FilmSlate,
  FilmStrip,
  Gauge,
  MagicWand,
  Package,
  Sparkle,
  SquaresFour,
  UserCircle,
  ImageSquare,
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
  { to: '/', label: '工作台概览', shortLabel: '概览', icon: Gauge, keywords: 'dashboard home 首页 统计 健康', group: '概览' },
  { to: '/workbench', label: '创作工作台', shortLabel: '工作台', icon: SquaresFour, keywords: 'workbench 最近素材 剪辑 解说 导出 下载', group: '概览' },
  { to: '/videos', label: '视频素材库', shortLabel: '素材', icon: VideoCamera, keywords: 'videos library 视频 海报墙 素材卡', group: '素材' },
  { to: '/images', label: '图片素材库', shortLabel: '图片', icon: ImageSquare, keywords: 'images library 图片 素材 参考图', group: '素材' },
  { to: '/download', label: '视频下载与灵感', shortLabel: '下载', icon: DownloadSimple, keywords: 'download 下载 yt-dlp 灵感 链接 笔记', group: '获取' },
  { to: '/record', label: '直播录制', shortLabel: '录制', icon: Broadcast, keywords: 'record live 录制 直播 分段', group: '获取' },
  { to: '/edit', label: '智能剪辑', shortLabel: '剪辑', icon: FilmSlate, keywords: 'edit editor 智能剪辑 拼接 时间线 timeline 理解', group: '制作' },
  { to: '/narrate', label: 'AI 解说', shortLabel: '解说', icon: MagicWand, keywords: 'narrato ai 解说 配音 字幕 生成 剧本 分镜', group: '制作' },
  { to: '/ai-media', label: 'AI 素材', shortLabel: 'AI 素材', icon: Sparkle, keywords: 'ai image video 图片 视频 生成 人物档案 key', group: '制作' },
  { to: '/ai-drama', label: 'AI 短剧', shortLabel: '短剧', icon: FilmStrip, keywords: 'ai drama 短剧 小说 分集 分镜 剧集 锚定图 生成', group: '制作' },
  { to: '/export', label: '封面与导出', shortLabel: '导出', icon: Package, keywords: 'export 导出 封面 标题 多画幅 字幕 套装', group: '交付' },
  { to: '/tasks', label: '任务中心', shortLabel: '任务', icon: ClockCounterClockwise, keywords: 'tasks 任务 队列 进度', group: '运行' },
  { to: '/profiles', label: '创作档案', shortLabel: '档案', icon: UserCircle, keywords: '人物 场景 风格 发音 词典 profiles', group: '设置' },
  { to: '/settings/system', label: '系统', shortLabel: '系统', icon: Broom, keywords: 'maintenance cleanup 清理 删除 日志 磁盘 服务 设置', group: '设置' },
]

export const NAV_GROUPS = ['概览', '素材', '获取', '制作', '交付', '运行', '设置']
export const MOBILE_NAV = NAV_ITEMS.filter((item) => ['/', '/videos', '/narrate', '/download', '/export'].includes(item.to))
