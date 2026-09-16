"use client";

import { useEffect } from "react";

/** Registers the minimal app-shell service worker (public/sw.js) so "Add
 * to Home Screen" installs as a real PWA. No-op if unsupported. */
export function ServiceWorkerRegister() {
  useEffect(() => {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => {
        // Installability just degrades gracefully — not worth surfacing.
      });
    }
  }, []);

  return null;
}
