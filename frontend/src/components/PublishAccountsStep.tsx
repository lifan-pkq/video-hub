import { Link } from 'react-router-dom'
import { CheckCircle, SignIn, WarningCircle } from '@phosphor-icons/react'
import { Alert, PlatformIcon, Tag } from '../ui'
import { platformLabel, type Account } from '../api'

interface Props {
  accounts: Account[]
  /** 已选目标，元素格式 "platform:account" */
  targets: string[]
  onChange: (targets: string[]) => void
}

type AccountState = 'valid' | 'invalid' | 'no_cookie'

function accountState(acc: Account): AccountState {
  if (!acc.cookie_exists) return 'no_cookie'
  if (acc.valid === false) return 'invalid'
  return 'valid'
}

const STATE_META: Record<AccountState, { label: string; color: 'green' | 'red' | 'gray' }> = {
  valid: { label: '有效', color: 'green' },
  invalid: { label: '失效', color: 'red' },
  no_cookie: { label: '未登录', color: 'gray' },
}

/** 第③步：选择平台账号。按平台分组卡片多选；失效/未登录置灰并附「去登录」 */
export default function PublishAccountsStep({ accounts, targets, onChange }: Props) {
  const byPlatform = new Map<string, Account[]>()
  for (const acc of accounts) {
    const list = byPlatform.get(acc.platform) ?? []
    list.push(acc)
    byPlatform.set(acc.platform, list)
  }

  const toggle = (key: string, selectable: boolean) => {
    if (!selectable) return
    onChange(targets.includes(key) ? targets.filter((k) => k !== key) : [...targets, key])
  }

  if (byPlatform.size === 0) {
    return (
      <Alert type="warning">
        暂无平台账号，请先到
        <Link to="/accounts" className="mx-1 text-accent hover:underline">
          平台账号
        </Link>
        页添加并登录
      </Alert>
    )
  }

  return (
    <div className="space-y-4">
      {[...byPlatform.entries()].map(([platform, list]) => (
        <div key={platform}>
          <div className="mb-2 flex items-center gap-1.5">
            <PlatformIcon platform={platform} size={16} />
            <span className="text-[13px] font-medium text-ink-1">{platformLabel(platform)}</span>
            <span className="tnum text-xs text-ink-4">
              {targets.filter((k) => k.startsWith(`${platform}:`)).length}/{list.length}
            </span>
          </div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {list.map((acc) => {
              const key = `${acc.platform}:${acc.account}`
              const state = accountState(acc)
              const selectable = state === 'valid'
              const checked = targets.includes(key)
              const meta = STATE_META[state]
              return (
                <div
                  key={key}
                  onClick={() => toggle(key, selectable)}
                  className={`flex items-center gap-2.5 rounded-[10px] border p-3 transition-all ${
                    selectable
                      ? `cursor-pointer ${checked ? 'border-accent bg-accent-soft' : 'border-line bg-fill hover:border-line-soft hover:bg-fill-deep'}`
                      : 'cursor-not-allowed border-line-soft bg-fill opacity-55'
                  }`}
                >
                  <span
                    aria-hidden
                    className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-[5px] border transition-colors ${
                      checked ? 'border-accent bg-accent text-white' : 'border-line bg-card'
                    }`}
                  >
                    {checked && <CheckCircle size={12} weight="fill" />}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px] text-ink-1" title={acc.account}>
                      {acc.account}
                    </span>
                    {state === 'invalid' && acc.detail && (
                      <span className="mt-0.5 block truncate text-xs text-ink-3" title={acc.detail}>
                        {acc.detail}
                      </span>
                    )}
                  </span>
                  <Tag color={meta.color} icon={state === 'valid' ? undefined : <WarningCircle size={11} />}>
                    {meta.label}
                  </Tag>
                  {!selectable && (
                    <Link
                      to="/accounts"
                      onClick={(e) => e.stopPropagation()}
                      className="flex shrink-0 items-center gap-0.5 text-xs text-accent hover:underline"
                    >
                      <SignIn size={12} />
                      去登录
                    </Link>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}
