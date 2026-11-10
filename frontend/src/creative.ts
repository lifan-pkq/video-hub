import http from './api'

export type ProfileKind = 'characters' | 'scenes' | 'styles' | 'preferences'

export interface CreativeProfile {
  id: string
  kind: ProfileKind
  name: string
  version: number
  description?: string
  fixed_features?: string
  clothing?: string
  age_rules?: string
  voice?: string
  catchphrases?: string
  layout?: string
  lighting?: string
  color_palette?: string
  color_grade?: string
  subtitle?: Record<string, unknown>
  transitions?: string
  music?: string
  narration_tone?: string
  pronunciation?: Record<string, string>
  references?: Array<{ name: string; url: string; size: number; data_uri?: string }>
}

export async function fetchProfiles(kind: ProfileKind): Promise<CreativeProfile[]> {
  const { data } = await http.get<CreativeProfile[]>('/profiles/' + kind)
  return data
}

export async function saveProfile(kind: ProfileKind, profile: Partial<CreativeProfile>, id = ''): Promise<CreativeProfile> {
  const { data } = id
    ? await http.put<CreativeProfile>(`/profiles/${kind}/${id}`, profile)
    : await http.post<CreativeProfile>(`/profiles/${kind}`, profile)
  return data
}

export async function deleteProfile(kind: ProfileKind, id: string): Promise<void> {
  await http.delete(`/profiles/${kind}/${id}`)
}

export async function uploadProfileReference(file: File): Promise<{ name: string; url: string; size: number }> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await http.post('/profiles/references', form)
  return data
}

