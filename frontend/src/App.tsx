import { useEffect, useState, type FormEvent } from "react"
import { ArrowDownLeft, ArrowRight, Check, CheckCircle2, ChevronLeft, ChevronRight, CircleHelp, Clock3, Database, FileClock, Fingerprint, LayoutDashboard, Loader2, LockKeyhole, LogOut, Search, Settings2, ShieldCheck, XCircle } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { api, ApiError, type Campaign, type History, type Lookup, type User } from "@/lib/api"

const labels = { accepted: "Good to go", suppressed: "Not allowed", error: "Failed", pending: "Pending" }
const date = (value: string) => new Date(value).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
const errorText = (e: unknown) => e instanceof Error ? e.message : "Something went wrong. Please try again."
function Brand() { return <div className="brand"><div className="brand-mark"><ShieldCheck size={23} /></div><div><div className="font-semibold tracking-tight text-white text-lg">D27<span className="font-normal text-[#b6ccbd]"> / desk</span></div><div className="text-[10px] tracking-[.13em] text-[#abc2b3] uppercase mt-0.5">Campaign operations</div></div></div> }
function Status({ status }: { status: Lookup["status"] }) { return <span className={`status-badge status-${status}`}><span className="size-1.5 rounded-full bg-current" />{labels[status]}</span> }
function ErrorMessage({ message }: { message: string }) { return message ? <div role="alert" className="error-message">{message}</div> : null }

function SignIn({ onLogin }: { onLogin: (user: User) => void }) {
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError("")
    try { const data = await api<{ user: User }>("auth/login/", { username, password }); onLogin(data.user) }
    catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  return <div className="login-layout"><div className="login-brand"><Brand /><div className="max-w-md"><div className="text-xs uppercase tracking-[.18em] text-[#c8dfad] mb-5">A clear answer. A complete record.</div><h1 className="text-4xl lg:text-5xl font-medium leading-tight tracking-tight">Confidence before<br />every call.</h1><p className="mt-6 text-[#b9d0c2] leading-relaxed text-sm max-w-sm">Check campaign suppression, review the response, and keep your team's lookup history in one place.</p></div><div className="text-xs text-[#94b3a1] mt-10">D27 · Campaign suppression checker</div></div><main className="flex items-center justify-center p-8 lg:p-16"><div className="w-full max-w-sm"><div className="size-11 rounded-xl border border-border bg-white flex items-center justify-center mb-7"><LockKeyhole size={20} /></div><h2 className="text-3xl font-semibold tracking-tight">Welcome back</h2><p className="text-sm text-muted-foreground mt-3 mb-8">Sign in with your team account to continue.</p><form onSubmit={submit} className="space-y-5"><div><label className="field-label" htmlFor="username">Username</label><Input id="username" autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} required maxLength={150} autoFocus /></div><div><label className="field-label" htmlFor="password">Password</label><Input id="password" type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required /></div><ErrorMessage message={error} /><Button className="w-full h-11" disabled={busy}>{busy ? <Loader2 className="animate-spin" /> : null}Sign in<ArrowRight /></Button></form><p className="text-xs text-muted-foreground leading-relaxed mt-7">Need access or a password reset? Contact your team administrator.</p></div></main></div>
}

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [booting, setBooting] = useState(true)
  const [bootError, setBootError] = useState("")
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let cancelled = false
    api<{ user: User | null }>("auth/session/").then(data => { if (!cancelled) setUser(data.user) }).catch(e => { if (!cancelled) setBootError(errorText(e)) }).finally(() => { if (!cancelled) setBooting(false) })
    return () => { cancelled = true }
  }, [attempt])
  if (booting) return <div role="status" className="min-h-screen flex items-center justify-center gap-3 text-sm"><Loader2 className="size-5 animate-spin" />Loading your workspace…</div>
  if (bootError) return <main className="max-w-lg mx-auto p-8 mt-20 space-y-4"><h1 className="text-2xl font-semibold">Unable to load workspace</h1><ErrorMessage message={bootError} /><Button onClick={() => { setBootError(""); setBooting(true); setAttempt(v => v + 1) }}>Try again</Button></main>
  if (!user) return <SignIn onLogin={setUser} />
  return <Workspace key={user.username} user={user} onLogout={() => setUser(null)} />
}

