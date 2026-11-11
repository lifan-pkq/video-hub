import axios from 'axios'

/** 所有 /api 请求共用的客户端。页面不要自己 new axios。 */
const http = axios.create({
  baseURL: '/api',
  timeout: 120000,
})

export default http

export class ApiError extends Error {
  code: string
  action: string
  status: number

  constructor(message: string, code = 'http', action = '', status = 0) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.action = action
    this.status = status
  }
}

export function raiseApi(error: unknown): never {
  if (axios.isAxiosError(error)) {
    const detail = (error.response?.data as { detail?: unknown } | undefined)?.detail
    if (detail && typeof detail === 'object') {
      const record = detail as { message?: string; code?: string; action?: string }
      throw new ApiError(record.message || '请求失败', record.code || 'http', record.action || '', error.response?.status || 0)
    }
    if (typeof detail === 'string' && detail) throw new ApiError(detail, 'http', '', error.response?.status || 0)
  }
  throw error instanceof Error ? error : new ApiError('请求失败')
}

export interface Problem {
  code: string
  message: string
  action: string
}

export function errorDetail(e: unknown): Problem {
  if (e instanceof ApiError) return { code: e.code || 'http', message: e.message, action: e.action || '' }
  if (axios.isAxiosError(e)) {
    const detail = (e.response?.data as { detail?: unknown } | undefined)?.detail
    if (typeof detail === 'string' && detail) return { code: 'http', message: detail, action: '' }
    if (detail && typeof detail === 'object') {
      const record = detail as { code?: string; message?: string; action?: string }
      return { code: record.code || 'http', message: record.message || '请求失败', action: record.action || '' }
    }
    return { code: 'http', message: e.message || '请求失败', action: '' }
  }
  if (e instanceof Error) return { code: 'error', message: e.message, action: '' }
  return { code: 'error', message: '请求失败', action: '' }
}

export function errorMessage(e: unknown): string {
  const problem = errorDetail(e)
  return problem.action ? `${problem.message} ${problem.action}` : problem.message
}
