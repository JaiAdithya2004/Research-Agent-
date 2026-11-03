import type { components } from "@/lib/api/schema.gen";

export type AuditStatus = components["schemas"]["AuditListItem"]["status"];
export const STATUS_VALUES: readonly AuditStatus[] = ["running", "complete", "capped", "failed"];
export const MAX_BRAND_FILTER = 255; // the API's maxLength for the brand filter

export interface AuditFilters {
  status: AuditStatus | null;
  brand: string;
}

export const NO_FILTERS: AuditFilters = { status: null, brand: "" };

export function parseFilters(params: { get(name: string): string | null }): AuditFilters {
  const rawStatus = params.get("status");
  const status = STATUS_VALUES.find((s) => s === rawStatus) ?? null;
  const brand = (params.get("brand") ?? "").trim().slice(0, MAX_BRAND_FILTER);
  return { status, brand };
}

/** "" when no filter is set, otherwise "?status=...&brand=...". */
export function serializeFilters(filters: AuditFilters): string {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  const brand = filters.brand.trim();
  if (brand) params.set("brand", brand.slice(0, MAX_BRAND_FILTER));
  const text = params.toString();
  return text ? `?${text}` : "";
}

export function hasFilters(filters: AuditFilters): boolean {
  return filters.status !== null || filters.brand.trim() !== "";
}
