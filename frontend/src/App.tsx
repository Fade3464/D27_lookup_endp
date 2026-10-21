import { useEffect, useState, type FormEvent } from "react"
import { ArrowRight, ArrowUpRight, Check, CheckCircle2, ChevronLeft, ChevronRight, CircleHelp, Clock3, Database, FileClock, FlaskConical, Info, Loader2, LockKeyhole, LogOut, PanelLeft, Phone, Radio, Search, Settings2, ShieldCheck, UserRound, X, XCircle } from "lucide-react"
import BatchDashboard from "@/components/BatchDashboard"
import IndividualLookup from "@/components/IndividualLookup"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { api, ApiError, type Campaign, type History, type Lookup, type User } from "@/lib/api"
import { previewCampaigns, previewRecord, previewRecords, previewUser } from "@/lib/design-preview"

const labels = { accepted: "Good to go", suppressed: "Not allowed", error: "Check failed", pending: "Pending" }
type Tab = "batches" | "lookup" | "history" | "account"
type PreviewState = "accepted" | "suppressed" | "error" | "empty"
const tabLabels = { batches: "CSV Queue", lookup: "Suppression Check", history: "Lookup History", account: "My Account" }
const date = (value: string) => new Date(value).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
const phone = (v: string) => v.length === 10 ? `(${v.slice(0, 3)}) ${v.slice(3, 6)}-${v.slice(6)}` : v
const errorText = (e: unknown) => e instanceof Error ? e.message : "Something went wrong. Please try again."

function Brand() {
  return <div className="brand"><div className="brand-mark"><ShieldCheck aria-hidden="true" /></div><div><div className="brand-title">D27<span>Desk</span></div><div className="brand-subtitle">Campaign operations</div></div></div>
}
function Status({ status }: { status: Lookup["status"] }) {
  const Icon = status === "accepted" ? Check : status === "suppressed" ? X : status === "error" ? CircleHelp : Clock3
  return <span className={`status-badge status-${status}`}><Icon aria-hidden="true" />{labels[status]}</span>
}
function ErrorMessage({ message }: { message: string }) {
  return message ? <div role="alert" className="error-message"><CircleHelp size={17} aria-hidden="true" /><span>{message}</span></div> : null
}

export default function App() {
  if (window.location.pathname.replace(/\/+$/, "") === "/lookup-record") return <IndividualLookup />
  return window.location.pathname === "/design-preview" ? <DesignPreview /> : <AuthenticatedApp />
}

function DesignPreview() {
  const [screen, setScreen] = useState<"workspace" | "login">("workspace")
  const [state, setState] = useState<PreviewState>("accepted")
  return <div className="design-preview">
    <div className="preview-banner"><div className="preview-label"><FlaskConical size={15} aria-hidden="true" /><strong>Design preview</strong><span>Fictional sample data · No live lookups</span></div><div className="preview-controls">
      {screen === "workspace" && <><label className="sr-only" htmlFor="preview-state">Preview result state</label><select id="preview-state" value={state} onChange={e => setState(e.target.value as PreviewState)}><option value="accepted">Good to go</option><option value="suppressed">Not allowed</option><option value="error">Service error</option><option value="empty">Empty state</option></select></>}
      <button onClick={() => setScreen(screen === "workspace" ? "login" : "workspace")}>{screen === "workspace" ? "View Sign In" : "View Workspace"}<ArrowRight size={14} /></button>
    </div></div>
    {screen === "login" ? <SignIn preview onLogin={() => setScreen("workspace")} /> : <Workspace key={state} user={previewUser} onLogout={() => setScreen("login")} preview={state} />}
  </div>
}

