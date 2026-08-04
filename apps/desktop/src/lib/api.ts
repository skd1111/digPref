/**
 * Pure-JS helpers (no Tauri dependency) — safe to use in tests and SSR contexts.
 */
export function nowIso(): string {
  return new Date().toISOString();
}

export function uuid(): string {
  return crypto.randomUUID();
}