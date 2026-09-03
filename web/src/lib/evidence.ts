import type { ProvenanceBundleResponse } from "../api/client";

export function safeExternalUrl(value: string): string | null {
  if (!/^https?:\/\//i.test(value.trim())) return null;
  try {
    const url = new URL(value.trim());
    return url.username || url.password ? null : url.href;
  } catch { return null; }
}

export const textValue = (value: unknown) => typeof value === "string" ? value : "";
export const basisLabel = (value: unknown) => ({ full_text: "全文来源", partial: "部分正文", snippet_only: "搜索摘要", search_snippet: "搜索摘要", metadata_only: "仅元数据", abstract: "摘要" }[textValue(value)] ?? "内容范围未标注");

export interface CitationTarget {
  label: string;
  passageId: string;
  text: string;
  claim: string;
  origin: string;
  snapshotId: string;
  source: string;
  url: string;
  traceId: string;
  basis: string;
  relation: string;
  resolved: boolean;
}

/** Exact persisted identifiers only; no ordinal/nearest-number repair. */
export function citationTargets(bundle: ProvenanceBundleResponse | null): Map<string, CitationTarget> {
  const targets = new Map<string, CitationTarget>();
  if (!bundle) return targets;
  const index = (items: Record<string, unknown>[], key: string) => new Map(items.map((item) => [textValue(item[key]), item]));
  const passages = index(bundle.passages, "passage_id");
  const snapshots = index(bundle.source_snapshots, "snapshot_id");
  const documents = index(bundle.source_documents, "document_id");
  const claims = index(bundle.report_claims, "report_claim_id");
  const edges = index(bundle.edges, "edge_id");
  for (const citation of bundle.citations) {
    const label = textValue(citation.citation_label);
    if (!label) continue;
    const passage = passages.get(textValue(citation.passage_id));
    const snapshot = snapshots.get(textValue(passage?.snapshot_id));
    const source = documents.get(textValue(snapshot?.document_id));
    const claim = claims.get(textValue(citation.report_claim_id));
    const edge = edges.get(textValue(citation.edge_id));
    const target = {
      label, passageId: textValue(citation.passage_id), text: textValue(passage?.text),
      claim: textValue(claim?.claim_text), origin: textValue(claim?.origin), snapshotId: textValue(passage?.snapshot_id),
      source: textValue(source?.title), url: textValue(source?.canonical_uri),
      traceId: textValue(passage?.trace_id), basis: basisLabel(passage?.content_basis), relation: textValue(edge?.relation),
      resolved: !!(passage && snapshot && source && claim && textValue(passage.text)),
    };
    // Conflicting repeated labels are ambiguous, not a valid citation target.
    if (targets.has(label)) { targets.get(label)!.resolved = false; continue; }
    targets.set(label, target);
  }
  return targets;
}
