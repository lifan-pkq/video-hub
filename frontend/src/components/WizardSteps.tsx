import { Check } from '@phosphor-icons/react'

export interface WizardStep {
  key: string
  label: string
}

interface Props {
  steps: WizardStep[]
  /** 当前步骤下标（0 起） */
  current: number
}

/**
 * 向导步骤条：桌面端横向、移动端（<768px）竖向。
 * 已完成步骤打勾、当前步骤高亮、未来步骤灰色。
 */
export default function WizardSteps({ steps, current }: Props) {
  return (
    <ol className="flex flex-col gap-1 md:flex-row md:items-center md:gap-0" aria-label="向导步骤">
      {steps.map((step, i) => {
        const done = i < current
        const active = i === current
        return (
          <li key={step.key} className="flex flex-col md:flex-1 md:flex-row md:items-center md:last:flex-none">
            <div className="flex items-center gap-2.5">
              <span
                aria-current={active ? 'step' : undefined}
                className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[11px] font-medium transition-colors ${
                  done
                    ? 'border-accent bg-accent text-white'
                    : active
                      ? 'border-accent bg-accent-soft text-accent'
                      : 'border-line bg-fill text-ink-4'
                }`}
              >
                {done ? <Check size={12} weight="bold" /> : i + 1}
              </span>
              <span
                className={`whitespace-nowrap text-[13px] transition-colors ${
                  active ? 'font-medium text-ink-1' : done ? 'text-ink-2' : 'text-ink-4'
                }`}
              >
                {step.label}
              </span>
            </div>
            {i < steps.length - 1 && (
              <>
                {/* 桌面横向连接线 */}
                <span
                  aria-hidden
                  className={`mx-3 hidden h-px min-w-6 flex-1 md:block ${done ? 'bg-accent' : 'bg-line'}`}
                />
                {/* 移动端竖向连接线（对齐圆圈中心） */}
                <span
                  aria-hidden
                  className={`my-1 ml-[11px] block h-4 w-px md:hidden ${done ? 'bg-accent' : 'bg-line'}`}
                />
              </>
            )}
          </li>
        )
      })}
    </ol>
  )
}
