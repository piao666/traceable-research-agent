import { useEffect, useState } from "react";
import { api, errorMessage, type ResultEvidenceResponse } from "../api/client";

export function useEvidence(runId: string, revision: string) {
  const [provenance, setProvenance] = useState<ResultEvidenceResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); let active = true;
    setLoading(true); setError(""); setProvenance(null);
    void api.getResultEvidence(runId, controller.signal)
      .then((graph) => { if (active) setProvenance(graph); })
      .catch((reason: unknown) => { if (active) setError(errorMessage(reason)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [runId, revision, retry]);
  return { provenance, loading, error, refresh: () => setRetry((value) => value + 1) };
}
