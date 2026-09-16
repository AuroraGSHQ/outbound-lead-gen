import { NextResponse } from "next/server";
import { hasValidSession } from "@/lib/auth";
import { backendFetch } from "@/lib/backend";

/**
 * Never a direct DB write — this must go through the Python backend's
 * modules/contracts reject_contract. See lib/backend.ts's file comment.
 */
export async function POST(request: Request, { params }: { params: Promise<{ id: string }> }) {
  if (!(await hasValidSession())) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  const { id } = await params;
  const contractId = Number(id);
  if (!Number.isInteger(contractId)) {
    return NextResponse.json({ error: "Invalid contract id" }, { status: 400 });
  }

  let reason = "";
  try {
    const body = await request.json();
    reason = typeof body?.reason === "string" ? body.reason : "";
  } catch {
    // no body / not JSON — reason stays ""
  }

  let backendRes: Response;
  try {
    backendRes = await backendFetch(`/contracts/${contractId}/reject`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ reason }),
    });
  } catch (err) {
    return NextResponse.json(
      { error: `Could not reach the backend: ${err instanceof Error ? err.message : String(err)}` },
      { status: 502 }
    );
  }

  const text = await backendRes.text();
  if (!backendRes.ok) {
    return NextResponse.json(
      { error: text || `Backend returned ${backendRes.status}` },
      { status: backendRes.status }
    );
  }

  return new NextResponse(text || JSON.stringify({ ok: true }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}
