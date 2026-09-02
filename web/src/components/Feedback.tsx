import type { ReactNode } from "react";
import { Button } from "./primitives";

export function LoadingState({ children = "正在读取…" }: { children?: ReactNode }) {
  return <p className="loading-state" role="status" aria-live="polite">{children}</p>;
}

export function ErrorState({ message, retry, retryLabel = "重新读取" }: { message: string; retry?: () => void; retryLabel?: string }) {
  return <div className="error-banner feedback-row"><p role="alert">{message}</p>{retry && <Button variant="secondary" onClick={retry}>{retryLabel}</Button>}</div>;
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <p className="empty-state" role="status">{children}</p>;
}
