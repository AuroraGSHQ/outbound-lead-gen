/**
 * Next.js 16 renamed `middleware.ts` to `proxy.ts` (same mechanism, new
 * name/export) — see node_modules/next/dist/docs/01-app/03-api-reference/
 * 03-file-conventions/proxy.md. This file is NOT `middleware.ts`; that
 * convention is deprecated in this version and won't run.
 *
 * Gates every route except /login and static assets behind the signed
 * session cookie from lib/auth.ts. Next's own docs warn that a proxy
 * matcher change can silently stop protecting a route, so every API route
 * ALSO re-verifies the session itself (see lib/auth.ts's file comment) —
 * this is the fast path, not the only line of defense.
 */
import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE_NAME, verifySessionToken } from "@/lib/auth";

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (pathname === "/login" || pathname === "/api/login") {
    return NextResponse.next();
  }

  const token = request.cookies.get(SESSION_COOKIE_NAME)?.value;
  if (verifySessionToken(token)) {
    return NextResponse.next();
  }

  if (pathname.startsWith("/api/")) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  const loginUrl = new URL("/login", request.url);
  loginUrl.searchParams.set("next", pathname);
  return NextResponse.redirect(loginUrl);
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|manifest.json|sw.js|icons/).*)",
  ],
};
