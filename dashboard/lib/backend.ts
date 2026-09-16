/**
 * Calls into the existing Python FastAPI backend (../app + ../modules) for
 * any action that has a real external side effect — most importantly
 * contract approve/reject, which must go through modules/contracts'
 * approve_contract/reject_contract (PandaDoc/DocuSign send, strict status
 * state machine). Writing `contracts.status` directly from here would skip
 * that entirely and the contract would never actually get sent.
 *
 * Direct DB writes via lib/db.ts are fine for state with no external side
 * effect and no other module reacting to it (e.g. deadlines.completed).
 * When in doubt, prefer this over a raw UPDATE — ask "does the Python
 * backend already have an endpoint that does this correctly?" before
 * writing new SQL for a mutation.
 */

function backendAuthHeader(): string {
  const username = process.env.BACKEND_API_USERNAME || "";
  const password = process.env.BACKEND_API_PASSWORD || "";
  const encoded = Buffer.from(`${username}:${password}`).toString("base64");
  return `Basic ${encoded}`;
}

export async function backendFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const baseUrl = process.env.BACKEND_API_BASE_URL || "http://localhost:8000";
  return fetch(`${baseUrl}${path}`, {
    ...init,
    headers: {
      ...init.headers,
      Authorization: backendAuthHeader(),
    },
    cache: "no-store",
  });
}
