import Anthropic from "@anthropic-ai/sdk";
import { NextResponse } from "next/server";
import { hasValidSession } from "@/lib/auth";
import { backendFetch } from "@/lib/backend";
import { getDb, getReadonlyDb } from "@/lib/db";

/**
 * "Ask Jarvis" command bar backend. A manual tool-use loop (not the beta
 * tool runner — this is 3-4 simple tools with a small iteration cap) using
 * @anthropic-ai/sdk against the DB directly and lib/backend.ts for the one
 * action with an external side effect. See dashboard/README.md.
 */

const MAX_ITERATIONS = 6;
const MAX_ROWS = 200;

const SYSTEM_PROMPT = `You are Jarvis, the owner's assistant embedded in their internal operations \
dashboard for a small marketing/services business. You are talking directly to the owner, the \
one person who uses this system.

You have exactly four tools, and only these four:
- query_database: read-only SQL SELECT against the operations database (contacts, call_logs, \
deadlines, contracts, notifications, emails).
- mark_deadline_complete: marks one deadline done.
- approve_contract: approves a pending contract, which triggers the actual send for signature \
(PandaDoc/DocuSign) via the backend.
- reject_contract: rejects a pending contract with an optional reason.

Be honest about what you can and cannot do. You do NOT have a tool to place, trigger, or start a \
phone call — that is not wired up yet in this system (only a scheduled outbound-calling window \
and an inbound call-ended webhook exist on the backend, no on-demand "call this contact now" \
endpoint). If the owner asks you to call someone, or do anything else you have no tool for, say \
plainly that it isn't wired up yet — never claim to have done something you have no tool for, \
and never fabricate a result (e.g. do not write a fake call_logs row via query_database — that \
tool is read-only and enforced as such).

When you answer a factual question, use query_database and base your answer only on what it \
returns. Keep replies short and conversational — this renders in a small chat-style panel on a \
phone screen. When you take an action (marking a deadline done, approving/rejecting a contract), \
say plainly what you did and the result.`;

const tools: Anthropic.Tool[] = [
  {
    name: "query_database",
    description:
      "Run a read-only SQL SELECT query against the operations database to answer questions " +
      "about contacts, call_logs, deadlines, contracts, notifications, or emails. Must be a " +
      "single SELECT statement. Results are capped at 200 rows.",
    input_schema: {
      type: "object",
      properties: {
        sql: {
          type: "string",
          description: "A single SQL SELECT statement, no trailing semicolon needed.",
        },
      },
      required: ["sql"],
    },
  },
  {
    name: "mark_deadline_complete",
    description: "Mark a deadline as completed by its id.",
    input_schema: {
      type: "object",
      properties: {
        deadline_id: { type: "integer", description: "The deadlines.id to mark complete." },
      },
      required: ["deadline_id"],
    },
  },
  {
    name: "approve_contract",
    description:
      "Approve a pending contract by its id. This triggers the actual send-for-signature via " +
      "PandaDoc/DocuSign through the backend — only call this when the owner clearly wants to " +
      "approve that specific contract.",
    input_schema: {
      type: "object",
      properties: {
        contract_id: { type: "integer", description: "The contracts.id to approve." },
      },
      required: ["contract_id"],
    },
  },
  {
    name: "reject_contract",
    description: "Reject a pending contract by its id, with an optional reason.",
    input_schema: {
      type: "object",
      properties: {
        contract_id: { type: "integer", description: "The contracts.id to reject." },
        reason: { type: "string", description: "Optional reason for the rejection." },
      },
      required: ["contract_id"],
    },
  },
];

/** Defense-in-depth on top of getReadonlyDb()'s `query_only` pragma, which
 * is the actual enforcement boundary. */
function isSafeSelect(sql: string): boolean {
  const trimmed = sql.trim();
  if (!/^select\b/i.test(trimmed)) return false;

  const withoutTrailingSemicolon = trimmed.endsWith(";") ? trimmed.slice(0, -1) : trimmed;
  if (withoutTrailingSemicolon.includes(";")) return false;

  const forbidden = /\b(insert|update|delete|drop|alter|attach|pragma|create)\b/i;
  if (forbidden.test(withoutTrailingSemicolon)) return false;

  return true;
}

function runQueryDatabase(sql: string): { content: string; is_error?: boolean } {
  if (!isSafeSelect(sql)) {
    return {
      content: "Rejected: only a single read-only SELECT statement is allowed.",
      is_error: true,
    };
  }

  let query = sql.trim();
  if (query.endsWith(";")) query = query.slice(0, -1);
  if (!/\blimit\b/i.test(query)) {
    query = `${query} LIMIT ${MAX_ROWS}`;
  }

  try {
    const rows = getReadonlyDb().prepare(query).all() as unknown[];
    const capped = rows.slice(0, MAX_ROWS);
    return { content: JSON.stringify(capped) };
  } catch (err) {
    return {
      content: `Query error: ${err instanceof Error ? err.message : String(err)}`,
      is_error: true,
    };
  }
}

