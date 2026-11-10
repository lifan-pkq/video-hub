import { Clapperboard, Mic } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { api } from '../lib/api.ts'
import type { Health } from '../lib/types.ts'
import { cn } from '../lib/utils.ts'

export function Shell() {
  const [health, setHealth] = useState<Health | null>(null)
  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null))
  }, [])
  return (
    <div className="flex h-full min-h-screen flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-2.5">
        <div className="mr-2 flex items-center gap-2">
          <span className="grid h-7 w-7 place-items-center rounded bg-amber text-xs font-semibold text-bg">剪</span>
          <div>
            <p className="text-sm font-medium leading-none">剪辑台</p>
            <p className="mt-1 text-[11px] text-faint">智能剪辑 · AI 解说</p>
          </div>
        </div>
        <nav className="flex gap-1">
          <TopLink to="/edit" icon={<Clapperboard size={15} />}>智能剪辑</TopLink>
          <TopLink to="/narrate" icon={<Mic size={15} />}>AI 解说</TopLink>
        </nav>
        <div className="ml-auto flex flex-wrap gap-1.5 text-[11px] text-muted">
          <Chip ok={!!health?.ffmpeg}>ffmpeg</Chip>
          <Chip ok={!!health?.llm}>文本模型</Chip>
          <Chip ok={!!health?.vision}>视觉</Chip>
          <Chip ok={health?.asr === 'remote' || health?.asr === 'whisper'}>
            {health?.asr === 'remote' ? '共享 ASR' : health?.asr === 'whisper' ? '本地转写' : '转写未配置'}
          </Chip>
        </div>
      </header>
      <main className="min-h-0 flex-1">
        <Outlet />
      </main>
    </div>
  )
}

function TopLink({ to, icon, children }: { to: string; icon: ReactNode; children: string }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn('inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm', isActive ? 'bg-panel-2 text-ink' : 'text-muted hover:text-ink')
      }
    >
      {icon}
      {children}
    </NavLink>
  )
}

function Chip({ ok, children }: { ok: boolean; children: string }) {
  return (
    <span className={cn('rounded-full border px-2 py-0.5', ok ? 'border-keep/40 text-keep' : 'border-line text-faint')}>
      {children}
    </span>
  )
}
