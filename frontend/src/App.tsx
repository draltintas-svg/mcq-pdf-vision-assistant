import { FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import {
  AnswerResult,
  Candidate,
  DocumentInfo,
  Question,
  askQuestion,
  fetchDocuments,
  fetchQuestions,
  updateQuestion,
  uploadJson,
  uploadPdf,
} from './api'

function App() {
  const [pdfFile, setPdfFile] = useState<File | null>(null)
  const [jsonFile, setJsonFile] = useState<File | null>(null)
  const [imageFile, setImageFile] = useState<File | null>(null)
  const [questionText, setQuestionText] = useState('')
  const [allowWebFallback, setAllowWebFallback] = useState(false)
  const [documents, setDocuments] = useState<DocumentInfo[]>([])
  const [questions, setQuestions] = useState<Question[]>([])
  const [answer, setAnswer] = useState<AnswerResult | null>(null)
  const [loading, setLoading] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const isFastRoute = window.location.pathname.toLowerCase().startsWith('/fast')

  const hasLocalQuestions = useMemo(
    () => documents.some((doc) => doc.status === 'ready' && doc.question_count > 0),
    [documents],
  )

  async function refresh() {
    const [docList, questionList] = await Promise.all([fetchDocuments(), fetchQuestions(30)])
    setDocuments(docList)
    setQuestions(questionList)
  }

  useEffect(() => {
    refresh().catch((err) => setError(err.message))
  }, [])

  async function handleUpload(event: FormEvent) {
    event.preventDefault()
    if (!pdfFile) return
    setError(null)
    setLoading('pdf')
    try {
      await uploadPdf(pdfFile)
      setPdfFile(null)
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload fehlgeschlagen')
    } finally {
      setLoading(null)
    }
  }

  async function handleUploadJson(event: FormEvent) {
    event.preventDefault()
    if (!jsonFile) return
    setError(null)
    setLoading('json')
    try {
      await uploadJson(jsonFile)
      setJsonFile(null)
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'JSON-Import fehlgeschlagen')
    } finally {
      setLoading(null)
    }
  }

  async function handleAsk(event: FormEvent) {
    event.preventDefault()
    if (!questionText.trim() && !imageFile) return
    setError(null)
    setAnswer(null)
    setLoading('answer')
    try {
      const result = await askQuestion({ text: questionText, image: imageFile, allowWebFallback })
      setAnswer(result)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Abfrage fehlgeschlagen')
    } finally {
      setLoading(null)
    }
  }

  async function saveQuestionAnswer(id: number, value: string) {
    setError(null)
    try {
      await updateQuestion(id, { correct_answer: value })
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Speichern fehlgeschlagen')
    }
  }

  if (isFastRoute) {
    return <FastCameraMode documents={documents} refresh={refresh} />
  }

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">PDF-/JSON-Fragenbank · lokale OCR · iPhone-Schnellscan</p>
          <h1>MCQ PDF Vision Assistant</h1>
          <p className="subtitle">
            Importiere eine strukturierte JSON-Fragenbank kostenlos ohne OpenAI. Für das iPhone gibt es jetzt einen Vollbild-Schnellscan, der nach einem Start automatisch Kamerabilder auswertet.
          </p>
        </div>
        <StatusBadge hasLocalQuestions={hasLocalQuestions} />
      </header>

      <section className="fast-entry card">
        <div>
          <p className="eyebrow">Schnellmodus</p>
          <h2>iPhone-Kamera öffnen</h2>
          <p className="muted">Einmal starten, Kamera auf die Frage halten, Ergebnis erscheint automatisch sehr groß im Vollbild. Keine OpenAI-Kosten, solange die Frage lokal importiert ist.</p>
        </div>
        <a className="fast-link" href="/fast">Schnellscan starten</a>
      </section>

      {error && <div className="alert error">{error}</div>}

      <section className="grid">
        <div className="card">
          <h2>1. Fragenbank importieren</h2>
          <p className="muted">
            In dieser Version ist die Biology-Fragenbank bereits als Startdatenbank gebündelt. Zusätzlich kannst du weitere strukturierte JSON-Dateien importieren.
          </p>

          <form onSubmit={handleUploadJson} className="stacked-form">
            <label className="dropzone">
              <input
                type="file"
                accept="application/json,.json,.jsonl"
                onChange={(event) => setJsonFile(event.target.files?.[0] ?? null)}
              />
              <span>{jsonFile ? jsonFile.name : 'Strukturierte JSON-Fragenbank auswählen'}</span>
            </label>
            <button disabled={!jsonFile || loading === 'json'}>
              {loading === 'json' ? 'JSON-Import läuft …' : 'JSON kostenlos importieren'}
            </button>
          </form>

          <hr />

          <form onSubmit={handleUpload} className="stacked-form">
            <p className="muted small-note">Optionaler PDF-Import: benötigt OpenAI-API-Guthaben für die automatische Analyse.</p>
            <label className="dropzone secondary">
              <input
                type="file"
                accept="application/pdf"
                onChange={(event) => setPdfFile(event.target.files?.[0] ?? null)}
              />
              <span>{pdfFile ? pdfFile.name : 'PDF auswählen'}</span>
            </label>
            <button disabled={!pdfFile || loading === 'pdf'}>
              {loading === 'pdf' ? 'PDF-Import läuft …' : 'PDF hochladen und analysieren'}
            </button>
          </form>
        </div>

        <form className="card accent" onSubmit={handleAsk}>
          <h2>2. Frage suchen</h2>
          <label>
            Foto der Frage
            <input
              type="file"
              accept="image/png,image/jpeg,image/webp"
              capture="environment"
              onChange={(event) => setImageFile(event.target.files?.[0] ?? null)}
            />
          </label>
          <label>
            Stichworte oder Fragetext
            <textarea
              value={questionText}
              onChange={(event) => setQuestionText(event.target.value)}
              placeholder="z. B. The basic structural and functional unit of life"
              rows={5}
            />
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={allowWebFallback}
              onChange={(event) => setAllowWebFallback(event.target.checked)}
            />
            Optionalen OpenAI-Web-Fallback erlauben, wenn lokal kein sicherer Treffer gefunden wird
          </label>
          <button disabled={(!questionText.trim() && !imageFile) || loading === 'answer'}>
            {loading === 'answer' ? 'Suche läuft …' : 'Lösung anzeigen'}
          </button>
        </form>
      </section>

      {answer && <AnswerCard answer={answer} />}

      <section className="grid lower-grid">
        <div className="card">
          <h2>Importierte Datenbanken</h2>
          {documents.length === 0 ? (
            <p className="muted">Noch keine Datenbank importiert. Beim ersten Start wird die gebündelte Biology-Fragenbank automatisch geladen.</p>
          ) : (
            <div className="list">
              {documents.map((doc) => (
                <article key={doc.id} className="list-item">
                  <strong>{doc.filename}</strong>
                  <span className={`pill ${doc.status}`}>{doc.status}</span>
                  <p>{doc.question_count} Fragen · {doc.message || 'Keine Meldung'}</p>
                </article>
              ))}
            </div>
          )}
        </div>

        <div className="card">
          <h2>Letzte Fragen</h2>
          {questions.length === 0 ? (
            <p className="muted">Noch keine Fragen erkannt.</p>
          ) : (
            <div className="list scroll-list">
              {questions.map((question) => (
                <article key={question.id} className="list-item compact">
                  <div className="question-title">
                    <strong>{question.question_number ? `${question.question_number}. ` : ''}{question.question_text}</strong>
                  </div>
                  <p className="muted">Seite {question.source_page ?? '–'} · Antwort: {question.correct_answer}</p>
                  {question.correct_answer === 'UNKNOWN' && (
                    <InlineAnswerEditor onSave={(value) => saveQuestionAnswer(question.id, value)} />
                  )}
                </article>
              ))}
            </div>
          )}
        </div>
      </section>
    </main>
  )
}

