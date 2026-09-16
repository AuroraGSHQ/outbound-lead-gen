import { NextResponse } from "next/server";
import { hasValidSession } from "@/lib/auth";
import { getDb } from "@/lib/db";

/**
 * Toggle contacts.do_not_call. Safe as a direct write — no external side
 * effect, and it's the exact flag the dialer module checks fresh before
 * every call (per the module brief), so flipping it here is enough.
 */
export async function POST(request: Request, { params }: { params: Promise<{ id: string }> }) {
  if (!(await hasValidSession())) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  const { id } = await params;
  const contactId = Number(id);
  if (!Number.isInteger(contactId)) {
    return NextResponse.json({ error: "Invalid contact id" }, { status: 400 });
  }

  let body: { do_not_call?: boolean };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid request body" }, { status: 400 });
  }

  const doNotCall = body.do_not_call ? 1 : 0;
  const db = getDb();
  const result = db
    .prepare(
      "UPDATE contacts SET do_not_call = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?"
    )
    .run(doNotCall, contactId);

  if (result.changes === 0) {
    return NextResponse.json({ error: "Contact not found" }, { status: 404 });
  }

  return NextResponse.json({ ok: true, do_not_call: Boolean(doNotCall) });
}