function SignIn({ onLogin, preview = false }: { onLogin: (user: User) => void; preview?: boolean }) {
  const [username, setUsername] = useState(preview ? "alex.morgan" : "")
  const [password, setPassword] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  async function submit(e: FormEvent) {
    e.preventDefault()
    if (preview) { onLogin(previewUser); return }
    setBusy(true); setError("")
    try { const data = await api<{ user: User }>("auth/login/", { username, password }); onLogin(data.user) }
    catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  return <div className="login-layout">
    <header className="login-header"><Brand /><span className="quiet-label"><LockKeyhole size={13} aria-hidden="true" />Private workspace</span></header>
    <main className="login-main"><div className="login-card">
      <div className="login-symbol"><ShieldCheck size={34} strokeWidth={1.5} aria-hidden="true" /></div>
      <h1>Welcome to D27 Desk.</h1><p className="login-description">A clear answer before every call.<br />Sign in to your campaign workspace.</p>
      <form onSubmit={submit} className="login-form">
        <div><label className="field-label" htmlFor="username">Username</label><Input id="username" autoComplete={preview ? "off" : "username"} value={username} onChange={e => setUsername(e.target.value)} placeholder="Your username" required maxLength={150} /></div>
        <div><label className="field-label" htmlFor="password">Password</label><Input id="password" type="password" autoComplete={preview ? "off" : "current-password"} value={password} onChange={e => setPassword(e.target.value)} placeholder={preview ? "Not needed in design preview" : "Your password"} required={!preview} /></div>
        <ErrorMessage message={error} /><Button className="login-submit" disabled={busy}>{busy && <Loader2 className="animate-spin" />}{busy ? "Signing In…" : "Sign In"}{!busy && <ArrowRight />}</Button>
      </form>
      <div className="login-help">{preview ? "Preview only. No credentials are sent or saved." : "Need access? Contact your team administrator."}</div>
    </div><div className="login-reassurance"><LockKeyhole size={13} aria-hidden="true" />Your checks and records, in one secure place.</div></main>
    <footer className="login-footer"><span>D27 Campaign Desk</span><span>Check. Verify. Keep the record.</span></footer>
  </div>
}

function AuthenticatedApp() {
  const [user, setUser] = useState<User | null>(null)
  const [booting, setBooting] = useState(true)
  const [bootError, setBootError] = useState("")
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let cancelled = false
    api<{ user: User | null }>("auth/session/").then(data => { if (!cancelled) setUser(data.user) }).catch(e => { if (!cancelled) setBootError(errorText(e)) }).finally(() => { if (!cancelled) setBooting(false) })
    return () => { cancelled = true }
  }, [attempt])
  if (booting) return <div role="status" className="app-loading"><Loader2 className="animate-spin" size={20} />Loading your workspace…</div>
  if (bootError) return <main className="connection-error"><Brand /><h1>Unable to load workspace</h1><ErrorMessage message={bootError} /><Button onClick={() => { setBootError(""); setBooting(true); setAttempt(v => v + 1) }}>Try Again</Button></main>
  if (!user) return <SignIn onLogin={setUser} />
  return <Workspace key={user.username} user={user} onLogout={() => setUser(null)} />
}

