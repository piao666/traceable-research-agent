import { useEffect, useRef } from "react";

/** Move keyboard context with a deep link, but never steal focus on polling. */
export function useFocusTarget(id: string | null, ready: boolean) {
  const previous = useRef<string | null>(null);
  useEffect(() => {
    if (!id) { previous.current = null; return; }
    if (!ready || previous.current === id) return;
    const target = document.getElementById(id);
    if (!target) return;
    previous.current = id;
    target.focus({ preventScroll: true });
    target.scrollIntoView?.({ block: "center", behavior: "instant" });
  }, [id, ready]);
}
