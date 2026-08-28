import { PLATFORMS, type Account } from '../api'

export function metaOf(platform: string): { key: string; label: string } {
  return PLATFORMS.find((m) => m.key === platform) ?? { key: platform, label: platform }
}

export function accountKey(a: Account): string {
  return `${a.platform}:${a.account}`
}

/** 复制到剪贴板：优先 navigator.clipboard，非安全上下文降级 textarea + execCommand */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (window.isSecureContext && navigator.clipboard) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // 继续走降级方案
  }
  const ta = document.createElement('textarea')
  ta.value = text
  ta.style.position = 'fixed'
  ta.style.opacity = '0'
  document.body.appendChild(ta)
  ta.focus()
  ta.select()
  try {
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    document.body.removeChild(ta)
  }
}

export type CookieFormat = 'storage_state JSON' | 'Netscape 文本' | '请求头字符串'

export interface CookieDetect {
  format: CookieFormat
  count: number
  domains: string[]
}

interface RawCookie {
  name?: unknown
  domain?: unknown
}

/** 客户端识别粘贴的 cookie 内容：三种格式自动识别，返回数量与域名（用于导入结果展示） */
export function detectCookieContent(content: string): CookieDetect | null {
  const text = content.trim()
  if (!text) return null

  // 1. storage_state JSON：{ cookies: [...] } 或 cookie 对象数组
  if (text.startsWith('{') || text.startsWith('[')) {
    try {
      const j: unknown = JSON.parse(text)
      const cookies: RawCookie[] | null = Array.isArray(j)
        ? (j as RawCookie[])
        : j && typeof j === 'object' && Array.isArray((j as { cookies?: unknown }).cookies)
          ? ((j as { cookies: RawCookie[] }).cookies)
          : null
      if (cookies && cookies.length > 0 && cookies.every((c) => c && typeof c.name === 'string')) {
        const domains = [
          ...new Set(
            cookies
              .map((c) => (typeof c.domain === 'string' ? c.domain.replace(/^\./, '') : ''))
              .filter(Boolean),
          ),
        ]
        return { format: 'storage_state JSON', count: cookies.length, domains }
      }
    } catch {
      // 不是合法 JSON，继续尝试其它格式
    }
    return null
  }

  // 2. Netscape 文本：每行 7 个 tab 分隔字段，# 开头为注释
  const lines = text
    .split(/\r?\n/)
    .map((l) => l.trimEnd())
    .filter((l) => l.length > 0)
  const dataLines = lines.filter((l) => !l.startsWith('#'))
  if (
    dataLines.length > 0 &&
    lines.every((l) => l.startsWith('#') || l.split('\t').length >= 7)
  ) {
    const domains = [
      ...new Set(dataLines.map((l) => l.split('\t')[0].replace(/^\./, '')).filter(Boolean)),
    ]
    return { format: 'Netscape 文本', count: dataLines.length, domains }
  }

  // 3. 请求头字符串：name=value; name2=value2
  if (text.includes('=')) {
    const pairs = text
      .split(';')
      .map((s) => s.trim())
      .filter((s) => /^[^=;\s]+=/.test(s))
    if (pairs.length > 0 && pairs.join('; ').replace(/;\s*$/, '') === text.replace(/;\s*$/, '')) {
      return { format: '请求头字符串', count: pairs.length, domains: [] }
    }
  }
  return null
}
