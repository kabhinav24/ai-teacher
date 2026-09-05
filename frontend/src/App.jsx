import { useCallback, useEffect, useRef, useState } from 'react'
import { api, LANGUAGES } from './lib/api.js'
import {
  Board, DecisionLog, Feedback, MasteryRail, Question, Report, VideoPanel,
} from './components/Lesson.jsx'

const EXAMPLE = 'I am a beginner. Teach me Chapter 4 in 20 minutes. Explain it in Hindi using simple examples. Ask me questions during the lesson and test me at the end.'

export default function App() {
  const [config, setConfig] = useState(null)
  const [doc, setDoc] = useState(null)
  const [uploading, setUploading] = useState(false)

  const [request, setRequest] = useState(EXAMPLE)
  const [language, setLanguage] = useState('en')
  const [level, setLevel] = useState('beginner')
  const [minutes, setMinutes] = useState(20)

  const [session, setSession] = useState(null)
  const [segment, setSegment] = useState(null)
  const [phase, setPhase] = useState(null)
  const [question, setQuestion] = useState(null)
  const [feedback, setFeedback] = useState(null)
  const [transcript, setTranscript] = useState(null)
  const [mastery, setMastery] = useState([])
  const [log, setLog] = useState([])
  const [report, setReport] = useState(null)
  const [videoStatus, setVideoStatus] = useState(null)
  const [followUp, setFollowUp] = useState('')
  const [followUpAnswer, setFollowUpAnswer] = useState(null)

  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const pollRef = useRef(null)

  useEffect(() => { api.config().then(setConfig).catch(() => {}) }, [])

  useEffect(() => () => clearInterval(pollRef.current), [])

  const run = useCallback(async (fn) => {
    setBusy(true); setError(null)
    try { return await fn() } catch (e) { setError(e.message); return null } finally { setBusy(false) }
  }, [])

  /* ------------------------------------------------------------- actions */

  const upload = async (file) => {
    if (!file) return
    setUploading(true); setError(null)
    try {
      setDoc(await api.uploadDocument(file))
    } catch (e) {
      setError(e.message)
    } finally {
      setUploading(false)
    }
  }

  const begin = () => run(async () => {
    const data = await api.startLesson({
      request, doc_id: doc?.doc_id ?? null,
      teaching_language: language, level, minutes: Number(minutes),
    })
    setSession(data)
    setLog(data.decision_log || [])
    setReport(null); setSegment(null); setQuestion(null); setFeedback(null)
    await advance(data.session_id)
  })

  const advance = async (id = session?.session_id) => {
    if (!id) return
    const res = await api.step(id)
    setPhase(res.type)
    if (res.mastery) setMastery(res.mastery)
    if (res.decision_log) setLog((prev) => merge(prev, res.decision_log))
    if (res.segment) { setSegment(res.segment); setFeedback(null); setTranscript(null) }
    if (res.question) setQuestion(res.question)
    else if (res.type !== 'awaiting_answer') setQuestion(null)
    if (res.type === 'report') setReport(res.report)
    return res
  }

  const next = () => run(() => advance())

  const applyAnswer = (res) => {
    setFeedback(res.result)
    setTranscript(res.transcript || null)
    if (res.mastery) setMastery(res.mastery)
    if (res.decision_log) setLog((prev) => merge(prev, res.decision_log))
    setQuestion(res.retry_question || null)
  }

  const answer = (value) => run(async () => {
    applyAnswer(await api.answer(session.session_id, value))
  })

  const answerByVoice = (blob) => run(async () => {
    applyAnswer(await api.answerByVoice(session.session_id, blob))
  })

  const ask = () => run(async () => {
    if (!followUp.trim()) return
    const res = await api.ask(session.session_id, followUp)
    setFollowUpAnswer(res.answer)
    setFollowUp('')
  })

  const changeLanguage = (code) => run(async () => {
    setLanguage(code)
    if (!session) return
    const label = LANGUAGES.find((l) => l.code === code)?.label
    await api.switchLanguage(session.session_id, code, label)
    setLog((prev) => [...prev, {
      decision: 'switch teaching language',
      because: `now teaching in ${label}; the plan and your progress are kept`,
    }])
  })

  const buildVideo = () => run(async () => {
    await api.buildVideo(session.session_id)
    clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      try {
        const status = await api.videoStatus(session.session_id)
        setVideoStatus(status)
        if (status.state === 'complete' || status.state === 'failed') clearInterval(pollRef.current)
      } catch { clearInterval(pollRef.current) }
    }, 1500)
  })

  /* --------------------------------------------------------------- render */

  return (
    <div className="shell">
      <header className="masthead">
        <h1>AI Teacher</h1>
        {config && (
          <div className="provenance">
            <span>model <b>{config.llm.model}</b></span>
            <span>voice <b>{config.tts.provider}</b></span>
            <span>avatar <b>{config.avatar.provider}</b></span>
          </div>
        )}
      </header>

      {error && <div className="error"><b>That didn&apos;t work</b>{error}</div>}

      {!session ? (
        <div className="setup">
          <h2>Tell the teacher what you need</h2>
          <p className="lede">
            Upload a textbook, chapter or set of notes, or skip the upload and just name a topic.
            Say your level, how long you have, and which language you want — the teacher plans the
            lesson around that, then teaches it and questions you as it goes.
          </p>

          <div className="field">
            <label htmlFor="file">Learning material <span className="aside">optional — PDF, DOCX, PPTX, TXT, EPUB</span></label>
            <input id="file" type="file" onChange={(e) => upload(e.target.files?.[0])}
                   accept=".pdf,.docx,.doc,.pptx,.ppt,.txt,.md,.epub,.html" disabled={uploading} />
            {uploading && <p className="progress-label">Reading and indexing your file…</p>}
            {doc && (
              <div className="uploaded">
                <b>{doc.filename}</b>
                <dl>
                  <div><dt>passages indexed</dt><dd>{doc.chunks}</dd></div>
                  <div><dt>pages</dt><dd>{doc.pages ?? '—'}</dd></div>
                  <div><dt>language</dt><dd>{doc.language}</dd></div>
                  <div><dt>sections found</dt><dd>{doc.sections?.length ?? 0}</dd></div>
                </dl>
              </div>
            )}
          </div>

          <div className="field">
            <label htmlFor="request">What should the teacher cover?</label>
            <textarea id="request" value={request} onChange={(e) => setRequest(e.target.value)} />
          </div>

          <div className="row">
            <div className="field">
              <label htmlFor="lang">Teach me in</label>
              <select id="lang" value={language} onChange={(e) => setLanguage(e.target.value)}>
                {LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="level">My level</label>
              <select id="level" value={level} onChange={(e) => setLevel(e.target.value)}>
                <option value="beginner">Beginner</option>
                <option value="intermediate">Intermediate</option>
                <option value="advanced">Advanced</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="minutes">Minutes I have</label>
              <input id="minutes" type="text" inputMode="numeric" value={minutes}
                     onChange={(e) => setMinutes(e.target.value.replace(/\D/g, '') || 0)} />
            </div>
          </div>

          <button className="btn" onClick={begin} disabled={busy || !request.trim()}>
            {busy ? 'Planning your lesson…' : 'Begin the lesson'}
          </button>
        </div>
      ) : (
        <div className="stage-layout">
          <main>
            {report ? (
              <Report report={report} />
            ) : (
              <>
                <VideoPanel sessionId={session.session_id} status={videoStatus}
                            onBuild={buildVideo} busy={busy} />
                <Board segment={segment} phase={phase} />
                <Feedback result={feedback} transcript={transcript} />
                <Question
                  question={question}
                  onAnswer={answer}
                  onVoiceAnswer={answerByVoice}
                  voiceEnabled={config?.stt?.enabled}
                  busy={busy}
                />

                {!question && (
                  <div className="controls">
                    <button className="btn" onClick={next} disabled={busy}>
                      {busy ? 'Thinking…' : phase === 'assessment_question' ? 'Next question' : 'Continue'}
                    </button>
                    <select value={language} onChange={(e) => changeLanguage(e.target.value)} disabled={busy}
                            aria-label="Change teaching language">
                      {LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
                    </select>
                  </div>
                )}

                <div className="ask-row">
                  <input type="text" value={followUp} placeholder="Stop the teacher and ask something"
                         onChange={(e) => setFollowUp(e.target.value)}
                         onKeyDown={(e) => e.key === 'Enter' && ask()} disabled={busy} />
                  <button className="btn secondary" onClick={ask} disabled={busy || !followUp.trim()}>Ask</button>
                </div>

                {followUpAnswer && (
                  <div className="feedback">
                    <p>{followUpAnswer.answer}</p>
                    {followUpAnswer.is_ahead_of_lesson && (
                      <p className="diagnosis">That one is coming up later in this lesson.</p>
                    )}
                  </div>
                )}
              </>
            )}
          </main>

          <aside className="margin-rail">
            <MasteryRail mastery={mastery} currentKey={segment?.concept_key} />
            <DecisionLog entries={log} />
          </aside>
        </div>
      )}
    </div>
  )
}

function merge(prev, incoming) {
  const seen = new Set(prev.map((d) => `${d.decision}|${d.because}`))
  return [...prev, ...incoming.filter((d) => !seen.has(`${d.decision}|${d.because}`))]
}
