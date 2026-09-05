import { useCallback, useEffect, useRef, useState } from 'react'

/** Records a short answer from the microphone.
 *  Browsers hand back WebM/Opus; the backend transcodes before transcription. */
function useRecorder() {
  const [recording, setRecording] = useState(false)
  const [error, setError] = useState(null)
  const recorderRef = useRef(null)
  const chunksRef = useRef([])
  const resolveRef = useRef(null)

  const start = useCallback(async () => {
    setError(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const rec = new MediaRecorder(stream)
      chunksRef.current = []
      rec.ondataavailable = (e) => e.data.size > 0 && chunksRef.current.push(e.data)
      rec.onstop = () => {
        // Release the mic straight away — a live indicator light that never
        // goes out is alarming, especially for a student on a shared machine.
        stream.getTracks().forEach((t) => t.stop())
        resolveRef.current?.(new Blob(chunksRef.current, { type: 'audio/webm' }))
      }
      recorderRef.current = rec
      rec.start()
      setRecording(true)
    } catch {
      setError('Microphone unavailable. You can type your answer instead.')
    }
  }, [])

  const stop = useCallback(
    () =>
      new Promise((resolve) => {
        resolveRef.current = resolve
        recorderRef.current?.stop()
        setRecording(false)
      }),
    [],
  )

  const supported =
    typeof navigator !== 'undefined' && !!navigator.mediaDevices && typeof MediaRecorder !== 'undefined'

  return { recording, error, start, stop, supported }
}

/* ------------------------------------------------------------------ board */

export function Board({ segment, phase }) {
  if (!segment) {
    return <div className="board"><p className="empty">The lesson starts when you press Begin.</p></div>
  }
  const g = segment.grounding
  const isRemediation = phase === 'remediation'

  return (
    <>
      <div className="board">
        {isRemediation && <p className="eyebrow">Let&apos;s try that a different way</p>}
        <h3>{segment.board_title || 'Lesson'}</h3>
        {segment.board_points?.length > 0 && (
          <ul>{segment.board_points.map((p, i) => <li key={i}>{p}</li>)}</ul>
        )}
        {segment.callout && <p className="callout">{segment.callout}</p>}
      </div>

      <p className="narration">{segment.narration}</p>

      {segment.key_terms?.length > 0 && (
        <div className="terms">
          {segment.key_terms.map((t, i) => (
            <span className="term" key={i}><b>{t.term}</b> — {t.gloss}</span>
          ))}
        </div>
      )}

      {g && (
        <p className={`grounding${g.passed ? '' : ' weak'}`}>
          <span className="dot" aria-hidden="true" />
          {g.passed
            ? `${Math.round(g.score * 100)}% of this is traced to your material`
            : `Only ${Math.round(g.score * 100)}% traced to your material — treat the rest as background`}
          {g.citations?.length > 0 && <span className="cites">· {g.citations.join(', ')}</span>}
        </p>
      )}
    </>
  )
}

/* --------------------------------------------------------------- question */

export function Question({ question, onAnswer, onVoiceAnswer, voiceEnabled, busy }) {
  const [choice, setChoice] = useState(null)
  const [text, setText] = useState('')
  const [showHint, setShowHint] = useState(false)
  const recorder = useRecorder()

  useEffect(() => { setChoice(null); setText(''); setShowHint(false) }, [question?.prompt])

  if (!question) return null
  const isMcq = question.type === 'mcq' && question.options?.length > 0
  const value = isMcq ? choice : text.trim()

  const submit = () => { if (value) onAnswer(value) }

  return (
    <div className="question">
      <p className="asking">The teacher is asking you</p>
      <p className="prompt">{question.prompt}</p>

      {isMcq ? (
        <div className="options">
          {question.options.map((o) => (
            <button
              key={o.id}
              className="option"
              aria-pressed={choice === o.id}
              onClick={() => setChoice(o.id)}
              disabled={busy}
            >
              <span className="key">{o.id})</span>
              <span>{o.text}</span>
            </button>
          ))}
        </div>
      ) : (
        <div className="field">
          <label htmlFor="answer">Your answer</label>
          <textarea
            id="answer"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Answer in whichever language is easiest — you're graded on the idea, not the wording."
            disabled={busy}
          />
        </div>
      )}

      {showHint && question.hint && <p className="hint">{question.hint}</p>}

      <div className="controls">
        <button className="btn" onClick={submit} disabled={busy || !value}>
          {busy ? 'Checking…' : 'Send answer'}
        </button>

        {voiceEnabled && recorder.supported && !isMcq && (
          <button
            className={`btn secondary${recorder.recording ? ' recording' : ''}`}
            disabled={busy}
            onClick={async () => {
              if (recorder.recording) onVoiceAnswer(await recorder.stop())
              else recorder.start()
            }}
          >
            {recorder.recording ? '■ Stop and send' : '● Answer out loud'}
          </button>
        )}

        {question.hint && !showHint && (
          <button className="btn secondary small" onClick={() => setShowHint(true)}>Give me a hint</button>
        )}
      </div>
      {recorder.error && <p className="hint">{recorder.error}</p>}
    </div>
  )
}

