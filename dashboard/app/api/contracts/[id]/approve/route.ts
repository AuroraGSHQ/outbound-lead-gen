import { NextResponse } from "next/server";
import { hasValidSession } from "@/lib/auth";
import { backendFetch } from "@/lib/backend";

/**
 * Never a direct DB write — this must go through the Python backend's
 * modules/contracts approve_contract, which drives the actual PandaDoc/
 * DocuSign send. See lib/backend.ts's file comment.
 */
export async function POST(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  if (!(await hasValidSession())) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  const { id } = await params;
  const contractId = Number(id);
  if (!Number.isInteger(contractId)) {
    return NextResponse.json({ error: "Invalid contract id" }, { status: 400 });
  }

  let backendRes: Response;
  try {
    backendRes = await backendFetch(`/contracts/${contractId}/approve`, { method: "POST" });
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
