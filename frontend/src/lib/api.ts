export type User = { username: string; display_name: string; is_staff: boolean; can_resume_safety?: boolean }
export type Campaign = { campaign_id: string; label: string }
export type Lookup = {
  id: string; ani: string; campaign_id: string; campaign_label: string; username: string;
  status: "pending" | "accepted" | "suppressed" | "error"; created_at: string;
  completed_at: string | null; error: string; http_code?: number | null;
  request_payload?: unknown; response_payload?: unknown; selected_result?: unknown; raw_response?: string;
}
export type History = { results: Lookup[]; count: number; page: number; pages: number }
let csrfToken = ""
export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message) }
}
export async function api<T>(path: string, body?: unknown): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api/${path}`, {
      credentials: "same-origin", cache: "no-store",
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? {} : body instanceof FormData ? { "X-CSRFToken": csrfToken } : { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
      body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
    })
  } catch { throw new ApiError("Unable to connect. Check your connection and try again.", 0) }
  let data
  try { data = await response.json() } catch { throw new ApiError("The server returned an unexpected response. Please try again.", response.status) }
  if (data.csrf_token) csrfToken = data.csrf_token
  if (!response.ok) throw new ApiError(data.error || "Request failed. Please try again.", response.status)
  return data as T
}
