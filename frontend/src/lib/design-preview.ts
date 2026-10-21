import type { Campaign, Lookup, User } from "./api"

// Fictional examples, kept in memory. Nothing here reaches the API or database.
export const previewUser: User = { username: "alex.morgan", display_name: "Alex Morgan", is_staff: false }
export const previewCampaigns: Campaign[] = [
  { campaign_id: "21625", label: "D27 (1)" },
  { campaign_id: "21627", label: "D27 (2)" },
]
export function previewRecord(status: Lookup["status"], index = 0): Lookup {
  const campaign = previewCampaigns[index % 2]
  const ani = `20255501${String(48 + index).padStart(2, "0")}`
  const timestamp = new Date(Date.now() - index * 780000).toISOString()
  const result = { ok: status !== "error", http_code: status === "error" ? 503 : 200, api_response: status === "error" ? null : { accepted: status === "accepted" ? 1 : 0, status: "success" } }
  return {
    id: `design-preview-${index}`, ani, campaign_id: campaign.campaign_id, campaign_label: campaign.label,
    username: previewUser.username, status, created_at: timestamp, completed_at: status === "pending" ? null : timestamp,
    http_code: status === "error" ? 503 : 200,
    error: status === "error" ? "The lookup service could not be reached. Try checking the number again." : "",
    request_payload: { ani }, selected_result: result,
    response_payload: { ani, results: [{ ...campaign, result }] }, raw_response: JSON.stringify({ ani, result }),
  }
}
export const previewRecords = (["accepted", "accepted", "suppressed", "accepted", "error"] as const).map((status, index) => previewRecord(status, index))
