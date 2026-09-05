/** Thin API client. Every call surfaces the server's own error text so the UI
 *  can explain what actually went wrong instead of showing "something failed". */

async function request(path, { method = 'GET', body, isForm } = {}) {
  const options = { method, headers: {} }
  if (body !== undefined) {
    if (isForm) {
      options.body = body
    } else {
      options.headers['Content-Type'] = 'application/json'
      options.body = JSON.stringify(body)
    }
  }
  const res = await fetch(`/api${path}`, options)
  const text = await res.text()
  let data
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = { detail: text }
  }
  if (!res.ok) {
    throw new Error(data?.detail || `Request failed (${res.status})`)
  }
  return data
}

export const api = {
  config: () => request('/config'),
  uploadDocument(file) {
    const form = new FormData()
    form.append('file', file)
    return request('/documents', { method: 'POST', body: form, isForm: true })
  },
  startLesson: (payload) => request('/lessons', { method: 'POST', body: payload }),
  step: (id) => request(`/lessons/${id}/step`, { method: 'POST' }),
  answer: (id, answer) => request(`/lessons/${id}/answer`, { method: 'POST', body: { answer } }),
  answerByVoice(id, blob) {
    const form = new FormData()
    form.append('audio', blob, 'answer.webm')
    return request(`/lessons/${id}/answer/voice`, { method: 'POST', body: form, isForm: true })
  },
  ask: (id, question) => request(`/lessons/${id}/ask`, { method: 'POST', body: { question } }),
  switchLanguage: (id, language, label) =>
    request(`/lessons/${id}/language`, { method: 'POST', body: { language, label } }),
  buildVideo: (id) => request(`/lessons/${id}/video`, { method: 'POST' }),
  videoStatus: (id) => request(`/lessons/${id}/video`),
  notes: (id) => request(`/lessons/${id}/notes`),
  progress: (studentId) => request(`/students/${studentId}/progress`),
}

export const LANGUAGES = [
  { code: 'en', label: 'English' },
  { code: 'hi', label: 'हिन्दी' },
  { code: 'hinglish', label: 'Hinglish' },
  { code: 'bn', label: 'বাংলা' },
  { code: 'ta', label: 'தமிழ்' },
  { code: 'te', label: 'తెలుగు' },
  { code: 'mr', label: 'मराठी' },
  { code: 'gu', label: 'ગુજરાતી' },
  { code: 'kn', label: 'ಕನ್ನಡ' },
  { code: 'ml', label: 'മലയാളം' },
  { code: 'pa', label: 'ਪੰਜਾਬੀ' },
  { code: 'ur', label: 'اردو' },
  { code: 'es', label: 'Español' },
  { code: 'fr', label: 'Français' },
  { code: 'ar', label: 'العربية' },
]
