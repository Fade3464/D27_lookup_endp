import { useEffect, useRef, useState, type DragEvent } from "react"
import { CheckCircle2, ChevronLeft, ChevronRight, Download, FileSpreadsheet, Loader2, Pause, Play, RotateCcw, ShieldAlert, Upload, X } from "lucide-react"
import { api, ApiError, type User } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

type Job = { id: string; name: string; status: string; pause_reason: string; total_rows: number; valid_rows: number; cleaned_rows: number; rejected_rows: number; processed_rows: number; resolved_rows: number; good_rows: number; bad_rows: number; mixed_rows: number; error_rows: number; progress: number; error: string; created_at: string; export_bytes: number; download_url: string | null }
type Dashboard = { results: Job[]; active: Job[]; count: number; page: number; pages: number; queue: { enabled: boolean; awaiting_decision: boolean }; safety: { paused: boolean; reason: string; notification: string | null; notification_error: string; discord_configured: boolean } }
type Detail = { batch: Job; rows: { sequence: number; source_row: number; ani: string; status: string; attempts: number; accepted_1: number | null; accepted_2: number | null; error: string }[]; next_cursor: number | null; events: { action: string; message: string; created_at: string }[] }
const statusNames: Record<string, string> = { uploaded: "Awaiting cleanup", preprocessing: "Cleaning CSV", queued: "Queued", running: "Looking up", pausing: "Finishing current lookup", paused: "Paused", review: "Needs retry", export_pending: "Export queued", exporting: "Building export", export_failed: "Export failed", completed: "Ready to download", invalid: "Invalid CSV" }
const number = (value: number) => value.toLocaleString()
const message = (e: unknown) => e instanceof Error ? e.message : "Request failed. Please try again."
const previewJob = (id: string, status: string, total = 10000, resolved = 0): Job => ({ id, status, name: `${id}.csv`, pause_reason: "", total_rows: total + 18, valid_rows: total, cleaned_rows: 163, rejected_rows: 18, processed_rows: resolved, resolved_rows: resolved, good_rows: Math.floor(resolved * .7), bad_rows: resolved - Math.floor(resolved * .7), mixed_rows: 12, error_rows: 0, progress: total ? resolved / total * 100 : 0, error: "", created_at: new Date().toISOString(), export_bytes: 123456, download_url: null })
function sampleDashboard(empty: boolean): Dashboard {
  const rows = empty ? [] : [previewJob("morning-campaign", "running", 10000, 6250), previewJob("afternoon-list", "queued", 500000), previewJob("completed-list", "completed", 5000, 5000)]
  return { results: rows, active: rows.filter(r => r.status !== "completed"), count: rows.length, page: 1, pages: 1, queue: { enabled: true, awaiting_decision: false }, safety: { paused: false, reason: "", notification: null, notification_error: "", discord_configured: false } }
}

