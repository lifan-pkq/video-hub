import { useCallback, useEffect, useRef, useState, type ChangeEvent } from 'react'
import { ClipboardText, QrCode, UserCircle } from '@phosphor-icons/react'
import {
  checkAccount,
  deleteAccount,
  errorMessage,
  exportCookieString,
  fetchAccounts,
  formatTime,
  PLATFORMS,
  QR_LOGIN_PLATFORMS,
  uploadCookie,
  type Account,
  type CheckResult,
  type CookieExportResult,
} from '../api'
import {
  Alert,
  Button,
  confirmDialog,
  EmptyState,
  PageHeader,
  Panel,
  PlatformIcon,
  Tag,
  toast,
} from '../ui'
import AccountsLoginModal from '../components/AccountsLoginModal'
import AccountsImportModal from '../components/AccountsImportModal'
import AccountsExportModal from '../components/AccountsExportModal'
import { accountKey, metaOf } from '../components/AccountsShared'

/** cookie 状态徽标：有效=绿 / 失效=红 / 未知=灰 */
function cookieBadge(acc: Account, checked: CheckResult | null) {
  const invalid = checked ? !checked.valid : acc.valid === false
  const valid = checked ? checked.valid : acc.valid === true
  if (!acc.cookie_exists) return <Tag color="gray">未配置 Cookie</Tag>
  if (invalid) return <Tag color="red">Cookie 已失效</Tag>
  if (valid) return <Tag color="green">Cookie 有效</Tag>
  return <Tag color="gray">状态未知</Tag>
}