function FastCameraMode({ documents, refresh }: { documents: DocumentInfo[]; refresh: () => Promise<void> }) {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const intervalRef = useRef<number | null>(null)
  const runningRef = useRef(false)
  const requestInFlightRef = useRef(false)
  const streamRef = useRef<MediaStream | null>(null)
  const wakeLockRef = useRef<any>(null)
  const [started, setStarted] = useState(false)
  const [status, setStatus] = useState('Bereit')
  const [error, setError] = useState<string | null>(null)
  const [answer, setAnswer] = useState<AnswerResult | null>(null)
  const [lastScanAt, setLastScanAt] = useState<string>('')

  const readyCount = documents
    .filter((doc) => doc.status === 'ready')
    .reduce((sum, doc) => sum + doc.question_count, 0)

  useEffect(() => {
    if (documents.length === 0) {
      refresh().catch(() => undefined)
    }
  }, [documents.length, refresh])

  useEffect(() => {
    return () => stopCamera()
  }, [])

  async function startCamera() {
    setError(null)
    setStatus('Kamera startet …')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: { ideal: 'environment' },
          width: { ideal: 1920 },
          height: { ideal: 1080 },
        },
        audio: false,
      })
      streamRef.current = stream
      const video = videoRef.current
      if (!video) throw new Error('Videoelement nicht verfügbar.')
      video.srcObject = stream
      video.setAttribute('playsinline', 'true')
      video.muted = true
      await video.play()
      runningRef.current = true
      setStarted(true)
      setStatus('Automatischer Scan aktiv')
      try {
        wakeLockRef.current = await (navigator as any).wakeLock?.request?.('screen')
      } catch {
        wakeLockRef.current = null
      }
      window.setTimeout(() => void captureAndAsk(), 800)
      intervalRef.current = window.setInterval(() => void captureAndAsk(), 3800)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Kamera konnte nicht gestartet werden.')
      setStatus('Kamera blockiert')
    }
  }

  function stopCamera() {
    runningRef.current = false
    if (intervalRef.current) {
      window.clearInterval(intervalRef.current)
      intervalRef.current = null
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop())
      streamRef.current = null
    }
    try {
      wakeLockRef.current?.release?.()
    } catch {
      // ignore
    }
    wakeLockRef.current = null
    setStarted(false)
  }

  async function captureAndAsk() {
    if (!runningRef.current || requestInFlightRef.current) return
    const video = videoRef.current
    const canvas = canvasRef.current
    if (!video || !canvas || video.readyState < 2 || !video.videoWidth || !video.videoHeight) return

    requestInFlightRef.current = true
    setStatus('Scan läuft …')
    try {
      const blob = await captureFrame(video, canvas)
      if (!blob) throw new Error('Kamerabild konnte nicht gelesen werden.')
      const file = new File([blob], `iphone-scan-${Date.now()}.jpg`, { type: 'image/jpeg' })
      const result = await askQuestion({ text: '', image: file, allowWebFallback: false })
      setLastScanAt(new Date().toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' }))
      setAnswer(result)
      if (result.source === 'database_local' || result.source === 'database_embedding') {
        navigator.vibrate?.([80])
        setStatus('Treffer gefunden · weiter scannen aktiv')
      } else {
        setStatus('Kein sicherer Treffer · näher ranhalten')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Scan fehlgeschlagen')
      setStatus('Scanfehler')
    } finally {
      requestInFlightRef.current = false
    }
  }

  return (
    <main className="fast-page">
      <video ref={videoRef} className="fast-video" playsInline muted />
      <canvas ref={canvasRef} className="hidden-canvas" />

      <div className="fast-topbar">
        <a href="/" className="fast-back">←</a>
        <div>
          <strong>iPhone Schnellscan</strong>
          <span>{readyCount > 0 ? `${readyCount} lokale Fragen geladen` : 'Datenbank lädt …'}</span>
        </div>
        <button type="button" className="fast-mini" onClick={() => void captureAndAsk()} disabled={!started}>
          Scan
        </button>
      </div>

      {!started && (
        <section className="fast-start">
          <h1>Schnellscan</h1>
          <p>Einmal starten, dann die Kamera auf die Frage halten. Die Antwort erscheint automatisch groß im Vollbild.</p>
          <button type="button" onClick={() => void startCamera()}>Kamera starten</button>
          <p className="fast-note">iOS verlangt aus Datenschutzgründen diesen einmaligen Start-Tipp für die Kamera.</p>
        </section>
      )}

      {error && <div className="fast-error">{error}</div>}

      <section className={`fast-answer ${answer ? 'has-answer' : ''}`}>
        {answer ? <FastAnswer answer={answer} lastScanAt={lastScanAt} /> : <div className="fast-placeholder">Frage vor die Kamera halten</div>}
      </section>

      <div className="fast-status">{status}</div>
    </main>
  )
}

async function captureFrame(video: HTMLVideoElement, canvas: HTMLCanvasElement): Promise<Blob | null> {
  const width = video.videoWidth
  const height = video.videoHeight
  const maxWidth = 1600
  const scale = width > maxWidth ? maxWidth / width : 1
  canvas.width = Math.max(1, Math.round(width * scale))
  canvas.height = Math.max(1, Math.round(height * scale))
  const ctx = canvas.getContext('2d')
  if (!ctx) return null
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
  return new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.82))
}

