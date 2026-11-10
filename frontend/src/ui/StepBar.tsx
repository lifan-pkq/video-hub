export interface StepBarStep {
  label: string
  desc?: string
  state: 'done' | 'current' | 'todo'
}

/**
 * 横向步骤条：只做展示，不改任何业务逻辑。
 * done = 已完成，current = 当前步，todo = 未到。
 */
export default function StepBar({ steps }: { steps: StepBarStep[] }) {
  return (
    <ol className="vh-stepbar" aria-label="当前流程">
      {steps.map((step, index) => (
        <li key={step.label} className={`vh-stepbar-item ${step.state}`} aria-current={step.state === 'current' ? 'step' : undefined}>
          <span className="vh-stepbar-dot">{step.state === 'done' ? '✓' : index + 1}</span>
          <span className="vh-stepbar-text">
            <strong>{step.label}</strong>
            {step.desc && <small>{step.desc}</small>}
          </span>
        </li>
      ))}
    </ol>
  )
}
