import { useCallback, useEffect, useState } from 'react'
import { Plus, Trash, UploadSimple, UserCircle } from '@phosphor-icons/react'
import { Button, EmptyState, Input, PageHeader, Panel, ProblemAlert, Select, TextArea, confirmDialog, toast } from '../ui'
import RequestState from '../ui/RequestState'
import {
  deleteProfile,
  fetchProfiles,
  saveProfile,
  uploadProfileReference,
  type CreativeProfile,
  type ProfileKind,
} from '../creative'
import { errorMessage } from '../api'

const KIND_OPTIONS = [
  { value: 'characters', label: '人物档案' },
  { value: 'scenes', label: '场景档案' },
  { value: 'styles', label: '系列风格' },
  { value: 'preferences', label: '默认偏好与词典' },
]

const FIELD_LABELS: Record<string, string> = {
  description: '说明',
  fixed_features: '固定面部特征',
  clothing: '固定服装',
  age_rules: '年龄变化规则',
  voice: '声音特征',
  catchphrases: '口头禅（避免每段滥用）',
  layout: '固定摆设',
  lighting: '光线',
  color_palette: '场景色调',
  narration_tone: '旁白语气',
  color_grade: '统一色彩',
  transitions: '转场规则',
  music: '音乐情绪',
}

const KIND_FIELDS: Record<ProfileKind, string[]> = {
  characters: ['description', 'fixed_features', 'clothing', 'age_rules', 'voice', 'catchphrases'],
  scenes: ['description', 'layout', 'lighting', 'color_palette'],
  styles: ['description', 'narration_tone', 'color_grade', 'transitions', 'music'],
  preferences: ['description'],
}

