export function readDraft(key: string): string {
  try { return sessionStorage.getItem(key) ?? ""; } catch { return ""; }
}

export function saveDraft(key: string, value: string): boolean {
  try { sessionStorage.setItem(key, value); return true; } catch { return false; }
}

export function removeDraft(key: string) {
  try { sessionStorage.removeItem(key); } catch { /* Cannot clear disabled storage. */ }
}