function FastAnswer({ answer, lastScanAt }: { answer: AnswerResult; lastScanAt: string }) {
  const details = getCorrectDetails(answer)
  const isGood = answer.source === 'database_local' || answer.source === 'database_embedding'
  return (
    <div className={`fast-answer-box ${isGood ? 'good' : 'miss'}`}>
      <div className="fast-answer-label">{isGood ? 'ANTWORT' : 'KEIN TREFFER'}</div>
      <div className="fast-answer-main">{details.label}</div>
      {details.text && <div className="fast-answer-text">{details.text}</div>}
      {answer.confidence != null && <div className="fast-confidence">Score {answer.confidence} · {lastScanAt}</div>}
      {answer.matched_question && <div className="fast-question">{answer.matched_question.question_text}</div>}
      {answer.warning && <div className="fast-warning">{answer.warning}</div>}
    </div>
  )
}

function getCorrectDetails(answer: AnswerResult): { label: string; text: string } {
  const candidate = answer.matched_question
  if (!candidate) return { label: '—', text: answer.answer }
  const raw = candidate.correct_answer || ''
  const labels = raw.split(',').map((x) => x.trim().toUpperCase()).filter(Boolean)
  if (labels.length === 0 || raw === 'UNKNOWN') return { label: '—', text: answer.answer }
  const texts = labels.map((label) => candidate.options[label] ? `${label}: ${candidate.options[label]}` : label)
  return { label: labels.join(' + '), text: texts.join(' · ') }
}

