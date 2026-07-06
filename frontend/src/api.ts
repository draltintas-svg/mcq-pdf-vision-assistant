export const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

export type DocumentInfo = {
  id: number
  filename: string
  status: string
  message?: string | null
  question_count: number
  created_at: string
}

export type Candidate = {
  id: number
  score: number
  source_page?: number | null
  question_text: string
  options: Record<string, string>
  correct_answer: string
  explanation?: string | null
}

export type AnswerResult = {
  source: string
  confidence?: number | null
  extracted_question?: string | null
  answer: string
  explanation?: string | null
  matched_question?: Candidate | null
  candidates: Candidate[]
  web_answer?: string | null
  warning?: string | null
}

export type Question = {
  id: number
  document_id: number
  question_number?: string | null
  source_page?: number | null
  question_text: string
  options: Record<string, string>
  correct_answer: string
  explanation?: string | null
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    const detail = payload?.detail || response.statusText
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return response.json() as Promise<T>
}

export async function uploadPdf(file: File) {
  const form = new FormData()
  form.append('file', file)
  const response = await fetch(`${API_BASE}/api/upload-pdf`, {
    method: 'POST',
    body: form,
  })
  return parseResponse<{
    document_id: number
    filename: string
    status: string
    message?: string | null
    question_count: number
  }>(response)
}


export async function uploadJson(file: File) {
  const form = new FormData()
  form.append('file', file)
  const response = await fetch(`${API_BASE}/api/upload-json`, {
    method: 'POST',
    body: form,
  })
  return parseResponse<{
    document_id: number
    filename: string
    status: string
    message?: string | null
    question_count: number
  }>(response)
}

export async function fetchDocuments() {
  const response = await fetch(`${API_BASE}/api/documents`)
  return parseResponse<DocumentInfo[]>(response)
}

export async function fetchQuestions(limit = 25) {
  const response = await fetch(`${API_BASE}/api/questions?limit=${limit}`)
  return parseResponse<Question[]>(response)
}

export async function askQuestion(params: {
  text: string
  image?: File | null
  allowWebFallback: boolean
}) {
  const form = new FormData()
  if (params.text.trim()) form.append('text', params.text.trim())
  form.append('allow_web_fallback', String(params.allowWebFallback))
  if (params.image) form.append('image', params.image)
  const response = await fetch(`${API_BASE}/api/answer`, {
    method: 'POST',
    body: form,
  })
  return parseResponse<AnswerResult>(response)
}

export async function updateQuestion(id: number, payload: { correct_answer?: string; explanation?: string }) {
  const response = await fetch(`${API_BASE}/api/questions/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return parseResponse<Question>(response)
}