export default function BatchDashboard({ user, preview, onUnauthorized }: { user: User; preview?: string; onUnauthorized: () => void }) {
  const [data, setData] = useState<Dashboard | null>(preview ? sampleDashboard(preview === "empty") : null)
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [busy, setBusy] = useState("")
  const [page, setPage] = useState(1)
  const [revision, setRevision] = useState(0)
  const [dragging, setDragging] = useState(false)
  const [decision, setDecision] = useState<{ next: string | null } | null>(null)
  const [confirmSafety, setConfirmSafety] = useState(false)
  const [detail, setDetail] = useState<Detail | null>(null)
  const [raw, setRaw] = useState<unknown>(null)
  const dialog = useRef<HTMLDialogElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const handleError = (e: unknown) => { if (e instanceof ApiError && e.status === 401) onUnauthorized(); setError(message(e)) }
  useEffect(() => {
    if (preview) return
    let alive = true, loading = false
    async function refresh() {
      if (loading) return
      loading = true
      try { const result = await api<Dashboard>(`batches/?page=${page}`); if (alive) setData(result) }
      catch (e) { if (alive) handleError(e) } finally { loading = false }
    }
    void refresh()
    const timer = setInterval(refresh, 3000)
    return () => { alive = false; clearInterval(timer) }
  }, [page, revision, preview])
  useEffect(() => {
    if (decision || confirmSafety) dialog.current?.showModal()
    else dialog.current?.close()
  }, [decision, confirmSafety])
  function choose(next: File | undefined) {
    setError(""); setNotice("")
    if (!next) return
    if (!next.name.toLowerCase().endsWith(".csv")) { setError("Choose a CSV file."); return }
    if (!next.size || next.size > 100 * 1024 * 1024) { setError("Choose a nonempty CSV file up to 100 MB."); return }
    setFile(next)
  }
  async function upload() {
    if (!file) return
    if (preview) { setNotice("Design preview only. No file was uploaded."); return }
    setBusy("upload"); setError("")
    const form = new FormData(); form.append("file", file)
    try { await api<Job>("batches/upload/", form); setFile(null); if (input.current) input.current.value = ""; setNotice("File received. Cleanup runs before the file enters your lookup queue."); setRevision(v => v + 1) }
    catch (e) { handleError(e) } finally { setBusy("") }
  }
  async function control(job: Job, action: string) {
    setBusy(job.id); setError("")
    try {
      if (preview && data) {
        const head = data.active.find(j => j.status === "running") || data.active.find(j => j.status === "queued")
        const needs = action === "pause" && head?.id === job.id
        const rows = data.results.map(j => j.id === job.id ? { ...j, status: action === "pause" ? "paused" : "queued" } : j)
        setData({ ...data, results: rows, active: rows.filter(j => j.status !== "completed"), queue: { enabled: !needs, awaiting_decision: needs } })
        if (needs) setDecision({ next: data.active.find(j => j.id !== job.id && j.status === "queued")?.name || null })
      } else {
        const result = await api<{ needs_next_decision?: boolean; next_name?: string }>(`batches/${job.id}/control/`, { action })
        if (result.needs_next_decision) setDecision({ next: result.next_name || null })
        setRevision(v => v + 1)
      }
    } catch (e) { handleError(e) } finally { setBusy("") }
  }
  async function continueNext(start: boolean) {
    setBusy("queue"); setError("")
    try {
      if (preview && data) {
        const head = start ? data.active.find(j => j.status === "queued")?.id : null
        const rows = data.results.map(j => j.id === head ? { ...j, status: "running" } : j)
        setData({ ...data, results: rows, active: rows.filter(j => j.status !== "completed"), queue: { enabled: start, awaiting_decision: false } })
      } else { await api("batches/queue/continue/", { start_next: start }); setRevision(v => v + 1) }
      setDecision(null)
    } catch (e) { handleError(e) } finally { setBusy("") }
  }
  async function resumeSafety() {
    setBusy("safety"); setError("")
    try { if (!preview) await api("batches/safety/resume/", {}); setConfirmSafety(false); setRevision(v => v + 1) }
    catch (e) { handleError(e) } finally { setBusy("") }
  }
  async function inspect(job: Job, after = 0) {
    setBusy(job.id); setRaw(null); setError("")
    try {
      if (preview) { setDetail({ batch: job, rows: [{ sequence: 1, source_row: 2, ani: "2025550148", status: "good", attempts: 1, accepted_1: 1, accepted_2: 1, error: "" }], next_cursor: null, events: [{ action: "uploaded", message: "Fictional sample file", created_at: job.created_at }] }); return }
      setDetail(await api<Detail>(`batches/${job.id}/?after=${after}`))
    } catch (e) { handleError(e) } finally { setBusy("") }
  }
  async function inspectNumber(sequence: number) {
    if (!detail) return
    if (preview) { setRaw({ preview: true, ani: "2025550148", accepted_1: 1, accepted_2: 1 }); return }
    try { setRaw(await api(`batches/${detail.batch.id}/numbers/${sequence}/`)) } catch (e) { handleError(e) }
  }
  const actions = (job: Job) => <div className="batch-actions">
    {["queued", "running"].includes(job.status) && <Button size="sm" variant="outline" disabled={Boolean(busy) || data?.safety.paused} onClick={() => control(job, "pause")}><Pause />Pause</Button>}
    {job.status === "paused" && <Button size="sm" variant="outline" disabled={Boolean(busy) || data?.safety.paused} onClick={() => control(job, "resume")}><Play />Resume</Button>}
    {job.status === "queued" && !data?.queue.enabled && <Button size="sm" variant="outline" disabled={Boolean(busy) || data?.safety.paused} onClick={() => control(job, "start")}><Play />Start</Button>}
    {job.status === "review" && <Button size="sm" variant="outline" disabled={Boolean(busy) || data?.safety.paused} onClick={() => control(job, "retry")}><RotateCcw />Retry Errors</Button>}
    {job.status === "export_failed" && <Button size="sm" variant="outline" disabled={Boolean(busy)} onClick={() => control(job, "retry_export")}><RotateCcw />Retry Export</Button>}
    {job.status === "completed" && (preview ? <Button size="sm" variant="outline" onClick={() => setNotice("This sample export is for design review only.")}><Download />Download</Button> : <Button size="sm" variant="outline" asChild><a href={job.download_url || "#"}><Download />Download</a></Button>)}
    <Button size="sm" variant="ghost" disabled={Boolean(busy)} onClick={() => inspect(job)}>Track<ChevronRight /></Button>
  </div>
  return <div className="batch-dashboard">
    {error && <div className="error-message" role="alert">{error}</div>}{notice && <div className="batch-notice" role="status">{notice}</div>}
    {data?.safety.paused && <section className="safety-banner" role="alert"><ShieldAlert /><div><h2>All lookup queues are safety-paused</h2><p>{data.safety.reason}</p><p>Discord: {data.safety.notification || "pending"}{!data.safety.discord_configured && " · webhook not configured"}. Unresolved numbers remain on record for retry.</p>{user.can_resume_safety ? <Button variant="outline" onClick={() => setConfirmSafety(true)}>Review & Resume All Queues</Button> : <p>A staff member must review and resume processing.</p>}</div></section>}
    <Card><CardHeader><CardTitle>Upload a CSV</CardTitle><p className="field-hint">Column one becomes a clean list of 10-digit numbers. Both campaigns are checked for every number.</p></CardHeader><CardContent>
      <div className={`csv-dropzone ${dragging ? "dragging" : ""}`} onDragOver={e => { e.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={(e: DragEvent) => { e.preventDefault(); setDragging(false); choose(e.dataTransfer.files[0]) }}>
        <FileSpreadsheet size={30} strokeWidth={1.5} /><div><strong>{file ? file.name : "Drop your CSV here"}</strong><p>{file ? `${(file.size / (1024 * 1024)).toFixed(2)} MB · ready to upload` : "or choose a file · up to 100 MB"}</p></div>
        <Button variant="outline" onClick={() => input.current?.click()} disabled={busy === "upload"}>Choose File</Button><input ref={input} className="sr-only" type="file" accept=".csv,text/csv" aria-label="Choose CSV file" onChange={e => choose(e.target.files?.[0])} disabled={busy === "upload"} />
      </div><div className="check-actions"><span>Formatting and a leading US +1 are cleaned. Invalid rows are skipped.</span><Button disabled={!file || Boolean(busy)} onClick={upload}>{busy === "upload" ? <Loader2 className="animate-spin" /> : <Upload />}{busy === "upload" ? "Uploading…" : "Upload & Queue"}</Button></div>
    </CardContent></Card>
    <div className="batch-section-heading"><h2>Your Lookup Queue</h2><span>{data?.active.length || 0} unfinished files</span></div>
    {data && !data.queue.enabled && !data.safety.paused && <div className="batch-notice">Your queue is paused. <Button size="sm" variant="outline" disabled={Boolean(busy)} onClick={() => continueNext(true)}><Play />Start Next Queued File</Button></div>}
    {!data ? <div className="table-loading" role="status"><Loader2 className="animate-spin" />Loading queue…</div> : !data.active.length ? <Card className="empty-state"><FileSpreadsheet size={28} /><h3>Your next file starts here.</h3><p>Upload a CSV to clean, check, and export its numbers.</p></Card> : <div className="batch-cards">{data.active.map((job, index) => <Card key={job.id} className="batch-job"><div className="batch-job-heading"><div><span className="batch-position">{index + 1}</span><h3>{job.name}</h3></div><span className={`batch-state state-${job.status}`}>{statusNames[job.status]}</span></div>
      <div className="batch-progress-caption"><span>{number(job.resolved_rows)} / {number(job.valid_rows)} resolved</span><strong>{job.valid_rows ? `${job.progress.toFixed(1)}%` : "Preprocessing"}</strong></div><progress aria-label={`${job.name} lookup progress`} max={job.valid_rows || 1} value={["uploaded", "preprocessing"].includes(job.status) ? undefined : job.resolved_rows} />
      <div className="batch-counts"><span><CheckCircle2 size={13} />{number(job.good_rows)} good</span><span>{number(job.bad_rows)} bad{job.mixed_rows ? ` (${number(job.mixed_rows)} mixed)` : ""}</span><span>{number(job.error_rows)} unresolved errors</span></div><p className="field-hint">{number(job.cleaned_rows)} cleaned · {number(job.rejected_rows)} skipped · {number(job.total_rows)} source rows</p>{job.error && <p className="batch-error">{job.error}</p>}{job.status === "pausing" && <p className="field-hint">The current lookup will finish and save before this file pauses.</p>}{actions(job)}</Card>)}</div>}
    <Card className="history-card"><CardHeader><CardTitle>Files & Downloads</CardTitle><p className="field-hint">Completed exports are prepared once. Each file contains only sqGOOD and sqBAD.</p></CardHeader><div className="table-scroll"><table className="records-table"><thead><tr><th>File</th><th>Status</th><th>Resolved</th><th>Actions</th></tr></thead><tbody>{data?.results.length ? data.results.map(job => <tr key={job.id}><td className="batch-filename"><strong>{job.name}</strong>{job.error && <span>{job.error}</span>}</td><td>{statusNames[job.status]}</td><td>{number(job.resolved_rows)} / {number(job.valid_rows)}</td><td>{actions(job)}</td></tr>) : <tr><td colSpan={4}>No CSV files yet.</td></tr>}</tbody></table></div><div className="history-footer"><span>{data?.count || 0} files · both campaigns must accept for sqGOOD; all other valid decisions go to sqBAD.</span><div className="pagination"><span>{data?.page || 1} / {data?.pages || 1}</span><Button aria-label="Previous files page" variant="ghost" size="icon" disabled={page <= 1 || Boolean(preview)} onClick={() => setPage(v => v - 1)}><ChevronLeft /></Button><Button aria-label="Next files page" variant="ghost" size="icon" disabled={!data || page >= data.pages || Boolean(preview)} onClick={() => setPage(v => v + 1)}><ChevronRight /></Button></div></div></Card>
    {detail && <Card className="batch-detail"><CardHeader className="history-header"><CardTitle>Tracking: {detail.batch.name}</CardTitle><Button variant="ghost" size="icon" aria-label="Close file tracking" onClick={() => { setDetail(null); setRaw(null) }}><X /></Button></CardHeader><CardContent><div className="batch-event-list">{detail.events.map((event, i) => <div key={i}><strong>{event.action.replaceAll("_", " ")}</strong><span>{new Date(event.created_at).toLocaleString()}</span><p>{event.message}</p></div>)}</div><div className="table-scroll"><table className="records-table"><thead><tr><th>CSV Row</th><th>Number</th><th>Result</th><th>Attempts</th><th>Response</th></tr></thead><tbody>{detail.rows.map(row => <tr key={row.sequence}><td>{row.source_row}</td><td>{row.ani}</td><td>{row.status}{row.error && <p className="batch-error">{row.error}</p>}</td><td>{row.attempts}</td><td><Button size="sm" variant="ghost" onClick={() => inspectNumber(row.sequence)}>View Response</Button></td></tr>)}</tbody></table></div>{detail.next_cursor && <Button variant="outline" onClick={() => inspect(detail.batch, detail.next_cursor || 0)}>Next 100 Rows</Button>}{raw !== null && <pre className="json-box">{JSON.stringify(raw, null, 2)}</pre>}</CardContent></Card>}
    <dialog ref={dialog} className="queue-dialog" aria-labelledby="queue-dialog-title" aria-describedby="queue-dialog-description" onCancel={e => { e.preventDefault(); if (!busy) { if (decision) void continueNext(false); else setConfirmSafety(false) } }}>
      <h2 id="queue-dialog-title">{confirmSafety ? "Resume all lookup queues?" : "Start the next file?"}</h2><p id="queue-dialog-description">{confirmSafety ? "Only continue after reviewing the cause of the safety stop. Manually paused files stay paused; unresolved errors require retry." : decision?.next ? `The current file is paused or finishing its last lookup. Start ${decision.next} next?` : "There are no other files queued. Your current file stays paused with its progress saved."}</p>{error && <p role="alert" className="batch-error">{error}</p>}<div className="batch-dialog-actions"><Button variant="outline" autoFocus disabled={Boolean(busy)} onClick={() => { if (confirmSafety) setConfirmSafety(false); else void continueNext(false) }}>{confirmSafety ? "Cancel" : "Keep Queue Paused"}</Button>{(confirmSafety || decision?.next) && <Button disabled={Boolean(busy)} onClick={() => confirmSafety ? resumeSafety() : continueNext(true)}>{busy && <Loader2 className="animate-spin" />}{confirmSafety ? "Resume After Review" : "Start Next File"}</Button>}</div>
    </dialog>
  </div>
}
