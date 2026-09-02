import { useEffect, useState } from "react";
import { api, errorMessage, type EvidenceBundleResponse, type ProvenanceBundleResponse } from "../api/client";

export function useEvidence(runId: string, revision: string) {
  const [bundle, setBundle] = useState<EvidenceBundleResponse | null>(null);
  const [provenance, setProvenance] = useState<ProvenanceBundleResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [provenanceError, setProvenanceError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); let active = true;
    setLoading(true); setError(""); setProvenanceError(""); setBundle(null); setProvenance(null);
    void Promise.allSettled([api.getEvidence(runId, controller.signal), api.getProvenance(runId, controller.signal)]).then(([evidence, graph]) => {
      if (!active) return;
      if (evidence.status === "fulfilled") setBundle(evidence.value);
      else { setBundle(null); setError(errorMessage(evidence.reason)); }
      if (graph.status === "fulfilled") setProvenance(graph.value);
      else { setProvenance(null); setProvenanceError(errorMessage(graph.reason)); }
      setLoading(false);
    });
    return () => { active = false; controller.abort(); };
  }, [runId, revision, retry]);
  return { bundle, provenance, loading, error, provenanceError, refresh: () => setRetry((value) => value + 1) };
}