function runMarkDeadlineComplete(deadlineId: unknown): { content: string; is_error?: boolean } {
  const id = Number(deadlineId);
  if (!Number.isInteger(id)) {
    return { content: "Invalid deadline_id.", is_error: true };
  }
  try {
    const result = getDb().prepare("UPDATE deadlines SET completed = 1 WHERE id = ?").run(id);
    return { content: JSON.stringify({ updated: result.changes > 0 }) };
  } catch (err) {
    return {
      content: `Update error: ${err instanceof Error ? err.message : String(err)}`,
      is_error: true,
    };
  }
}

async function runApproveContract(contractId: unknown): Promise<{ content: string; is_error?: boolean }> {
  const id = Number(contractId);
  if (!Number.isInteger(id)) {
    return { content: "Invalid contract_id.", is_error: true };
  }
  try {
    const res = await backendFetch(`/contracts/${id}/approve`, { method: "POST" });
    const body = await res.text();
    return { content: JSON.stringify({ status: res.status, body }), is_error: !res.ok };
  } catch (err) {
    return {
      content: `Backend error: ${err instanceof Error ? err.message : String(err)}`,
      is_error: true,
    };
  }
}

async function runRejectContract(
  contractId: unknown,
  reason: unknown
): Promise<{ content: string; is_error?: boolean }> {
  const id = Number(contractId);
  if (!Number.isInteger(id)) {
    return { content: "Invalid contract_id.", is_error: true };
  }
  try {
    const res = await backendFetch(`/contracts/${id}/reject`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ reason: typeof reason === "string" ? reason : "" }),
    });
    const body = await res.text();
    return { content: JSON.stringify({ status: res.status, body }), is_error: !res.ok };
  } catch (err) {
    return {
      content: `Backend error: ${err instanceof Error ? err.message : String(err)}`,
      is_error: true,
    };
  }
}

async function executeTool(
  name: string,
  input: Record<string, unknown>
): Promise<{ content: string; is_error?: boolean }> {
  switch (name) {
    case "query_database":
      return runQueryDatabase(String(input.sql ?? ""));
    case "mark_deadline_complete":
      return runMarkDeadlineComplete(input.deadline_id);
    case "approve_contract":
      return runApproveContract(input.contract_id);
    case "reject_contract":
      return runRejectContract(input.contract_id, input.reason);
    default:
      return { content: `Unknown tool: ${name}`, is_error: true };
  }
}

export async function POST(request: Request) {
  if (!(await hasValidSession())) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  let body: { message?: string };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid request body" }, { status: 400 });
  }

  const userMessage = (body.message || "").trim();
  if (!userMessage) {
    return NextResponse.json({ error: "message is required" }, { status: 400 });
  }

  if (!process.env.ANTHROPIC_API_KEY) {
    return NextResponse.json(
      { error: "ANTHROPIC_API_KEY is not configured for this deployment." },
      { status: 500 }
    );
  }

  const client = new Anthropic();
  const messages: Anthropic.MessageParam[] = [{ role: "user", content: userMessage }];

  try {
    for (let i = 0; i < MAX_ITERATIONS; i++) {
      const response = await client.messages.create({
        model: process.env.ANTHROPIC_MODEL || "claude-sonnet-5",
        max_tokens: 4096,
        system: SYSTEM_PROMPT,
        tools,
        messages,
      });

      if (response.stop_reason !== "tool_use") {
        const text = response.content
          .filter((block): block is Anthropic.TextBlock => block.type === "text")
          .map((block) => block.text)
          .join("\n")
          .trim();
        return NextResponse.json({ reply: text || "(no reply)" });
      }

      messages.push({ role: "assistant", content: response.content });

      const toolResults: Anthropic.ToolResultBlockParam[] = [];
      for (const block of response.content) {
        if (block.type !== "tool_use") continue;
        const result = await executeTool(block.name, (block.input as Record<string, unknown>) ?? {});
        toolResults.push({
          type: "tool_result",
          tool_use_id: block.id,
          content: result.content,
          is_error: result.is_error,
        });
      }
      messages.push({ role: "user", content: toolResults });
    }
  } catch (err) {
    return NextResponse.json(
      { error: `Ask Jarvis failed: ${err instanceof Error ? err.message : String(err)}` },
      { status: 502 }
    );
  }

  return NextResponse.json({
    reply: "I wasn't able to finish that in time — try asking again, maybe more specifically.",
  });
}
