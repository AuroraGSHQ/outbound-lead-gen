/**
 * Read-only credential status for /settings. Never returns actual secret
 * values, only whether an env var looks set.
 *
 * This dashboard and the Python backend (../app, ../modules) are SEPARATE
 * deployables in production (Vercel vs Railway/Render/Fly per README), so
 * this Node process generally cannot see the backend's env vars at
 * runtime. In local dev they usually share a filesystem, so as a courtesy
 * we also peek at the repo-root .env (read-only, never written to, and
 * gitignored) when it happens to be present. See the per-group `note`
 * below for the caveat this implies.
 */
import fs from "node:fs";
import path from "node:path";

export interface CredentialItem {
  label: string;
  envVar: string;
  set: boolean;
}

export interface CredentialGroup {
  name: string;
  items: CredentialItem[];
  note?: string;
}

const BACKEND_NOTE =
  "Backend-side credential (../app/config.py). This dashboard is a separate " +
  "deployment from the Python backend, so it can only see this when the " +
  "two happen to share an env source (e.g. local dev reading the repo-root " +
  ".env) — a 'missing' badge here does not prove it's actually unset on the " +
  "backend host. Confirm there directly if in doubt.";

function readRootEnvKeys(): Set<string> {
  const keys = new Set<string>();
  try {
    const rootEnvPath = path.resolve(process.cwd(), "..", ".env");
    const contents = fs.readFileSync(rootEnvPath, "utf-8");
    for (const rawLine of contents.split("\n")) {
      const line = rawLine.trim();
      if (!line || line.startsWith("#")) continue;
      const eq = line.indexOf("=");
      if (eq === -1) continue;
      const key = line.slice(0, eq).trim();
      const value = line.slice(eq + 1).trim();
      if (key && value) keys.add(key);
    }
  } catch {
    // No repo-root .env visible from this process — expected in
    // production, where the dashboard and backend are separate hosts.
  }
  return keys;
}

export function checkCredentials(): CredentialGroup[] {
  const rootKeys = readRootEnvKeys();
  const item = (label: string, envVar: string): CredentialItem => ({
    label,
    envVar,
    set: Boolean(process.env[envVar]?.trim()) || rootKeys.has(envVar),
  });

  return [
    {
      name: "Anthropic (Ask Jarvis)",
      items: [item("API key", "ANTHROPIC_API_KEY")],
    },
    {
      name: "Dashboard ↔ backend link",
      items: [
        item("Backend API base URL", "BACKEND_API_BASE_URL"),
        item("Backend API username", "BACKEND_API_USERNAME"),
        item("Backend API password", "BACKEND_API_PASSWORD"),
      ],
      note: "Used by this app to call the Python backend's own endpoints (contract approve/reject) — set on this deployment, not the backend's.",
    },
    {
      name: "Twilio",
      items: [
        item("Account SID", "TWILIO_ACCOUNT_SID"),
        item("Auth token", "TWILIO_AUTH_TOKEN"),
        item("Phone number", "TWILIO_PHONE_NUMBER"),
      ],
      note: BACKEND_NOTE,
    },
    {
      name: "Voice agent (Vapi / Retell)",
      items: [
        item("Vapi API key", "VAPI_API_KEY"),
        item("Retell API key", "RETELL_API_KEY"),
      ],
      note: BACKEND_NOTE,
    },
    {
      name: "Zoom",
      items: [
        item("Account ID", "ZOOM_ACCOUNT_ID"),
        item("Client ID", "ZOOM_CLIENT_ID"),
        item("Client secret", "ZOOM_CLIENT_SECRET"),
      ],
      note: BACKEND_NOTE,
    },
    {
      name: "Contracts (PandaDoc / DocuSign)",
      items: [
        item("PandaDoc API key", "PANDADOC_API_KEY"),
        item("DocuSign integration key", "DOCUSIGN_INTEGRATION_KEY"),
      ],
      note: BACKEND_NOTE,
    },
    {
      name: "Stripe",
      items: [
        item("API key", "STRIPE_API_KEY"),
        item("Webhook secret", "STRIPE_WEBHOOK_SECRET"),
      ],
      note: BACKEND_NOTE,
    },
    {
      name: "Gmail OAuth (sync)",
      items: [
        item("OAuth client ID", "GMAIL_OAUTH_CLIENT_ID"),
        item("OAuth client secret", "GMAIL_OAUTH_CLIENT_SECRET"),
        item("Business inbox refresh token", "GMAIL_BUSINESS_REFRESH_TOKEN"),
        item("Personal inbox refresh token", "GMAIL_PERSONAL_REFRESH_TOKEN"),
      ],
      note: BACKEND_NOTE,
    },
    {
      name: "Vibe Prospecting",
      items: [item("API key", "VIBE_PROSPECTING_API_KEY")],
      note: BACKEND_NOTE,
    },
  ];
}
