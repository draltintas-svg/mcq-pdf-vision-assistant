import { FormEvent, useEffect, useMemo, useState } from 'react'
import {
  AnswerResult,
  Candidate,
  DocumentInfo,
  Question,
  askQuestion,
  fetchDocuments,
  fetchQuestions,
  updateQuestion,
  uploadPdf,
} from './api'

function App() {
  const [pdfFile, setPdfFile] = useState<File | null>(null)
  const [imageFile, setImageFile] = useState<File | null>(null)
  const [questionText, setQuestionText] = useState('')
  const [allowWebFallback, setAllowWebFallback] = useState(true)
  const [documents, setDocuments] = useState<DocumentInfo[]>([])
  const [questions, setQuestions] = useState<Question[]>([])
  const [answer, setAnswer] = useState<AnswerResult | null>(null)
  const [loading, setLoading] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

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

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">PDF-Fragenbank · OCR · Fotoabgleich · Web-Fallback</p>
          <h1>MCQ PDF Vision Assistant</h1>
          <p className="subtitle">
            Lade eine Multiple-Choice-PDF hoch, fotografiere eine Frage oder gib Stichworte ein. Das System sucht zuerst lokal in deiner Datenbank und nutzt erst danach externe Recherche.
          </p>
        </div>
        <StatusBadge hasLocalQuestions={hasLocalQuestions} />
      </header>

      {error && <div className="alert error">{error}</div>}

      <section className="grid">
        <form className="card" onSubmit={handleUpload}>
          <h2>1. PDF importieren</h2>
          <p className="muted">
            Am besten funktionieren digitale PDFs. Gescannte PDFs werden jetzt zusätzlich per OCR verarbeitet; bei schlechter Scanqualität bitte importierte Fragen kontrollieren.
          </p>
          <label className="dropzone">
            <input
              type="file"
              accept="application/pdf"
              onChange={(event) => setPdfFile(event.target.files?.[0] ?? null)}
            />
            <span>{pdfFile ? pdfFile.name : 'PDF auswählen'}</span>
          </label>
          <button disabled={!pdfFile || loading === 'pdf'}>
            {loading === 'pdf' ? 'Import läuft …' : 'PDF hochladen und analysieren'}
          </button>
        </form>

        <form className="card accent" onSubmit={handleAsk}>
          <h2>2. Frage suchen</h2>
          <label>
            Foto der Frage
            <input
              type="file"
              accept="image/png,image/jpeg,image/webp"
              onChange={(event) => setImageFile(event.target.files?.[0] ?? null)}
            />
          </label>
          <label>
            Stichworte oder Fragetext
            <textarea
              value={questionText}
              onChange={(event) => setQuestionText(event.target.value)}
              placeholder="z. B. NSTEMI Troponin Therapie Antwort B"
              rows={5}
            />
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={allowWebFallback}
              onChange={(event) => setAllowWebFallback(event.target.checked)}
            />
            Web-Fallback erlauben, wenn lokal kein sicherer Treffer gefunden wird
          </label>
          <button disabled={(!questionText.trim() && !imageFile) || loading === 'answer'}>
            {loading === 'answer' ? 'Suche läuft …' : 'Lösung anzeigen'}
          </button>
        </form>
      </section>

      {answer && <AnswerCard answer={answer} />}

      <section className="grid lower-grid">
        <div className="card">
          <h2>Importierte PDFs</h2>
          {documents.length === 0 ? (
            <p className="muted">Noch keine PDF importiert.</p>
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

function StatusBadge({ hasLocalQuestions }: { hasLocalQuestions: boolean }) {
  return (
    <div className={`status ${hasLocalQuestions ? 'ready' : 'empty'}`}>
      <span>{hasLocalQuestions ? 'Lokale Datenbank aktiv' : 'Noch keine lokale Datenbank'}</span>
    </div>
  )
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