function Workspace({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [tab, setTab] = useState<"lookup" | "history" | "account">("lookup")
  const [campaigns, setCampaigns] = useState<Campaign[]>([])
  const [campaign, setCampaign] = useState("")
  const [ani, setAni] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [record, setRecord] = useState<Lookup | null>(null)
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
    let cancelled = false
    api<{ campaigns: Campaign[] }>("campaigns/").then(data => { if (!cancelled) { setCampaigns(data.campaigns); setCampaign(data.campaigns[0]?.campaign_id || "") } }).catch(e => { if (!cancelled) setError(handleError(e)) })
    return () => { cancelled = true }
  }, [])
  useEffect(() => {
    let cancelled = false
    setHistoryLoading(true); setHistoryError("")
    const params = new URLSearchParams({ page: String(page), ani: query, status: filter })
    api<History>(`history/?${params}`).then(data => { if (!cancelled) setHistory(data) }).catch(e => { if (!cancelled) setHistoryError(handleError(e)) }).finally(() => { if (!cancelled) setHistoryLoading(false) })
    return () => { cancelled = true }
  }, [page, query, filter, revision])

  async function lookup(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError(""); setRecord(null)
    try { const data = await api<Lookup>("lookups/", { campaign_id: campaign, ani }); setRecord(data); setRevision(v => v + 1) }
    catch (e) { setError(handleError(e)) } finally { setBusy(false) }
  }
  async function openDetail(id: string) {
    setDetail(null); setDetailLoading(true); setHistoryError("")
    try { setDetail(await api<Lookup>(`history/${id}/`)) } catch (e) { setHistoryError(handleError(e)) } finally { setDetailLoading(false) }
  }
  async function signOut() {
    setLoggingOut(true); setLogoutError("")
    try { await api("auth/logout/", {}); onLogout() } catch (e) { setLogoutError(handleError(e)) } finally { setLoggingOut(false) }
  }
  const historyRows = tab === "history" ? history?.results : history?.results.slice(0, 5)

  return <div className="app-shell">
    <aside className="sidebar"><Brand /><div className="nav-caption text-[10px] uppercase tracking-[.15em] text-[#8eae9b] px-3 mb-3">Workspace</div><nav aria-label="Main navigation"><button className={`nav-link ${tab === "lookup" ? "active" : ""}`} onClick={() => { setTab("lookup"); setDetail(null); setPage(1); setQuery(""); setSearch(""); setFilter("") }}><LayoutDashboard size={18} />Suppression check</button><button className={`nav-link ${tab === "history" ? "active" : ""}`} onClick={() => { setTab("history"); setDetail(null) }}><FileClock size={18} />Lookup history</button><button className={`nav-link ${tab === "account" ? "active" : ""}`} onClick={() => setTab("account")}><Settings2 size={18} />My account</button>{user.is_staff && <a className="nav-link" href="/admin/"><Database size={18} />Administration<ArrowDownLeft size={14} /></a>}</nav><div className="sidebar-bottom"><div className="px-3 text-xs leading-relaxed text-[#a7c1b1] flex gap-2"><ShieldCheck size={17} className="shrink-0" /><span>Every lookup.<br />Accounted for.</span></div><div className="mt-6 px-3 text-[10px] text-[#8eae9b]">D27 OPERATIONS · v1.0</div></div></aside>
    <div className="min-w-0"><header className="page-top"><div className="text-xs text-muted-foreground">Workspace <span className="mx-2 text-[#b9c3bb]">/</span><span className="text-foreground font-medium">{tab === "lookup" ? "Suppression check" : tab === "history" ? "Lookup history" : "My account"}</span></div><div className="flex items-center gap-3"><span className="hidden sm:inline text-xs text-muted-foreground">{user.display_name}</span><div className="size-8 rounded-full bg-[#e8eee2] flex items-center justify-center text-xs font-semibold" aria-label={user.display_name}>{user.display_name.slice(0, 2).toUpperCase()}</div><Button aria-label="Sign out" variant="ghost" size="icon" disabled={loggingOut} onClick={signOut}>{loggingOut ? <Loader2 className="animate-spin" /> : <LogOut />}</Button></div></header>
    <main className="page-body"><div className="flex justify-between items-end gap-4 mb-8"><div><p className="eyebrow mb-3">Campaign operations</p><h1 className="text-3xl md:text-[34px] font-semibold tracking-[-.035em]">{tab === "lookup" ? "Check before you connect." : tab === "history" ? "Your lookup record." : "Account settings."}</h1><p className="mt-3 text-sm text-muted-foreground">{tab === "lookup" ? "Look up a number, verify campaign eligibility, and keep a record." : tab === "history" ? "Search previous checks and inspect the original responses." : "Keep your account secure with a strong, unique password."}</p></div><div className="hidden lg:flex items-center gap-2 text-xs text-muted-foreground pb-1"><LockKeyhole size={13} />Private workspace</div></div>
    {logoutError && <div className="mb-5"><ErrorMessage message={logoutError} /></div>}
    {tab === "account" ? <><ErrorMessage message={error} /><PasswordForm onUnauthorized={onLogout} /></> : <>
    {tab === "lookup" && <div className="grid lg:grid-cols-[1.7fr_1fr] gap-5 mb-8"><Card><CardHeader><div className="flex items-center gap-3 mb-2"><div className="size-8 rounded-lg bg-[#eef3e9] flex items-center justify-center"><Search size={17} /></div><CardTitle>New suppression check</CardTitle></div><p className="text-xs text-muted-foreground">Choose a campaign and enter the number you want to check.</p></CardHeader><CardContent><form onSubmit={lookup}><div className="grid sm:grid-cols-[1fr_1.2fr] gap-5 mb-6"><div><label className="field-label" htmlFor="campaign">Campaign</label><select id="campaign" value={campaign} onChange={e => setCampaign(e.target.value)} className="native-select" disabled={busy} required>{!campaigns.length && <option value="">No campaigns available</option>}{campaigns.map(c => <option key={c.campaign_id} value={c.campaign_id}>{c.label} · {c.campaign_id}</option>)}</select></div><div><label className="field-label" htmlFor="ani">Phone number (ANI)</label><Input id="ani" inputMode="tel" autoComplete="tel-national" placeholder="e.g. 234 567 8901" value={ani} onChange={e => setAni(e.target.value)} maxLength={24} disabled={busy} required /></div></div><ErrorMessage message={error} /><div className="flex flex-wrap items-center justify-between gap-3 mt-5"><p className="text-[11px] text-muted-foreground flex gap-1.5 items-center"><Fingerprint size={14} />Saved to your lookup history</p><Button type="submit" disabled={busy || !campaign}>{busy ? <Loader2 className="animate-spin" /> : <Search />}{busy ? "Checking…" : "Check number"}{!busy && <ArrowRight />}</Button></div></form></CardContent></Card>
    <Card className="bg-[#eef2e7] border-[#e1e7d6] shadow-none"><CardHeader><div className="flex items-center justify-between"><div className="eyebrow text-[#718158]">The checking process</div><CircleHelp size={17} className="text-[#7e8e66]" /></div></CardHeader><CardContent><div className="space-y-5">{[["01", "Select your campaign", "Eligibility is specific to each campaign."], ["02", "Run the lookup", "The service checks the number's suppression status."], ["03", "Review & keep the record", "The response and timestamp are saved automatically."]].map(([n, title, text]) => <div className="flex gap-3" key={n}><span className="text-[10px] font-mono mt-1 text-[#85926e]">{n}</span><div><h3 className="text-xs font-semibold">{title}</h3><p className="text-[11px] text-[#718065] leading-relaxed mt-1">{text}</p></div></div>)}</div></CardContent></Card></div>}
    {record && tab === "lookup" && <div className="mb-8"><Result record={record} /></div>}
    <Card><CardHeader className="flex-row justify-between items-center gap-3"><div className="flex items-center gap-3"><Clock3 size={18} className="text-muted-foreground" /><CardTitle>{tab === "history" ? "Lookup history" : "Recent lookups"}</CardTitle>{history && <span className="rounded-md bg-muted px-2 py-1 text-[10px] text-muted-foreground">{history.count} total</span>}</div>{tab === "lookup" && <Button variant="ghost" size="sm" onClick={() => { setTab("history"); setPage(1) }}>View history<ArrowRight /></Button>}</CardHeader>
    {tab === "history" && <form onSubmit={e => { e.preventDefault(); setPage(1); setQuery(search) }} className="flex flex-wrap gap-3 px-6 pb-5"><Input aria-label="Search phone number" placeholder="Search phone number…" value={search} onChange={e => setSearch(e.target.value)} className="max-w-xs" /><select aria-label="Filter by status" value={filter} onChange={e => { setPage(1); setFilter(e.target.value) }} className="native-select max-w-44"><option value="">All statuses</option>{Object.entries(labels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select><Button variant="outline" type="submit"><Search />Search</Button></form>}
    {historyError && <div className="px-6 pb-4"><ErrorMessage message={historyError} /><Button className="mt-3" variant="outline" size="sm" onClick={() => setRevision(v => v + 1)}>Retry</Button></div>}
    <div className="overflow-x-auto" aria-busy={historyLoading}><table className="records-table"><thead><tr><th>Phone number</th><th>Campaign</th><th>Result</th><th>Checked</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>{historyLoading ? <tr><td colSpan={5}><div role="status" className="flex gap-2 items-center justify-center py-6 text-muted-foreground"><Loader2 size={16} className="animate-spin" />Loading records…</div></td></tr> : historyRows?.length ? historyRows.map(row => <tr key={row.id}><td className="font-medium font-mono tracking-tight">{row.ani}</td><td>{row.campaign_label}<span className="text-[10px] text-muted-foreground ml-2">{row.campaign_id}</span></td><td><Status status={row.status} /></td><td className="text-muted-foreground whitespace-nowrap">{date(row.created_at)}</td><td><Button variant="ghost" size="sm" onClick={() => openDetail(row.id)} disabled={detailLoading}>Details<ArrowRight /></Button></td></tr>) : <tr><td colSpan={5}><div className="text-center py-10"><FileClock className="mx-auto mb-3 text-[#a4b3a7]" size={28} /><p className="font-medium">{query || filter ? "No matching records" : "Your first check starts the record"}</p><p className="text-xs text-muted-foreground mt-2">{query || filter ? "Try a different number or status." : "Completed and failed lookups will appear here."}</p></div></td></tr>}</tbody></table></div>
    <div className="px-6 py-4 flex justify-between items-center gap-3"><span className="text-[11px] text-muted-foreground flex items-center gap-1.5"><Database size={12} />{tab === "history" ? "Original responses retained with each record" : "Showing your most recent checks"}</span>{tab === "history" && history && <div className="flex items-center gap-2"><span className="text-xs text-muted-foreground">{history.page} / {history.pages}</span><Button aria-label="Previous page" size="icon" variant="outline" disabled={historyLoading || page <= 1} onClick={() => setPage(v => v - 1)}><ChevronLeft /></Button><Button aria-label="Next page" size="icon" variant="outline" disabled={historyLoading || page >= history.pages} onClick={() => setPage(v => v + 1)}><ChevronRight /></Button></div>}</div></Card>
    {detailLoading && <div role="status" className="mt-6 flex gap-2 text-sm"><Loader2 size={17} className="animate-spin" />Loading response…</div>}
    {detail && <div className="mt-6"><div className="flex justify-between items-center mb-3"><h2 className="text-sm font-semibold">Saved lookup details</h2><Button size="sm" variant="ghost" onClick={() => setDetail(null)}>Close</Button></div><Result record={detail} /></div>}
    </>}
    <footer className="mt-7 flex flex-wrap justify-between gap-2 text-[10px] text-muted-foreground"><span>D27 Campaign Desk</span><span>Lookup results reflect the service response at the time of the check.</span></footer></main></div></div>
}

function Result({ record }: { record: Lookup }) {
  return <Card aria-live="polite"><CardHeader className="flex-row items-start justify-between gap-3"><div className="flex items-start gap-3">{record.status === "accepted" ? <CheckCircle2 className="text-[#3b7750]" /> : record.status === "suppressed" ? <XCircle className="text-[#ab5141]" /> : <CircleHelp className="text-[#a57b2f]" />}<div><CardTitle>{labels[record.status]} <span className="font-normal text-muted-foreground">· {record.ani}</span></CardTitle><p className="text-xs text-muted-foreground mt-2">{record.campaign_label} · {record.campaign_id} · {date(record.created_at)}</p></div></div><Status status={record.status} /></CardHeader><CardContent><ErrorMessage message={record.error} /><div className="mt-2 text-[11px] text-muted-foreground">Checked by {record.username} · HTTP {record.http_code ?? "—"} · Record {record.id}</div><details className="mt-5"><summary className="text-xs font-medium cursor-pointer">View saved API response</summary><pre className="json-box mt-3">{JSON.stringify({ request: record.request_payload, selected_result: record.selected_result, full_response: record.response_payload, raw_response: record.raw_response }, null, 2)}</pre></details></CardContent></Card>
}

function PasswordForm({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [oldPassword, setOld] = useState("")
  const [newPassword, setNew] = useState("")
  const [confirm, setConfirm] = useState("")
  const [error, setError] = useState("")
  const [success, setSuccess] = useState(false)
  const [busy, setBusy] = useState(false)
  async function submit(e: FormEvent) {
    e.preventDefault(); setError(""); setSuccess(false)
    if (newPassword !== confirm) { setError("New passwords do not match."); return }
    setBusy(true)
    try { await api("auth/password/", { old_password: oldPassword, new_password: newPassword }); setSuccess(true); setOld(""); setNew(""); setConfirm("") }
    catch (e) { if (e instanceof ApiError && e.status === 401) onUnauthorized(); setError(errorText(e)) } finally { setBusy(false) }
  }
  return <Card className="max-w-xl"><CardHeader><CardTitle>Change password</CardTitle><p className="text-xs text-muted-foreground mt-2">Use at least 12 characters. Other active sessions will expire.</p></CardHeader><CardContent><form onSubmit={submit} className="space-y-5"><div><label className="field-label" htmlFor="old">Current password</label><Input id="old" type="password" autoComplete="current-password" value={oldPassword} onChange={e => setOld(e.target.value)} required /></div><div><label className="field-label" htmlFor="new">New password</label><Input id="new" type="password" autoComplete="new-password" minLength={12} value={newPassword} onChange={e => setNew(e.target.value)} required /></div><div><label className="field-label" htmlFor="confirm">Confirm new password</label><Input id="confirm" type="password" autoComplete="new-password" minLength={12} value={confirm} onChange={e => setConfirm(e.target.value)} required /></div><ErrorMessage message={error} />{success && <div role="status" className="text-sm text-primary flex gap-2"><Check size={17} />Password updated.</div>}<Button disabled={busy}>{busy ? <Loader2 className="animate-spin" /> : <LockKeyhole />}Update password</Button></form></CardContent></Card>
}
