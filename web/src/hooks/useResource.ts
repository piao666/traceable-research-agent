import { useCallback, useEffect, useState } from "react";
import { errorMessage } from "../api/client";

/** Caller memoizes loader; abort and identity guards discard stale responses. */
export function useResource<T>(loader: (signal: AbortSignal) => Promise<T>) {
  const [state, setState] = useState<{ data: T | null; error: string; loading: boolean }>({ data: null, error: "", loading: true });
  const [version, setVersion] = useState(0);
  const refresh = useCallback(() => setVersion((value) => value + 1), []);
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setState({ data: null, error: "", loading: true });
    loader(controller.signal).then((data) => {
      if (active) setState({ data, error: "", loading: false });
    }).catch((error: unknown) => {
      if (active) setState({ data: null, error: errorMessage(error), loading: false });
    });
    return () => { active = false; controller.abort(); };
  }, [loader, version]);
  return { ...state, refresh };
}