function StatusBadge({ hasLocalQuestions }: { hasLocalQuestions: boolean }) {
  return <span className={`status ${hasLocalQuestions ? 'ready' : 'empty'}`}>{hasLocalQuestions ? 'Lokale DB aktiv' : 'Lokale DB lädt/leer'}</span>
}

function AnswerCard({ answer }: { answer: AnswerResult }) {
  return (
    <section className="card answer-card">
      <div className="answer-header">
        <div>
          <p className="eyebrow">Ergebnis</p>
          <h2>{answer.answer}</h2>
        </div>
        <span className={`source ${answer.source}`}>{answer.source}</span>
      </div>

      {answer.warning && <div className="alert warning">{answer.warning}</div>}
      {answer.confidence != null && <p className="muted">Matching-Score: {answer.confidence}</p>}
      {answer.explanation && <p>{answer.explanation}</p>}
      {answer.web_answer && <pre className="web-answer">{answer.web_answer}</pre>}

      {answer.matched_question && (
        <CandidateCard candidate={answer.matched_question} title="Bester lokaler Treffer" />
      )}

      {answer.candidates.length > 1 && (
        <details>
          <summary>Weitere lokale Kandidaten anzeigen</summary>
          <div className="candidate-grid">
            {answer.candidates.slice(1).map((candidate) => (
              <CandidateCard key={candidate.id} candidate={candidate} title={`Kandidat #${candidate.id}`} />
            ))}
          </div>
        </details>
      )}
    </section>
  )
}

function CandidateCard({ candidate, title }: { candidate: Candidate; title: string }) {
  return (
    <article className="candidate">
      <p className="eyebrow">{title} · Score {candidate.score}</p>
      <strong>{candidate.question_text}</strong>
      {Object.keys(candidate.options).length > 0 && (
        <ul>
          {Object.entries(candidate.options).map(([letter, value]) => (
            <li key={letter}><b>{letter}</b>: {value}</li>
          ))}
        </ul>
      )}
      <p>Antwort in Datenbank: <b>{candidate.correct_answer}</b></p>
      {candidate.source_page && <p className="muted">PDF-Seite: {candidate.source_page}</p>}
    </article>
  )
}

function InlineAnswerEditor({ onSave }: { onSave: (value: string) => void }) {
  const [value, setValue] = useState('')
  return (
    <div className="inline-editor">
      <select value={value} onChange={(event) => setValue(event.target.value)}>
        <option value="">Antwort ergänzen</option>
        <option value="A">A</option>
        <option value="B">B</option>
        <option value="C">C</option>
        <option value="D">D</option>
        <option value="E">E</option>
        <option value="A,C">A,C</option>
      </select>
      <button type="button" disabled={!value} onClick={() => onSave(value)}>Speichern</button>
    </div>
  )
}

export default App
