import { useEffect, useState } from 'react'
import { CheckCircle } from '@phosphor-icons/react'
import { errorMessage, importCookieString, PLATFORMS } from '../api'
import { Alert, Button, Input, Modal, PlatformIcon, Segment, TextArea } from '../ui'
import { detectCookieContent, metaOf, type CookieDetect } from './AccountsShared'

const ACCOUNT_NAME_RE = /^[A-Za-z0-9_-]+$/

interface Props {
  open: boolean
  /** 初始平台 */
  platform: string
  /** 初始账号名；lockTarget 时锁定（账号卡「导入」场景） */
  account: string
  lockTarget?: boolean
  onClose: () => void
  /** 导入成功（点「完成」关闭时）回调，用于刷新列表 */
  onImported: () => void
}

/** cookie 字符串导入弹窗：选平台/账号 → 粘贴（三种格式自动识别）→ 展示识别结果 */
export default function AccountsImportModal({
  open,
  platform: initPlatform,
  account: initAccount,
  lockTarget = false,
  onClose,
  onImported,
}: Props) {
  const [platform, setPlatform] = useState(initPlatform)
  const [account, setAccount] = useState(initAccount)
  const [content, setContent] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [importing, setImporting] = useState(false)
  const [result, setResult] = useState<{ count: number; detect: CookieDetect | null } | null>(null)

  // 打开时重置
  useEffect(() => {
    if (open) {
      setPlatform(initPlatform)
      setAccount(initAccount)
      setContent('')
      setError(null)
      setImporting(false)
      setResult(null)
    }
  }, [open, initPlatform, initAccount])

  const detect = detectCookieContent(content)

  const handleImport = async () => {
    const name = account.trim()
    if (!ACCOUNT_NAME_RE.test(name)) {
      setError('账号名只能包含字母、数字、下划线和短横线')
      return
    }
    const text = content.trim()
    if (!text) {
      setError('请先粘贴 cookie 内容')
      return
    }
    setError(null)
    setImporting(true)
    try {
      const r = await importCookieString(platform, name, text)
      setResult({ count: r.count, detect })
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setImporting(false)
    }
  }

  const handleDone = () => {
    onImported()
    onClose()
  }

  const requestClose = () => {
    if (importing) return
    onClose()
  }

  return (
    <Modal
      open={open}
      onClose={requestClose}
      title={lockTarget ? `导入 Cookie · ${metaOf(initPlatform).label} / ${initAccount}` : '导入 Cookie'}
      width={520}
      maskClosable={!importing}
    >
      {result ? (
        <div className="py-4 text-center">
          <CheckCircle size={44} weight="fill" className="mx-auto text-success" />
          <div className="mt-3 text-[15px] font-medium text-ink-1">导入成功</div>
          <div className="mt-2 text-[13px] leading-relaxed text-ink-2">
            识别到 {result.count} 个 cookie
            {result.detect ? `（${result.detect.format}）` : ''}
            {result.detect && result.detect.domains.length > 0 && (
              <>
                <br />
                域名：{result.detect.domains.join('、')}
              </>
            )}
          </div>
          <div className="mt-1.5 text-[12px] text-ink-3">
            已保存到 {metaOf(platform).label} / {account.trim()}
          </div>
          <Button variant="primary" full onClick={handleDone} className="mt-5">
            完成
          </Button>
        </div>
      ) : (
        <div>
          {!lockTarget && (
            <>
              <div className="text-xs text-ink-3">平台</div>
              <Segment
                className="mt-2"
                value={platform}
                onChange={setPlatform}
                options={PLATFORMS.map((m) => ({
                  value: m.key,
                  label: (
                    <span className="inline-flex items-center gap-1.5">
                      <PlatformIcon platform={m.key} size={14} />
                      {m.label}
                    </span>
                  ),
                }))}
              />
              <div className="mt-4 text-xs text-ink-3">账号名</div>
              <Input
                className="mt-2"
                value={account}
                onChange={setAccount}
                placeholder="字母、数字、_、-，如 default"
              />
            </>
          )}

          <div className="mt-4 text-xs leading-relaxed text-ink-3">
            获取方式：浏览器登录对应平台 → F12 → 网络 → 点任意请求 → 请求头 Cookie 整段复制粘贴到下方。
          </div>
          <TextArea
            className="mt-2 font-mono text-[12px]"
            rows={8}
            value={content}
            onChange={setContent}
            placeholder={
              '粘贴 cookie，自动识别三种格式：\n1. storage_state JSON（{"cookies":[…]}）\n2. Netscape 文本（tab 分隔）\n3. 请求头字符串（name=val; name2=val2; …）'
            }
          />
          {content.trim() && (
            <div className="mt-2 text-[12px] text-ink-3">
              {detect ? (
                <>
                  已识别：<span className="text-ink-1">{detect.format}</span>，约 {detect.count} 个
                  cookie
                  {detect.domains.length > 0 && `，域名 ${detect.domains.join('、')}`}
                </>
              ) : (
                '未能识别格式，仍可尝试提交（由服务端解析）'
              )}
            </div>
          )}

          {error && (
            <Alert type="error" className="mt-3">
              {error}
            </Alert>
          )}

          <Button
            variant="primary"
            full
            loading={importing}
            disabled={!content.trim() || !account.trim()}
            onClick={handleImport}
            className="mt-4"
          >
            确认导入
          </Button>
        </div>
      )}
    </Modal>
  )
}
