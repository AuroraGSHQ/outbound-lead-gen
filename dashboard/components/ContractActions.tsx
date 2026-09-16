"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export function ContractActions({ contractId }: { contractId: number }) {
  const router = useRouter();
  const [loading, setLoading] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showReject, setShowReject] = useState(false);
  const [reason, setReason] = useState("");

  async function approve() {
    setLoading("approve");
    setError(null);
    try {
      const res = await fetch(`/api/contracts/${contractId}/approve`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data.error || "Approve failed");
        setLoading(null);
        return;
      }
      router.refresh();
    } catch {
      setError("Could not reach the server");
      setLoading(null);
    }
  }

  async function reject() {
    setLoading("reject");
    setError(null);
    try {
      const res = await fetch(`/api/contracts/${contractId}/reject`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data.error || "Reject failed");
        setLoading(null);
        return;
      }
      router.refresh();
    } catch {
      setError("Could not reach the server");
      setLoading(null);
    }
  }

  return (
    <div className="flex flex-col items-end gap-2">
      <div className="flex gap-2">
        <button
          onClick={approve}
          disabled={loading !== null}
          className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-emerald-500 disabled:opacity-50"
        >
          {loading === "approve" ? "Approving..." : "Approve"}
        </button>
        <button
          onClick={() => setShowReject((v) => !v)}
          disabled={loading !== null}
          className="rounded-lg border border-red-300 px-3 py-1.5 text-xs font-medium text-red-700 transition hover:bg-red-50 disabled:opacity-50 dark:border-red-800 dark:text-red-300 dark:hover:bg-red-950"
        >
          Reject
        </button>
      </div>
      {showReject && (
        <div className="flex w-64 flex-col items-end gap-1">
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Reason (optional)"
            rows={2}
            className="w-full rounded-lg border border-zinc-300 bg-white px-2 py-1 text-xs outline-none focus:border-zinc-500 dark:border-zinc-700 dark:bg-zinc-950"
          />
          <button
            onClick={reject}
            disabled={loading !== null}
            className="rounded-lg bg-red-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-red-500 disabled:opacity-50"
          >
            {loading === "reject" ? "Rejecting..." : "Confirm reject"}
          </button>
        </div>
      )}
      {error && <span className="text-xs text-red-600 dark:text-red-400">{error}</span>}
    </div>
  );
}
