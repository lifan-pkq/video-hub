import { useEffect } from 'react'
import { Copy } from '@phosphor-icons/react'
import type { CookieExportResult } from '../api'
import { Button, Modal, TextArea, toast } from '../ui'
import { copyText, metaOf } from './AccountsShared'

interface Props {
  /** null = 关闭 */
  data: CookieExportResult | null
  onClose: () => void
}

/** cookie 导出弹窗：展示字符串 + 复制按钮（打开时自动复制一次） */
export default function AccountsExportModal({ data, onClose }: Props) {
  // 打开时自动复制到剪贴板
  useEffect(() => {
    if (!data) return
    let cancelled = false
    void copyText(data.cookie_string).then((ok) => {
      if (cancelled) return
      if (ok) toast.success('已复制到剪贴板')
      else toast.warning('自动复制失败，请手动复制')
    })
    return () => {
      cancelled = true
    }
  }, [data])

  const handleCopy = async () => {
    if (!data) return
    if (await copyText(data.cookie_string)) toast.success('已复制到剪贴板')
    else toast.error('复制失败，请手动选择复制')
  }

  return (
    <Modal
      open={!!data}
      onClose={onClose}
      title={data ? `导出 Cookie · ${metaOf(data.platform).label} / ${data.account}` : '导出 Cookie'}
      width={520}
    >
      <div className="text-xs leading-relaxed text-ink-3">
        cookie 字符串已尝试自动复制到剪贴板，也可手动选中下方内容复制。
      </div>
      <TextArea
        className="mt-3 font-mono text-[12px]"
        rows={8}
        value={data?.cookie_string ?? ''}
        onChange={() => {}}
        readOnly
        onFocus={(e) => e.target.select()}
      />
      <Button variant="primary" full icon={<Copy size={15} />} onClick={handleCopy} className="mt-4">
        复制
      </Button>
    </Modal>
  )
}
