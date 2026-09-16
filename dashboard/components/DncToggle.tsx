"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export function DncToggle({ contactId, initial }: { contactId: number; initial: boolean }) {
  const router = useRouter();
  const [value, setValue] = useState(initial);
  const [loading, setLoading] = useState(false);

  async function toggle() {
    const next = !value;
    setLoading(true);
    setValue(next); // optimistic
    try {
      const res = await fetch(`/api/contacts/${contactId}/dnc`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ do_not_call: next }),
      });
      if (!res.ok) {
        setValue(!next); // revert
      } else {
        router.refresh();
      }
    } catch {
      setValue(!next);
    } finally {
      setLoading(false);
    }
  }

  return (
    <button
      onClick={toggle}
      disabled={loading}
      className={`rounded-full border px-2.5 py-1 text-xs font-medium transition disabled:opacity-50 ${
        value
          ? "border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-200"
          : "border-zinc-200 bg-white text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800"
      }`}
      title="Toggle do-not-call"
    >
      {value ? "Do not call" : "Callable"}
    </button>
  );
}