/* --------------------------------------------------------------- feedback */

const VERDICT_LABEL = {
  correct: 'Correct',
  partially_correct: 'Partly there',
  incorrect: 'Not quite',
}

export function Feedback({ result, transcript }) {
  if (!result) return null
  return (
    <div className={`feedback ${result.verdict}`}>
      {transcript && (
        <p className="diagnosis">Heard: &ldquo;{transcript.text}&rdquo;</p>
      )}
      <div className="verdict">{VERDICT_LABEL[result.verdict] || result.verdict}</div>
      <p>{result.feedback}</p>
      {result.correct_answer && <p className="diagnosis">Answer: {result.correct_answer}</p>}
      {result.misconception && (
        <p className="diagnosis">
          Diagnosed as <b>{result.misconception.replace(/_/g, ' ')}</b> — the teacher will pick a
          different approach rather than repeat itself.
        </p>
      )}
    </div>
  )
}

/* ---------------------------------------------------------- mastery rail */

export function MasteryRail({ mastery, currentKey }) {
  if (!mastery?.length) return null
  return (
    <section>
      <h4>What you&apos;ve got so far</h4>
      {mastery.map((m) => {
        const state = m.mastered ? 'mastered' : m.struggling ? 'struggling' : ''
        return (
          <div key={m.key} className={`concept-meter ${state} ${m.key === currentKey ? 'current' : ''}`}>
            <span className="name">{m.name}</span>
            <span className="pct">{Math.round(m.mastery * 100)}%</span>
            <span className="meter"><span style={{ width: `${Math.round(m.mastery * 100)}%` }} /></span>
          </div>
        )
      })}
    </section>
  )
}

/* -------------------------------------------------------------- reasoning */

export function DecisionLog({ entries }) {
  if (!entries?.length) return null
  return (
    <section>
      <h4>Why the teacher did that</h4>
      <ul className="log">
        {entries.slice(-6).reverse().map((d, i) => (
          <li key={i}>
            <span className="decision">{d.decision}</span>
            <span className="because">{d.because}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

/* ------------------------------------------------------------------ video */

export function VideoPanel({ sessionId, status, onBuild, busy }) {
  const state = status?.state
  const ready = state === 'complete'

  return (
    <div className="video-panel">
      {ready ? (
        <>
          <video controls src={status.video_url} poster="">
            <track kind="captions" src={status.subtitles_url} srcLang={status.language} label="Captions" default />
          </video>
          <p className="progress-label">
            {status.segments} segments · {Math.round(status.duration_s)}s · voice {status.tts_provider} · avatar {status.avatar_provider}
          </p>
        </>
      ) : (
        <>
          <button className="btn" onClick={onBuild} disabled={busy || state === 'running' || state === 'queued'}>
            {state === 'running' || state === 'queued' ? 'Recording the lesson…' : 'Record this as a video'}
          </button>
          {(state === 'running' || state === 'queued') && (
            <>
              <div className="progress"><span style={{ width: `${Math.round((status.progress || 0) * 100)}%` }} /></div>
              <p className="progress-label">{status.message}</p>
            </>
          )}
          {state === 'failed' && (
            <div className="error"><b>The recording stopped</b>{status.message}</div>
          )}
        </>
      )}
    </div>
  )
}

/* ----------------------------------------------------------------- report */

export function Report({ report }) {
  if (!report) return null
  return (
    <div className="report">
      <div className="score">{report.score}%</div>
      <p className="headline">{report.headline}</p>

      {report.strong_areas?.length > 0 && (
        <section>
          <h4>Solid</h4>
          <ul>
            {report.strong_areas.map((s, i) => (
              <li key={i}><b>{s.concept}</b> — {s.evidence}</li>
            ))}
          </ul>
        </section>
      )}

      {report.weak_areas?.length > 0 && (
        <section>
          <h4>Needs another pass</h4>
          <ul>
            {report.weak_areas.map((w, i) => (
              <li key={i}>
                <b>{w.concept}</b> — {w.evidence}
                <br /><span className="fix">Do this: {w.fix}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {report.misconceptions_to_clear?.length > 0 && (
        <section>
          <h4>Ideas to straighten out</h4>
          <ul>{report.misconceptions_to_clear.map((m, i) => <li key={i}>{m}</li>)}</ul>
        </section>
      )}

      {report.revision_plan?.length > 0 && (
        <section>
          <h4>Revision plan</h4>
          <ul>
            {report.revision_plan.map((r, i) => (
              <li key={i}><b>{r.task}</b> ({r.minutes} min) — {r.why}</li>
            ))}
          </ul>
        </section>
      )}

      {report.next_topic && (
        <section>
          <h4>Learn next</h4>
          <p><b>{report.next_topic.name}</b> — {report.next_topic.reason}</p>
        </section>
      )}

      {report.study_tip && (
        <section>
          <h4>One thing to change</h4>
          <p>{report.study_tip}</p>
        </section>
      )}
    </div>
  )
}
