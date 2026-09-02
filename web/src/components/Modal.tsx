import { useEffect, useId, useRef, type ReactNode } from "react";

/** Native modal supplies focus containment/inert background; restore focus on exit. */
export function Modal({ title, description, busy, close, children }: {
  title: string; description: string; busy: boolean; close: () => void; children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const id = useId();
  useEffect(() => {
    const previous = document.activeElement;
    const element = dialog.current;
    element?.showModal();
    titleRef.current?.focus();
    return () => {
      element?.close();
      if (previous instanceof HTMLElement && previous !== document.body && previous.isConnected && !previous.matches(":disabled")) previous.focus();
      else document.getElementById("main-content")?.focus();
    };
  }, []);
  return <dialog ref={dialog} className="action-dialog" aria-modal="true" aria-labelledby={`${id}-title`} aria-describedby={`${id}-description`} aria-busy={busy}
    onCancel={(event) => { event.preventDefault(); if (!busy) close(); }}>
    <h2 id={`${id}-title`} ref={titleRef} tabIndex={-1}>{title}</h2>
    <p id={`${id}-description`}>{description}</p>{children}
  </dialog>;
}
