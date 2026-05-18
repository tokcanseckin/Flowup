/**
 * Thin wrapper around Plausible Analytics.
 * The Plausible script is loaded in index.html; this module provides a
 * typed, safe call site so every component can fire custom events without
 * duplicating the `window.plausible` guard.
 */
type PlausibleFn = (
  event: string,
  options?: { props?: Record<string, string | number | boolean> },
) => void

declare global {
  interface Window {
    plausible?: PlausibleFn
  }
}

export function track(
  event: string,
  props?: Record<string, string | number | boolean>,
): void {
  try {
    window.plausible?.(event, props ? { props } : undefined)
  } catch {
    // Never let analytics failures surface to users
  }
}
