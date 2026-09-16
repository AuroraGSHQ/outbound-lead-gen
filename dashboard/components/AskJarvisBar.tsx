"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

interface Turn {
  role: "user" | "jarvis";
  text: string;
  isError?: boolean;
}

export function AskJarvisBar() {
  const router = useRouter();
  const [message, setMessage] = useState("");
  const [history, setHistory] = useState<Turn[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const text = message.trim();
    if (!text || loading) return;

    setMessage("");
    setOpen(true);
    setHistory((h) => [...h, { role: "user", text }]);
    setLoading(true);

    try {
      const res = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setHistory((h) => [
          ...h,
          { role: "jarvis", text: data.error || "Something went wrong.", isError: true },
        ]);
      } else {
        setHistory((h) => [...h, { role: "jarvis", text: data.reply || "(no reply)" }]);
        // Something Jarvis did (e.g. marking a deadline complete, approving
        // a contract) may have changed data this page is showing.
        router.refresh();
      }
    } catch {
      setHistory((h) => [
        ...h,
        { role: "jarvis", text: "Could not reach the server. Try again.", isError: true },
      ]);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  }

  return (
    <div className="w-full">
      <form onSubmit={onSubmit} className="flex items-center gap-2">
        <span className="hidden shrink-0 text-sm font-medium text-zinc-500 sm:inline dark:text-zinc-400">
          Ask Jarvis
        </span>
        <input
          ref={inputRef}
          type="text"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder="e.g. how many prospects are in the marketing segment?"
          className="min-w-0 flex-1 rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 outline-none focus:border-zinc-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
        />
        <button
          type="submit"
          disabled={loading || !message.trim()}
          className="shrink-0 rounded-lg bg-zinc-900 px-3 py-2 text-sm font-medium text-white transition hover:bg-zinc-700 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
        >
          {loading ? "..." : "Ask"}
        </button>
        {history.length > 0 && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="shrink-0 rounded-lg border border-zinc-300 px-2 py-2 text-xs text-zinc-500 dark:border-zinc-700 dark:text-zinc-400"
          >
            {open ? "Hide" : "Show"}
          </button>
        )}
      </form>

      {open && history.length > 0 && (
        <div className="mt-2 max-h-64 space-y-2 overflow-y-auto rounded-lg border border-zinc-200 bg-zinc-50 p-3 text-sm dark:border-zinc-800 dark:bg-zinc-900">
          {history.map((turn, i) => (
            <div key={i} className={turn.role === "user" ? "text-zinc-900 dark:text-zinc-100" : ""}>
              <span className="font-medium">
                {turn.role === "user" ? "You: " : "Jarvis: "}
              </span>
              <span
                className={
                  turn.isError
                    ? "text-red-600 dark:text-red-400"
                    : "text-zinc-600 dark:text-zinc-300"
                }
              >
                {turn.text}
              </span>
            </div>
          ))}
          {loading && <div className="text-zinc-400">Jarvis is thinking...</div>}
        </div>
      )}
    </div>
  );
}