function Workspace({ user, onLogout, preview }: { user: User; onLogout: () => void; preview?: PreviewState }) {
  const [tab, setTab] = useState<Tab>("batches")
  const [sidebarHidden, setSidebarHidden] = useState(false)
  const [campaigns, setCampaigns] = useState<Campaign[]>(preview ? previewCampaigns : [])
  const [campaign, setCampaign] = useState(preview ? "21625" : "")
  const [ani, setAni] = useState(preview && preview !== "empty" ? "(202) 555-0148" : "")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [record, setRecord] = useState<Lookup | null>(preview && preview !== "empty" ? previewRecord(preview) : null)
  const [history, setHistory] = useState<History | null>(null)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState("")
  const [search, setSearch] = useState("")
  const [query, setQuery] = useState("")
  const [filter, setFilter] = useState("")
  const [page, setPage] = useState(1)
  const [revision, setRevision] = useState(0)
  const [detail, setDetail] = useState<Lookup | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [loggingOut, setLoggingOut] = useState(false)
  const [logoutError, setLogoutError] = useState("")
  const handleError = (e: unknown) => { if (e instanceof ApiError && e.status === 401) onLogout(); return errorText(e) }

  useEffect(() => {
    if (preview) return
    let cancelled = false
    api<{ campaigns: Campaign[] }>("campaigns/").then(data => { if (!cancelled) { setCampaigns(data.campaigns); setCampaign(data.campaigns[0]?.campaign_id || "") } }).catch(e => { if (!cancelled) setError(handleError(e)) })
    return () => { cancelled = true }
  }, [])
  useEffect(() => {
    if (preview) {
      const rows = (preview === "empty" ? [] : previewRecords).filter(r => r.ani.includes(query) && (!filter || r.status === filter))
      setHistory({ results: rows, count: rows.length, page: 1, pages: 1 })
      return
    }
    let cancelled = false
    setHistoryLoading(true); setHistoryError("")
    const params = new URLSearchParams({ page: String(page), ani: query, status: filter })
    api<History>(`history/?${params}`).then(data => { if (!cancelled) setHistory(data) }).catch(e => { if (!cancelled) setHistoryError(handleError(e)) }).finally(() => { if (!cancelled) setHistoryLoading(false) })
    return () => { cancelled = true }
  }, [page, query, filter, revision])

  async function lookup(e: FormEvent) {
    e.preventDefault()
    if (preview) { setError("This is a design preview. No lookup was sent. Use the preview selector above to explore result states."); return }
    setBusy(true); setError(""); setRecord(null)
    try { const data = await api<Lookup>("lookups/", { campaign_id: campaign, ani }); setRecord(data); setRevision(v => v + 1) }
    catch (e) { setError(handleError(e)) } finally { setBusy(false) }
  }
  async function openDetail(id: string) {
    if (preview) { setDetail(previewRecords.find(r => r.id === id) || null); return }
    setDetail(null); setDetailLoading(true); setHistoryError("")
    try { setDetail(await api<Lookup>(`history/${id}/`)) } catch (e) { setHistoryError(handleError(e)) } finally { setDetailLoading(false) }
  }
  async function signOut() {
    if (preview) { onLogout(); return }
    setLoggingOut(true); setLogoutError("")
    try { await api("auth/logout/", {}); onLogout() } catch (e) { setLogoutError(handleError(e)) } finally { setLoggingOut(false) }
  }
  function navigate(next: Tab) {
    setTab(next); setDetail(null)
    if (next === "lookup") { setPage(1); setQuery(""); setSearch(""); setFilter("") }
  }
  const historyRows = tab === "history" ? history?.results : history?.results.slice(0, 5)

  return <div className={`app-shell ${sidebarHidden ? "sidebar-collapsed" : ""}`}>
    <a className="skip-link" href="#main-content">Skip to content</a>
    <aside className="sidebar" aria-label="Workspace navigation"><Brand /><div className="nav-caption">Workspace</div>
      <nav aria-label="Main navigation">{([{ id: "batches", icon: Database }, { id: "lookup", icon: ShieldCheck }, { id: "history", icon: FileClock }, { id: "account", icon: Settings2 }] as const).map(item => <button key={item.id} className={`nav-link ${tab === item.id ? "active" : ""}`} aria-current={tab === item.id ? "page" : undefined} onClick={() => navigate(item.id)}><item.icon aria-hidden="true" /><span>{tabLabels[item.id]}</span>{tab === item.id && <span className="nav-selection" />}</button>)}
      {!preview && <a className="nav-link" href="/lookup-record"><Phone aria-hidden="true" /><span>Individual D27 Check</span><ArrowUpRight className="nav-external" aria-hidden="true" /></a>}
      {user.is_staff && <a className="nav-link" href="/admin/"><Database aria-hidden="true" /><span>Administration</span><ArrowUpRight className="nav-external" aria-hidden="true" /></a>}</nav>
      <div className="sidebar-bottom"><div className="workspace-note"><LockKeyhole size={15} aria-hidden="true" /><div>Private workspace<span>Every check has a record.</span></div></div><div className="sidebar-version">D27 Desk <span>v1.0</span></div></div>
    </aside>
    <div className="workspace-content"><header className="page-top"><div className="toolbar-leading"><Button variant="ghost" size="icon" aria-label={sidebarHidden ? "Show sidebar" : "Hide sidebar"} aria-expanded={!sidebarHidden} onClick={() => setSidebarHidden(!sidebarHidden)} title={sidebarHidden ? "Show Sidebar" : "Hide Sidebar"}><PanelLeft /></Button><span className="toolbar-divider" /><span className="toolbar-title">{tabLabels[tab]}</span></div><div className="toolbar-user"><span>{user.display_name}</span><div className="avatar" aria-label={user.display_name}>{user.display_name.split(" ").map(s => s[0]).slice(0, 2).join("").toUpperCase()}</div><Button aria-label="Sign out" title="Sign Out" variant="ghost" size="icon" disabled={loggingOut} onClick={signOut}>{loggingOut ? <Loader2 className="animate-spin" /> : <LogOut />}</Button></div></header>
      <main id="main-content" className="page-body" tabIndex={-1}>
        <div className="page-heading"><div><div className="eyebrow">D27 Campaign Desk</div><h1>{tabLabels[tab]}</h1><p>{tab === "batches" ? "Upload, clean, and check every number. Keep every result." : tab === "lookup" ? "The right campaign. A clear answer. Ready for the next call." : tab === "history" ? "Every check, with the details that matter." : "Your workspace identity and security."}</p></div><span className="quiet-label"><LockKeyhole size={13} aria-hidden="true" />{preview ? "Sample workspace" : "Private workspace"}</span></div>
        <ErrorMessage message={logoutError} />
        {tab === "batches" ? <BatchDashboard user={user} preview={preview} onUnauthorized={onLogout} /> : tab === "account" ? <><div className="profile-summary"><div className="avatar avatar-large"><UserRound size={22} /></div><div><h2>{user.display_name}</h2><p>@{user.username} · {user.is_staff ? "Staff account" : "Team member"}</p></div></div><ErrorMessage message={error} /><PasswordForm onUnauthorized={onLogout} preview={Boolean(preview)} /></> : <>
          {tab === "lookup" && <div className="check-layout"><Card className="check-card"><CardHeader><div className="section-kicker"><div className="section-symbol"><Phone size={18} aria-hidden="true" /></div><div><CardTitle>Check a Number</CardTitle><p>Verify eligibility for your selected campaign.</p></div></div></CardHeader><CardContent><form onSubmit={lookup}>
            <fieldset className="campaign-fieldset" disabled={busy}><legend className="field-label">Campaign</legend><div className="campaign-options">{campaigns.map(c => <label key={c.campaign_id} className={`campaign-option ${campaign === c.campaign_id ? "selected" : ""}`}><input type="radio" name="campaign" value={c.campaign_id} checked={campaign === c.campaign_id} onChange={() => setCampaign(c.campaign_id)} required /><Radio size={19} aria-hidden="true" /><span className="campaign-option-text"><strong>{c.label}</strong><span>Line {c.campaign_id}</span></span><span className="radio-indicator" aria-hidden="true">{campaign === c.campaign_id && <Check size={11} />}</span></label>)}</div>{!campaigns.length && <p className="field-hint">No active campaigns are available. Contact your administrator.</p>}</fieldset>
            <div className="phone-field"><label className="field-label" htmlFor="ani">Phone Number <span>ANI</span></label><div className="phone-input-wrap"><Phone size={18} aria-hidden="true" /><Input id="ani" type="tel" inputMode="tel" autoComplete="tel-national" placeholder="(234) 567-8901" value={ani} onChange={e => setAni(e.target.value)} maxLength={24} disabled={busy} required aria-describedby="phone-hint" /></div><p id="phone-hint" className="field-hint">10 digits, with or without a leading +1. Spaces and dashes are welcome.</p></div>
            <ErrorMessage message={error} /><div className="check-actions"><span><FileClock size={14} aria-hidden="true" />{preview ? "Sample data only" : "Automatically saved to history"}</span><Button type="submit" disabled={busy || !campaign}>{busy ? <Loader2 className="animate-spin" /> : <Search />}{busy ? "Checking…" : "Check Number"}{!busy && <ArrowRight />}</Button></div>
          </form></CardContent></Card>
          <aside className="context-panel" aria-label="About your lookup"><div className="context-icon"><ShieldCheck size={24} strokeWidth={1.5} aria-hidden="true" /></div><h2>A little certainty.<br />Before every call.</h2><p>Check whether a number is allowed for a specific campaign, then carry on with confidence.</p><div className="context-facts"><div><CheckCircle2 aria-hidden="true" /><span>Campaign-specific eligibility</span></div><div><FileClock aria-hidden="true" /><span>Original response on record</span></div><div><LockKeyhole aria-hidden="true" /><span>History stays in your workspace</span></div></div><div className="context-footnote"><Info size={14} aria-hidden="true" /><span>Results reflect the service response at the time of the check.</span></div></aside></div>}
          {record && tab === "lookup" && <div className="latest-result"><Result record={record} preview={Boolean(preview)} /></div>}
          <Card className="history-card"><CardHeader className="history-header"><div><CardTitle>{tab === "history" ? "All Lookups" : "Recent Lookups"}</CardTitle><p>{history ? `${history.count} ${history.count === 1 ? "record" : "records"} in your history` : "Your recent campaign checks"}</p></div>{tab === "lookup" && <Button variant="ghost" size="sm" onClick={() => { navigate("history"); setPage(1) }}>View All<ArrowRight /></Button>}</CardHeader>
            {tab === "history" && <form onSubmit={e => { e.preventDefault(); setPage(1); setQuery(search) }} className="history-filters"><div className="search-input-wrap"><Search size={16} aria-hidden="true" /><Input aria-label="Search phone number" placeholder="Search by phone number" value={search} onChange={e => setSearch(e.target.value)} /></div><select aria-label="Filter by status" value={filter} onChange={e => { setPage(1); setFilter(e.target.value) }} className="native-select"><option value="">All Results</option>{Object.entries(labels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select><Button variant="outline" type="submit">Search</Button></form>}
            {historyError && <div className="history-error"><ErrorMessage message={historyError} /><Button variant="outline" size="sm" onClick={() => setRevision(v => v + 1)}>Try Again</Button></div>}
            <div className="table-scroll" aria-busy={historyLoading}><table className="records-table"><caption className="sr-only">{tab === "history" ? "Lookup history" : "Recent lookups"}</caption><thead><tr><th scope="col">Phone Number</th><th scope="col">Campaign</th><th scope="col">Result</th><th scope="col">Checked</th><th scope="col"><span className="sr-only">Details</span></th></tr></thead><tbody>{historyLoading ? <tr><td colSpan={5}><div role="status" className="table-loading"><Loader2 size={18} className="animate-spin" />Loading records…</div></td></tr> : historyRows?.length ? historyRows.map(row => <tr key={row.id} className={detail?.id === row.id ? "selected-row" : ""}><td className="phone-cell">{phone(row.ani)}</td><td><span className="campaign-cell">{row.campaign_label}</span><span className="line-id">{row.campaign_id}</span></td><td><Status status={row.status} /></td><td className="date-cell">{date(row.created_at)}</td><td><Button variant="ghost" size="icon" aria-label={`View details for ${phone(row.ani)}`} title="View Details" onClick={() => openDetail(row.id)} disabled={detailLoading}><ChevronRight /></Button></td></tr>) : <tr><td colSpan={5}><div className="empty-state"><div className="empty-icon"><FileClock size={26} strokeWidth={1.5} aria-hidden="true" /></div><h3>{query || filter ? "No matching records" : "Your record starts here."}</h3><p>{query || filter ? "Try a different number or choose another result filter." : "Check a number to see its campaign result and saved response here."}</p>{!(query || filter) && tab === "history" && <Button variant="outline" onClick={() => navigate("lookup")}>Check a Number<ArrowRight /></Button>}</div></td></tr>}</tbody></table></div>
            <div className="history-footer"><span><LockKeyhole size={12} aria-hidden="true" />{preview ? "Fictional records for design review" : "Original responses retained with each record"}</span>{tab === "history" && history && <div className="pagination"><span>Page {history.page} of {history.pages}</span><Button aria-label="Previous page" size="icon" variant="ghost" disabled={historyLoading || page <= 1} onClick={() => setPage(v => v - 1)}><ChevronLeft /></Button><Button aria-label="Next page" size="icon" variant="ghost" disabled={historyLoading || page >= history.pages} onClick={() => setPage(v => v + 1)}><ChevronRight /></Button></div>}</div>
          </Card>
          {detailLoading && <div role="status" className="detail-loading"><Loader2 size={17} className="animate-spin" />Loading response…</div>}
          {detail && <section className="saved-detail" aria-label="Saved lookup details"><div className="detail-heading"><h2>Lookup Details</h2><Button size="sm" variant="ghost" onClick={() => setDetail(null)}>Close<X /></Button></div><Result record={detail} preview={Boolean(preview)} /></section>}
        </>}
        <footer className="page-footer"><span>D27 Desk</span><span>Clarity before the next call.</span></footer>
      </main>
    </div>
  </div>
}

function Result({ record, preview = false }: { record: Lookup; preview?: boolean }) {
  const Icon = record.status === "accepted" ? CheckCircle2 : record.status === "suppressed" ? XCircle : record.status === "error" ? CircleHelp : Clock3
  return <Card className={`result-card result-${record.status}`} aria-live="polite">
    <div className="result-content"><div className="result-symbol"><Icon size={28} strokeWidth={1.7} aria-hidden="true" /></div><div className="result-verdict"><div className="result-eyebrow">{preview ? "Sample result" : "Lookup result"}</div><h2>{labels[record.status]}</h2><p>{record.status === "accepted" ? "This number is allowed for the selected campaign." : record.status === "suppressed" ? "This number is not allowed for the selected campaign." : record.status === "error" ? "The service could not confirm eligibility." : "This check has not completed yet."}</p></div><div className="result-number"><strong>{phone(record.ani)}</strong><span>{record.campaign_label} · Line {record.campaign_id}</span></div></div>
    {record.error && <div className="result-error"><ErrorMessage message={record.error} /></div>}
    <div className="result-meta"><span><Clock3 size={13} aria-hidden="true" />{date(record.created_at)}</span><span>Checked by {record.username}</span></div>
    <details className="response-details"><summary><span>View {preview ? "Sample" : "Saved"} Response</span><ChevronRight size={14} aria-hidden="true" /></summary><div className="response-body"><div className="response-meta"><span>HTTP {record.http_code ?? "—"}</span><span>Record {record.id}</span></div><pre className="json-box">{JSON.stringify({ request: record.request_payload, selected_result: record.selected_result, full_response: record.response_payload, raw_response: record.raw_response }, null, 2)}</pre></div></details>
  </Card>
}

function PasswordForm({ onUnauthorized, preview = false }: { onUnauthorized: () => void; preview?: boolean }) {
  const [oldPassword, setOld] = useState("")
  const [newPassword, setNew] = useState("")
  const [confirm, setConfirm] = useState("")
  const [error, setError] = useState("")
  const [success, setSuccess] = useState(false)
  const [busy, setBusy] = useState(false)
  async function submit(e: FormEvent) {
    e.preventDefault(); setError(""); setSuccess(false)
    if (preview) { setError("Design preview only. No password changes are made here."); return }
    if (newPassword !== confirm) { setError("New passwords do not match."); return }
    setBusy(true)
    try { await api("auth/password/", { old_password: oldPassword, new_password: newPassword }); setSuccess(true); setOld(""); setNew(""); setConfirm("") }
    catch (e) { if (e instanceof ApiError && e.status === 401) onUnauthorized(); setError(errorText(e)) } finally { setBusy(false) }
  }
  return <Card className="password-card"><CardHeader><div className="section-kicker"><div className="section-symbol"><LockKeyhole size={18} aria-hidden="true" /></div><div><CardTitle>Change Password</CardTitle><p>Use at least 12 characters and a password unique to this account.</p></div></div></CardHeader><CardContent><form onSubmit={submit} className="password-form"><div><label className="field-label" htmlFor="old">Current Password</label><Input id="old" type="password" autoComplete="current-password" value={oldPassword} onChange={e => setOld(e.target.value)} required={!preview} /></div><div><label className="field-label" htmlFor="new">New Password</label><Input id="new" type="password" autoComplete="new-password" minLength={12} value={newPassword} onChange={e => setNew(e.target.value)} required={!preview} /></div><div><label className="field-label" htmlFor="confirm">Confirm New Password</label><Input id="confirm" type="password" autoComplete="new-password" minLength={12} value={confirm} onChange={e => setConfirm(e.target.value)} required={!preview} /></div><ErrorMessage message={error} />{success && <div role="status" className="success-message"><Check size={17} />Password updated.</div>}<div className="password-actions"><p>Other active sessions will expire.</p><Button disabled={busy}>{busy ? <Loader2 className="animate-spin" /> : <LockKeyhole />}Update Password</Button></div></form></CardContent></Card>
}
