import type { components } from "@/lib/api/schema.gen";
import type { Tone } from "@/lib/audit-status";
import { fetchReasonLabel } from "@/lib/fetch-reason";

type Evidence = Pick<components["schemas"]["EvidenceBrief"], "fetch_status" | "fetch_reason" | "brand_on_page">;
export type FetchStatus = components["schemas"]["EvidenceBrief"]["fetch_status"];

/**
 * found:          the page was read and the brand is on it.
 * not_found:      the page was read and the brand is NOT on it.
 * unreadable:     we could NOT read the page. This says nothing about the brand.
 * not_researched: the page was never fetched (research off, a cap was hit, or the URL was prose-only).
 */
export type EvidenceKind = "found" | "not_found" | "unreadable" | "not_researched";
export type EvidenceIcon = "check" | "search-x" | "ban" | "minus";

export interface EvidencePresentation {
  kind: EvidenceKind;
  label: string;
  /** Longer plain-language explanation. */
  meaning: string;
  /** Friendly fetch reason, when there is one. */
  reason: string | null;
  tone: Tone | "neutral";
  icon: EvidenceIcon;
}

const UNREADABLE_LABEL: Record<FetchStatus, string> = {
  ok: "Page read, result unknown",
  unreachable: "Could not read: unreachable",
  blocked: "Could not read: blocked",
  paywalled: "Could not read: paywalled",
  too_large: "Could not read: too large",
  error: "Could not read: fetch error",
};

export const FETCH_STATUS_LABEL: Record<FetchStatus, string> = {
  ok: "Fetched",
  unreachable: "Unreachable",
  blocked: "Blocked",
  paywalled: "Paywalled",
  too_large: "Too large",
  error: "Fetch error",
};

/**
 * The single place that decides how a source's result reads. `brand_on_page === false` only counts
 * as "not found" when the fetch actually succeeded: an unreadable page is never "brand absent".
 */
export function presentEvidence(evidence: Evidence | null): EvidencePresentation {
  if (evidence === null) {
    return {
      kind: "not_researched",
      label: "Page not checked",
      meaning:
        "This page was never fetched (page research was off, a cap was reached, or the URL only appeared in the answer text).",
      reason: null,
      tone: "neutral",
      icon: "minus",
    };
  }
  const reason = evidence.fetch_reason && evidence.fetch_reason !== "ok" ? fetchReasonLabel(evidence.fetch_reason) : null;
  if (evidence.fetch_status === "ok" && evidence.brand_on_page === true) {
    return {
      kind: "found",
      label: "Brand found on page",
      meaning: "The page was read and the brand appears in its text.",
      reason,
      tone: "success",
      icon: "check",
    };
  }
  if (evidence.fetch_status === "ok" && evidence.brand_on_page === false) {
    return {
      kind: "not_found",
      label: "Page read: brand not on it",
      meaning: "The page was read successfully and the brand does not appear in its text.",
      reason,
      tone: "info",
      icon: "search-x",
    };
  }
  return {
    kind: "unreadable",
    label: UNREADABLE_LABEL[evidence.fetch_status],
    meaning:
      "We could not read this page, so we do not know whether the brand is on it. This is not the same as the brand being absent.",
    reason,
    tone: "warning",
    icon: "ban",
  };
}

export interface CitationOrigin {
  kind: "grounding" | "prose";
  label: string;
  meaning: string;
}

export function citationOrigin(c: { source: "grounding_metadata" | "answer_text"; unverified: boolean }): CitationOrigin {
  if (c.source === "grounding_metadata" && !c.unverified) {
    return {
      kind: "grounding",
      label: "From grounding metadata",
      meaning: "The search provider returned this URL as a structured citation.",
    };
  }
  return {
    kind: "prose",
    label: "Only in answer text (unverified)",
    meaning: "This URL appears only in the written answer. It was never confirmed by the provider and is never fetched.",
  };
}
