import type { components } from "@/lib/api/schema.gen";

export type SourceType = components["schemas"]["SourceOut"]["source_type"];
export type SourceFetchStatus = components["schemas"]["SourceOut"]["fetch_status"];

export const SOURCE_TYPES: readonly SourceType[] = ["own_site", "review", "news", "forum", "competitor", "other"];
export const FETCH_STATUSES: readonly SourceFetchStatus[] = ["ok", "unreachable", "blocked", "paywalled", "too_large", "error"];
export const MAX_DOMAIN_FILTER = 255; // the API's maxLength for the domain filter

export const SOURCE_TYPE_LABEL: Record<SourceType, string> = {
  own_site: "Own site",
  review: "Review site",
  news: "News",
  forum: "Forum",
  competitor: "Competitor",
  other: "Other",
};

/** Everything on the sources page that lives in the URL. */
export interface SourcesParams {
  auditId: string | null;
  sourceType: SourceType | null;
  fetchStatus: SourceFetchStatus | null;
  domain: string;
  /** The source whose detail is open. */
  sourceId: string | null;
}

type ParamReader = { get(name: string): string | null };

export function parseSourcesParams(params: ParamReader): SourcesParams {
  const pick = (name: string) => {
    const v = params.get(name)?.trim();
    return v ? v : null;
  };
  const type = pick("source_type");
  const status = pick("fetch_status");
  return {
    auditId: pick("audit_id"),
    sourceType: SOURCE_TYPES.find((t) => t === type) ?? null,
    fetchStatus: FETCH_STATUSES.find((s) => s === status) ?? null,
    domain: (pick("domain") ?? "").slice(0, MAX_DOMAIN_FILTER),
    sourceId: pick("source"),
  };
}

export function serializeSourcesParams(p: SourcesParams): string {
  const q = new URLSearchParams();
  if (p.auditId) q.set("audit_id", p.auditId);
  if (p.sourceType) q.set("source_type", p.sourceType);
  if (p.fetchStatus) q.set("fetch_status", p.fetchStatus);
  if (p.domain.trim()) q.set("domain", p.domain.trim().slice(0, MAX_DOMAIN_FILTER));
  if (p.sourceId) q.set("source", p.sourceId);
  const text = q.toString();
  return text ? `?${text}` : "";
}

export function hasSourceFilters(p: SourcesParams): boolean {
  return p.sourceType !== null || p.fetchStatus !== null || p.domain.trim() !== "";
}