export default function AccountsPage() {
  const [accounts, setAccounts] = useState<Account[]>([])
  const [loading, setLoading] = useState(true)
  /** 顶部平台卡片筛选：null = 全部 */
  const [filter, setFilter] = useState<string | null>(null)

  const [checking, setChecking] = useState<Record<string, boolean>>({})
  const [checkResults, setCheckResults] = useState<Record<string, CheckResult | { error: string }>>({})

  const [uploading, setUploading] = useState<Record<string, boolean>>({})
  const fileRefs = useRef<Record<string, HTMLInputElement | null>>({})

  // 扫码登录弹窗
  const [loginOpen, setLoginOpen] = useState(false)
  const [loginPlatform, setLoginPlatform] = useState('douyin')
  const [loginAccount, setLoginAccount] = useState('')
  const [loginLock, setLoginLock] = useState(false)

  // 导入 cookie 弹窗
  const [importOpen, setImportOpen] = useState(false)
  const [importPlatform, setImportPlatform] = useState('douyin')
  const [importAccount, setImportAccount] = useState('')
  const [importLock, setImportLock] = useState(false)

  // 导出 cookie 弹窗
  const [exporting, setExporting] = useState<Record<string, boolean>>({})
  const [exportData, setExportData] = useState<CookieExportResult | null>(null)

  const load = useCallback(async () => {
    try {
      setAccounts(await fetchAccounts())
    } catch (e) {
      toast.error(`加载失败：${errorMessage(e)}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const openLogin = (platform = 'douyin', account = '', lock = false) => {
    setLoginPlatform(platform)
    setLoginAccount(account)
    setLoginLock(lock)
    setLoginOpen(true)
  }

  const openImport = (platform = 'douyin', account = '', lock = false) => {
    setImportPlatform(platform)
    setImportAccount(account)
    setImportLock(lock)
    setImportOpen(true)
  }

  const handleCheck = async (acc: Account) => {
    const key = accountKey(acc)
    setChecking((s) => ({ ...s, [key]: true }))
    setCheckResults((s) => {
      const next = { ...s }
      delete next[key]
      return next
    })
    try {
      const r = await checkAccount(acc.platform, acc.account)
      setCheckResults((s) => ({ ...s, [key]: r }))
      if (r.valid) toast.success(`${acc.account} cookie 有效`)
      else toast.warning(`${acc.account} cookie 无效${r.detail ? `：${r.detail}` : ''}`)
    } catch (e) {
      setCheckResults((s) => ({ ...s, [key]: { error: errorMessage(e) } }))
      toast.error(`校验失败：${errorMessage(e)}`)
    } finally {
      setChecking((s) => ({ ...s, [key]: false }))
    }
  }

  const handleUpload = async (acc: Account, file: File) => {
    const key = accountKey(acc)
    setUploading((s) => ({ ...s, [key]: true }))
    try {
      const r = await uploadCookie(acc.platform, acc.account, file)
      if (r.ok) toast.success(r.message)
      else toast.warning(r.message)
      await load()
    } catch (e) {
      toast.error(`上传失败：${errorMessage(e)}`)
    } finally {
      setUploading((s) => ({ ...s, [key]: false }))
      const input = fileRefs.current[key]
      if (input) input.value = ''
    }
  }

  const handleExport = async (acc: Account) => {
    const key = accountKey(acc)
    setExporting((s) => ({ ...s, [key]: true }))
    try {
      setExportData(await exportCookieString(acc.platform, acc.account))
    } catch (e) {
      toast.error(`导出失败：${errorMessage(e)}`)
    } finally {
      setExporting((s) => ({ ...s, [key]: false }))
    }
  }

  const handleDelete = async (acc: Account) => {
    const ok = await confirmDialog({
      title: '确认删除',
      content: `确定要删除账号「${metaOf(acc.platform).label} / ${acc.account}」吗？其 cookie 文件将一并删除，此操作不可恢复。`,
      okText: '确认删除',
      cancelText: '取消',
      danger: true,
    })
    if (!ok) return
    try {
      await deleteAccount(acc.platform, acc.account)
      toast.success('已删除')
      await load()
    } catch (e) {
      toast.error(`删除失败：${errorMessage(e)}`)
    }
  }

  // 按平台分组
  const grouped = new Map<string, Account[]>()
  for (const acc of accounts) {
    const list = grouped.get(acc.platform) ?? []
    list.push(acc)
    grouped.set(acc.platform, list)
  }
  const platformOrder = [
    ...PLATFORMS.map((m) => m.key).filter((k) => grouped.has(k)),
    ...[...grouped.keys()].filter((k) => !PLATFORMS.some((m) => m.key === k)),
  ]
  const visiblePlatforms = filter ? [filter] : platformOrder

  const renderAccountCard = (acc: Account, i: number) => {
    const key = accountKey(acc)
    const check = checkResults[key]
    // 本地校验结果优先于列表返回的 valid 字段
    const checked = check && !('error' in check) ? check : null
    const invalid = checked ? !checked.valid : acc.valid === false
    const reason = checked && !checked.valid ? checked.detail : invalid ? acc.detail : undefined
    return (
      <div
        key={key}
        className="vh-card enter-item p-4"
        style={{ animationDelay: `${Math.min(i * 50, 200)}ms` }}
      >
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="break-all font-medium text-ink-1" style={{ fontSize: 15 }}>
                {acc.account}
              </span>
              {cookieBadge(acc, checked)}
            </div>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-3">
              {acc.updated_at && <span className="tnum">更新于 {formatTime(acc.updated_at)}</span>}
              {invalid && reason && <span className="break-all text-danger">失效原因：{reason}</span>}
            </div>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            {acc.cookie_exists && (
              <Button loading={checking[key]} onClick={() => handleCheck(acc)}>
                校验
              </Button>
            )}
            {QR_LOGIN_PLATFORMS.includes(acc.platform) && (
              <Button
                variant={acc.cookie_exists ? 'secondary' : 'primary'}
                icon={<QrCode size={13} />}
                onClick={() => openLogin(acc.platform, acc.account, true)}
              >
                {acc.cookie_exists ? '重新登录' : '扫码登录'}
              </Button>
            )}
            <Button loading={uploading[key]} onClick={() => fileRefs.current[key]?.click()}>
              上传文件
            </Button>
            <Button onClick={() => openImport(acc.platform, acc.account, true)}>
              导入
            </Button>
            {acc.cookie_exists && (
              <Button loading={exporting[key]} onClick={() => handleExport(acc)}>
                导出
              </Button>
            )}
            {acc.cookie_exists && (
              <Button
                variant="text"
                style={{ color: 'var(--danger)' }}
                onClick={() => handleDelete(acc)}
              >
                删除
              </Button>
            )}
            <input
              ref={(el) => {
                fileRefs.current[key] = el
              }}
              type="file"
              accept=".json"
              className="hidden"
              onChange={(e: ChangeEvent<HTMLInputElement>) => {
                const f = e.target.files?.[0]
                if (f) handleUpload(acc, f)
              }}
            />
          </div>
        </div>

        {/* 校验结果 */}
        {check && (
          <Alert
            className="mt-3"
            type={'error' in check ? 'error' : check.valid ? 'success' : 'warning'}
          >
            {'error' in check
              ? `校验失败：${check.error}`
              : check.valid
                ? `有效${check.detail ? `：${check.detail}` : ''}`
                : `无效${check.detail ? `：${check.detail}` : ''}`}
          </Alert>
        )}
      </div>
    )
  }

  return (
    <div>
      <PageHeader
        title="平台账号"
        desc="管理各平台的登录账号，发布功能依赖有效的 cookie"
        extra={
          <>
            <Button icon={<ClipboardText size={15} />} onClick={() => openImport()}>
              导入 Cookie
            </Button>
            <Button variant="primary" icon={<QrCode size={15} weight="bold" />} onClick={() => openLogin()}>
              扫码登录
            </Button>
          </>
        }
      />

      {/* 平台汇总卡片：移动端 2×2，桌面一行四个 */}
      <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
        {PLATFORMS.map((m) => {
          const count = grouped.get(m.key)?.length ?? 0
          const active = filter === m.key
          return (
            <button
              key={m.key}
              type="button"
              aria-pressed={active}
              onClick={() => setFilter((f) => (f === m.key ? null : m.key))}
              className={`vh-card flex items-center gap-3 p-4 text-left transition-all hover:border-line ${
                active ? 'border-accent bg-accent-soft' : ''
              }`}
              style={active ? { borderColor: 'var(--accent)' } : undefined}
            >
              <PlatformIcon platform={m.key} size={32} />
              <div className="min-w-0">
                <div className="truncate text-[13px] font-medium text-ink-1">{m.label}</div>
                <div className="mt-0.5 text-xs text-ink-3">
                  <span className="tnum">{count}</span> 个账号
                </div>
              </div>
            </button>
          )
        })}
      </div>

      {loading ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <Panel key={i} pad="sm">
              <div className="space-y-2.5">
                <div className="skeleton h-4 w-2/5" />
                <div className="skeleton h-3.5 w-3/5" />
              </div>
            </Panel>
          ))}
        </div>
      ) : accounts.length === 0 ? (
        <Panel pad="none">
          <EmptyState
            icon={<UserCircle size={28} />}
            title="暂无账号"
            desc="扫码登录或粘贴 cookie 字符串，即可添加平台账号"
            action={
              <>
                <Button variant="primary" icon={<QrCode size={15} />} onClick={() => openLogin()}>
                  扫码登录
                </Button>
                <Button icon={<ClipboardText size={15} />} onClick={() => openImport()}>
                  导入 Cookie
                </Button>
              </>
            }
          />
        </Panel>
      ) : (
        <div className="max-w-3xl space-y-5">
          {visiblePlatforms.map((platform) => {
            const meta = metaOf(platform)
            const list = grouped.get(platform) ?? []
            return (
              <div key={platform}>
                <div className="mb-2 flex items-center gap-2">
                  <PlatformIcon platform={platform} size={20} />
                  <span className="section-title">{meta.label}</span>
                  <span className="text-xs text-ink-3">{list.length} 个账号</span>
                </div>
                {list.length === 0 ? (
                  <Panel pad="none">
                    <EmptyState
                      icon={<UserCircle size={24} />}
                      title={`暂无${meta.label}账号`}
                      desc="扫码登录或导入 cookie 添加账号"
                      action={
                        <>
                          {QR_LOGIN_PLATFORMS.includes(platform) && (
                            <Button
                              variant="primary"
                              icon={<QrCode size={13} />}
                              onClick={() => openLogin(platform)}
                            >
                              扫码登录
                            </Button>
                          )}
                          <Button onClick={() => openImport(platform)}>
                            导入 Cookie
                          </Button>
                        </>
                      }
                    />
                  </Panel>
                ) : (
                  <div className="space-y-3">{list.map(renderAccountCard)}</div>
                )}
              </div>
            )
          })}
        </div>
      )}

      <AccountsLoginModal
        open={loginOpen}
        platform={loginPlatform}
        account={loginAccount}
        lockTarget={loginLock}
        onClose={() => setLoginOpen(false)}
        onSuccess={load}
      />

      <AccountsImportModal
        open={importOpen}
        platform={importPlatform}
        account={importAccount}
        lockTarget={importLock}
        onClose={() => setImportOpen(false)}
        onImported={load}
      />

      <AccountsExportModal data={exportData} onClose={() => setExportData(null)} />
    </div>
  )
}
