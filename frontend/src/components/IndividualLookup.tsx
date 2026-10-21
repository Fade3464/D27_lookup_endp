import { useRef, useState, type FormEvent } from "react"
import { ArrowRight, CheckCircle2, CircleHelp, Loader2, ShieldCheck, XCircle } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api"

type Decision = {
  id: string; ani: string; status: "allowed" | "not_allowed";
  accepted_1: number; accepted_2: number; completed_at: string
}

export function normalizeIndividualPhone(value: string): string | null {
  const text = value.trim()
  if (value.length > 40 || !/^[+0-9() .\-\t]+$/.test(text) || /^[0-9]+\.[0-9]+$/.test(text)) return null
  if (text.includes("+") && (!text.startsWith("+") || text.split("+").length !== 2)) return null
  let digits = text.replace(/[^0-9]/g, "")
  if (text.startsWith("+") && !(digits.length === 11 && digits.startsWith("1"))) return null
  if (digits.length === 11 && digits.startsWith("1")) digits = digits.slice(1)
  return /^[0-9]{10}$/.test(digits) ? digits : null
}

export default function IndividualLookup() {
  const [value, setValue] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [decision, setDecision] = useState<Decision | null>(null)
  const submitting = useRef(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (submitting.current) return
    setDecision(null); setError("")
    const ani = normalizeIndividualPhone(value)
    if (!ani) { setError("Enter a 10-digit phone number. A leading +1 is optional."); return }
    submitting.current = true; setBusy(true)
    try {
      await api("individual/session/")
      const result = await api<Decision>("individual/check/", { ani })
      // A stale or malformed response must never produce a green light.
      if (result.ani !== ani || !["allowed", "not_allowed"].includes(result.status) ||
          ![0, 1].includes(result.accepted_1) || ![0, 1].includes(result.accepted_2) ||
          (result.status === "allowed") !== (result.accepted_1 === 1 && result.accepted_2 === 1)) {
        throw new Error("Unable to verify this number. Try again shortly.")
      }
      setValue(ani); setDecision(result)
    } catch (e) { setError(e instanceof Error ? e.message : "Unable to verify this number. Try again shortly.") }
    finally { submitting.current = false; setBusy(false) }
  }

  const allowed = decision?.status === "allowed"
  return <div className="individual-layout">
    <header className="individual-header"><a href="/" className="individual-brand"><ShieldCheck aria-hidden="true" />D27 Desk</a><a href="/">User Portal<ArrowRight size={16} aria-hidden="true" /></a></header>
    <main className="individual-main">
      <div className="individual-intro"><h1>D27 Lookup</h1></div>
      <Card><CardContent className="individual-content">
        <form onSubmit={submit} noValidate aria-busy={busy}>
          <label className="field-label" htmlFor="individual-phone">Phone number</label>
          <Input id="individual-phone" name="ani" type="tel" inputMode="tel" autoComplete="off" placeholder="(234) 567-8901" maxLength={40} value={value} disabled={busy}
            aria-describedby="individual-hint" aria-invalid={!!error} required
            onChange={e => { setValue(e.target.value); setDecision(null); setError("") }} />
          <p id="individual-hint" className="individual-hint">10 digits, with an optional +1.</p>
          <Button className="individual-submit" disabled={busy || !value.trim()}>{busy && <Loader2 className="animate-spin" aria-hidden="true" />}{busy ? "Checking…" : "Check Number"}</Button>
        </form>
        <div aria-live="polite" aria-atomic="true">
          {error && <div className="individual-result individual-unverified" role="alert"><CircleHelp aria-hidden="true" /><p>{error}</p></div>}
          {decision && <div className={`individual-result ${allowed ? "individual-allowed" : "individual-denied"}`}>
            {allowed ? <CheckCircle2 aria-hidden="true" /> : <XCircle aria-hidden="true" />}
            <h2>{allowed ? "Allowed for D27" : "Not allowed for D27"}</h2>
            <strong className="individual-number">{decision.ani}</strong>
          </div>}
        </div>
      </CardContent></Card>
    </main>
  </div>
}
