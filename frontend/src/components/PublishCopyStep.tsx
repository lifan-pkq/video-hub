import type { KeyboardEvent } from 'react'
import { X } from '@phosphor-icons/react'
import { Input, Tag, TextArea } from '../ui'

export const TITLE_MAX = 55

interface Props {
  title: string
  desc: string
  tags: string[]
  tagInput: string
  onTitleChange: (v: string) => void
  onDescChange: (v: string) => void
  onTagsChange: (tags: string[]) => void
  onTagInputChange: (v: string) => void
}

/** 第②步：填写文案（标题必填 / 简介 / 标签） */
export default function PublishCopyStep({
  title,
  desc,
  tags,
  tagInput,
  onTitleChange,
  onDescChange,
  onTagsChange,
  onTagInputChange,
}: Props) {
  const addTag = () => {
    const t = tagInput.trim().replace(/^#/, '')
    if (t && !tags.includes(t)) onTagsChange([...tags, t])
    onTagInputChange('')
  }

  return (
    <div className="space-y-4">
      <div>
        <label className="mb-1.5 block text-[13px] text-ink-2">
          标题 <span className="text-danger">*</span>
          <span className="ml-2 text-xs text-ink-3">（小红书限制20字以内）</span>
        </label>
        <Input
          value={title}
          onChange={onTitleChange}
          maxLength={TITLE_MAX}
          placeholder={`必填，最长 ${TITLE_MAX} 字`}
          suffix={
            <span className="tnum text-xs">
              {title.length}/{TITLE_MAX}
            </span>
          }
        />
      </div>

      <div>
        <label className="mb-1.5 block text-[13px] text-ink-2">简介</label>
        <TextArea value={desc} onChange={onDescChange} rows={4} placeholder="视频简介（可选）" />
      </div>

      <div>
        <label className="mb-1.5 block text-[13px] text-ink-2">标签（回车添加）</label>
        <Input
          value={tagInput}
          onChange={onTagInputChange}
          placeholder="输入标签后回车"
          onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              addTag()
            }
          }}
        />
        {tags.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-2">
            {tags.map((t) => (
              <Tag key={t} color="blue">
                #{t}
                <button
                  type="button"
                  aria-label={`删除标签 ${t}`}
                  onClick={() => onTagsChange(tags.filter((x) => x !== t))}
                  className="-mr-0.5 flex items-center opacity-60 transition-opacity hover:opacity-100"
                >
                  <X size={10} weight="bold" />
                </button>
              </Tag>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
