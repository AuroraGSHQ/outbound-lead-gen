import { NextResponse } from "next/server";
import { hasValidSession } from "@/lib/auth";
import { getDb } from "@/lib/db";

/**
 * The one direct-write mutation this app owns, per lib/backend.ts's file
 * comment: deadlines.completed has no external side effect and nothing
 * else reacts to it.
 */
export async function POST(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  if (!(await hasValidSession())) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  const { id } = await params;
  const deadlineId = Number(id);
  if (!Number.isInteger(deadlineId)) {
    return NextResponse.json({ error: "Invalid deadline id" }, { status: 400 });
  }

  const db = getDb();
  const result = db
    .prepare("UPDATE deadlines SET completed = 1 WHERE id = ?")
    .run(deadlineId);

  if (result.changes === 0) {
    return NextResponse.json({ error: "Deadline not found" }, { status: 404 });
  }

  return NextResponse.json({ ok: true });
}