export default function ProfilesPage() {
  const [kind, setKind] = useState<ProfileKind>('characters')
  const [profiles, setProfiles] = useState<CreativeProfile[]>([])
  const [form, setForm] = useState<Partial<CreativeProfile>>({ name: '' })
  const [saving, setSaving] = useState(false)
  const [loading, setLoading] = useState(true)
  const [hasData, setHasData] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [formError, setFormError] = useState('')
  const [pronunciation, setPronunciation] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const rows = await fetchProfiles(kind)
      setProfiles(rows)
      setHasData(true)
      setLoadError(null)
      if (kind === 'preferences' && rows[0]) {
        setForm(rows[0])
        setPronunciation(Object.entries(rows[0].pronunciation ?? {}).map(([key, value]) => `${key}=${value}`).join('\n'))
      }
    } catch (error) {
      setLoadError(errorMessage(error))
    } finally {
      setLoading(false)
    }
  }, [kind])

  useEffect(() => {
    setForm({ name: '' })
    setPronunciation('')
    setFormError('')
    void load()
  }, [load])

  const update = (key: string, value: unknown) => setForm((current) => ({ ...current, [key]: value }))

  const save = async () => {
    const name = form.name?.trim() ?? ''
    if (!name) {
      setFormError('先填写档案名称，例如人物名、场景名或系列名。')
      return
    }
    if (name.length > 80) {
      setFormError('名称不能超过 80 个字。')
      return
    }
    if (kind === 'preferences') {
      const bad = pronunciation.split('\n').map((line) => line.trim()).filter(Boolean).find((line) => !line.includes('='))
      if (bad) {
        setFormError(`「${bad}」不是「原词=读音」格式。每行都要有等号，字幕仍保留原词。`)
        return
      }
    }
    if (kind === 'characters' && (form.references?.length ?? 0) > 5) {
      setFormError('人物参考图最多 5 张。先删掉多余的再保存。')
      return
    }
    setFormError('')
    setSaving(true)
    try {
      const payload = { ...form, name, kind }
      if (kind === 'preferences') {
        payload.pronunciation = Object.fromEntries(
          pronunciation.split('\n').map((line) => line.trim()).filter(Boolean).map((line) => {
            const index = line.indexOf('=')
            return [line.slice(0, index).trim(), line.slice(index + 1).trim()]
          }),
        )
      }
      const saved = await saveProfile(kind, payload, form.id)
      toast.success('档案已保存')
      setForm(saved)
      await load()
    } catch (error) {
      toast.error('保存失败：' + errorMessage(error))
    } finally {
      setSaving(false)
    }
  }

  const remove = async () => {
    if (!form.id) return
    if (!await confirmDialog({ title: '删除档案', content: `确定删除「${form.name}」吗？历史任务中的快照不会改变。`, danger: true })) return
    try {
      await deleteProfile(kind, form.id)
      setForm({ name: '' })
      await load()
    } catch (error) {
      toast.error('删除失败：' + errorMessage(error))
    }
  }

  const upload = async (files: FileList | null) => {
    if (!files) return
    const references = [...(form.references ?? [])]
    for (const file of Array.from(files)) {
      if (references.length >= 5) break
      try {
        references.push(await uploadProfileReference(file))
      } catch (error) {
        toast.error('上传参考图失败：' + errorMessage(error))
      }
    }
    update('references', references)
  }

  return (
    <section>
      <PageHeader
        title="创作档案"
        desc="固定人物、场景和系列风格。任务保存使用时的快照，之后修改档案不会改变历史成片。"
        extra={<Button variant="primary" icon={<Plus size={15} />} onClick={() => setForm({ name: '' })}>新建档案</Button>}
      />
      <div className="mb-4 max-w-xs"><Select value={kind} onChange={(value) => setKind(value as ProfileKind)} options={KIND_OPTIONS} /></div>
      <RequestState error={loadError} hasData={hasData} loading={loading} onRetry={load} className="mb-4" />
      {formError && <ProblemAlert message={formError} action="改完再点保存。历史任务里的档案快照不会变。" className="mb-4" />}
      <div className="grid gap-5 xl:grid-cols-[320px_minmax(0,1fr)]">
        <Panel title="已有档案">
          {loading && !hasData ? (
            <div className="space-y-2">{[0, 1, 2].map((item) => <div key={item} className="skeleton h-12 rounded-xl" />)}</div>
          ) : !hasData ? null : profiles.length === 0 ? (
            <EmptyState icon={<UserCircle size={25} />} title="这一类还没有档案" desc="在右侧填写名称后保存。人物档案可以带参考图，发音写在默认偏好里。" />
          ) : (
            <div className="space-y-2">
              {profiles.map((profile) => (
                <button key={profile.id} type="button" onClick={() => {
                  setForm(profile)
                  if (kind === 'preferences') setPronunciation(Object.entries(profile.pronunciation ?? {}).map(([key, value]) => `${key}=${value}`).join('\n'))
                }} className="w-full rounded-xl border border-line-soft bg-fill px-3 py-2.5 text-left hover:border-accent">
                  <strong className="block text-[13px] text-ink-1">{profile.name}</strong>
                  <span className="text-[11px] text-ink-4">版本 {profile.version}{profile.description ? ` · ${profile.description}` : ''}</span>
                </button>
              ))}
            </div>
          )}
        </Panel>
        <Panel title={form.id ? `编辑 · ${form.name}` : '新档案'}>
          <div className="space-y-4">
            <label className="block"><span className="mb-1.5 block text-xs text-ink-3">名称</span><Input value={form.name ?? ''} onChange={(value) => update('name', value)} placeholder="例如：陈默 / 家里 / 回声系列" /></label>
            {KIND_FIELDS[kind].map((field) => (
              <label key={field} className="block">
                <span className="mb-1.5 block text-xs text-ink-3">{FIELD_LABELS[field]}</span>
                <TextArea value={String(form[field as keyof CreativeProfile] ?? '')} onChange={(value) => update(field, value)} rows={field === 'description' ? 2 : 3} />
              </label>
            ))}
            {kind === 'characters' && (
              <div className="rounded-xl border border-line-soft bg-fill p-3">
                <p className="text-xs text-ink-2">标准参考图（建议 3–5 张）</p>
                <p className="mt-1 text-[11px] text-ink-4">生成时会强制携带，固定特征和年龄规则也会一并发送。</p>
                <input type="file" accept="image/*" multiple onChange={(event) => { void upload(event.target.files); event.currentTarget.value = '' }} className="mt-3 text-xs" />
                <div className="mt-3 flex flex-wrap gap-2">
                  {(form.references ?? []).map((reference, index) => (
                    <div key={reference.name} className="relative">
                      <img src={reference.url} alt="人物标准参考" className="h-20 w-20 rounded-lg object-cover" />
                      <button type="button" aria-label="移除参考图" onClick={() => update('references', (form.references ?? []).filter((_, itemIndex) => itemIndex !== index))} className="absolute right-1 top-1 rounded bg-black/70 p-1 text-white"><Trash size={11} /></button>
                    </div>
                  ))}
                </div>
                {!(form.references ?? []).length && <UploadSimple size={22} className="mt-3 text-ink-4" />}
              </div>
            )}
            {kind === 'preferences' && (
              <label className="block">
                <span className="mb-1.5 block text-xs text-ink-3">专属发音词典（每行：原词=TTS 读音）</span>
                <TextArea value={pronunciation} onChange={setPronunciation} rows={8} placeholder={'陈默=陈莫\n重庆=崇庆'} />
                <span className="mt-1 block text-[11px] text-ink-4">只改变朗读文本，字幕始终保留原词。</span>
              </label>
            )}
            <div className="flex justify-end gap-2">
              {form.id && <Button variant="danger" onClick={() => void remove()}>删除</Button>}
              <Button variant="primary" loading={saving} onClick={() => void save()}>保存档案</Button>
            </div>
          </div>
        </Panel>
      </div>
    </section>
  )
}
