/** Small date/JSON formatting helpers shared across pages. */

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const withZone = /Z|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`;
  const date = new Date(withZone);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const withZone = /Z|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`;
  const date = new Date(withZone);
  if (Number.isNaN(date.getTime())) {
    // Plain "YYYY-MM-DD" due dates have no time component — parse as local.
    const plain = new Date(`${iso}T00:00:00`);
    if (Number.isNaN(plain.getTime())) return iso;
    return plain.toLocaleDateString(undefined, { dateStyle: "medium" });
  }
  return date.toLocaleDateString(undefined, { dateStyle: "medium" });
}

export function isOverdue(dueDate: string): boolean {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const due = new Date(`${dueDate.slice(0, 10)}T00:00:00`);
  return due.getTime() < today.getTime();
}

/** Best-effort pretty-print of a JSON TEXT column; falls back to the raw
 * string if it isn't valid JSON (schema stores these as free-form TEXT). */
export function parseJsonLoose(text: string | null | undefined): unknown {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}
