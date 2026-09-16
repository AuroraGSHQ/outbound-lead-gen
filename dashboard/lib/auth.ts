/**
 * Single username/password gate — this is a one-person system, per the
 * module README ("don't build multi-tenant auth for it"). A signed cookie
 * (HMAC-SHA256, Node crypto — no session table, no external auth service)
 * is enough: proxy.ts checks it on every request, and each API route
 * re-verifies it directly rather than trusting proxy alone, per Next's own
 * guidance (see proxy.ts's file-level comment for why).
 */
import { timingSafeEqual, createHmac } from "node:crypto";
import { cookies } from "next/headers";

export const SESSION_COOKIE_NAME = "jarvis_session";
const SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30; // 30 days

function secret(): string {
  const value = process.env.DASHBOARD_SESSION_SECRET;
  if (!value) {
    throw new Error("DASHBOARD_SESSION_SECRET is not set");
  }
  return value;
}

function timingSafeStringEqual(a: string, b: string): boolean {
  const bufA = Buffer.from(a);
  const bufB = Buffer.from(b);
  if (bufA.length !== bufB.length) return false;
  return timingSafeEqual(bufA, bufB);
}

export function verifyCredentials(username: string, password: string): boolean {
  const expectedUser = process.env.DASHBOARD_USERNAME || "";
  const expectedPass = process.env.DASHBOARD_PASSWORD || "";
  if (!expectedUser || !expectedPass) return false;
  return (
    timingSafeStringEqual(username, expectedUser) &&
    timingSafeStringEqual(password, expectedPass)
  );
}

/** `<expiry-unix-seconds>.<hmac-hex>` — no session store, just a signed expiry. */
export function createSessionToken(): string {
  const expires = Math.floor(Date.now() / 1000) + SESSION_MAX_AGE_SECONDS;
  const signature = createHmac("sha256", secret()).update(String(expires)).digest("hex");
  return `${expires}.${signature}`;
}

export function verifySessionToken(token: string | undefined | null): boolean {
  if (!token) return false;
  const [expiresRaw, signature] = token.split(".");
  if (!expiresRaw || !signature) return false;

  const expires = Number(expiresRaw);
  if (!Number.isFinite(expires) || expires < Math.floor(Date.now() / 1000)) return false;

  const expected = createHmac("sha256", secret()).update(expiresRaw).digest("hex");
  return timingSafeStringEqual(expected, signature);
}

/**
 * Re-verify the session inside a route handler / server component instead of
 * trusting proxy.ts alone — Next's own proxy.js docs warn a matcher change
 * or refactor can silently drop coverage for a route. Call this at the top
 * of every app/api/** route.ts. Returns true/false; it does not redirect
 * (route handlers should return a 401 JSON response on false).
 */
export async function hasValidSession(): Promise<boolean> {
  const store = await cookies();
  return verifySessionToken(store.get(SESSION_COOKIE_NAME)?.value);
}

export const sessionCookieOptions = {
  httpOnly: true,
  secure: process.env.NODE_ENV === "production",
  sameSite: "lax" as const,
  path: "/",
  maxAge: SESSION_MAX_AGE_SECONDS,
};
