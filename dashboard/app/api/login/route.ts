import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import {
  createSessionToken,
  sessionCookieOptions,
  SESSION_COOKIE_NAME,
  verifyCredentials,
} from "@/lib/auth";

export async function POST(request: Request) {
  let body: { username?: string; password?: string };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid request body" }, { status: 400 });
  }

  const username = body.username ?? "";
  const password = body.password ?? "";

  if (!verifyCredentials(username, password)) {
    return NextResponse.json({ error: "Invalid username or password" }, { status: 401 });
  }

  const store = await cookies();
  store.set(SESSION_COOKIE_NAME, createSessionToken(), sessionCookieOptions);

  return NextResponse.json({ ok: true });
}
