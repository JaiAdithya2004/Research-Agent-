"use client";

import { useCallback, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { auditQueryOptions } from "@/hooks/use-audit";
import { useGaps, useSource, useSources } from "@/hooks/use-sources";
import { ApiError } from "@/lib/api/errors";
import { buildBanners } from "@/lib/audit-banners";
import { rankGaps, visibleGaps } from "@/lib/gaps";
import { describeFreshness } from "@/lib/freshness";
import { limitRows, summarizeSources } from "@/lib/sources";
import { hasSourceFilters, parseSourcesParams, serializeSourcesParams, type SourcesParams } from "@/lib/source-filters";

/** Banners that change how the sources and gap numbers should be read. */
const SOURCE_BANNERS = new Set(["failed", "capped", "degraded", "running"]);

/** URL state, queries and derived views for the sources page. */
export function useSourcesPage() {
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const params = parseSourcesParams(search);
  const auditId = params.auditId;

  const [now] = useState(() => Date.now()); // fixed per mount: freshness is not a live clock
  const [showAllGaps, setShowAllGaps] = useState(false);
  const [showAllRows, setShowAllRows] = useState(false);
  const [domainInput, setDomainInput] = useState(params.domain);

  const replace = useCallback(
    (next: SourcesParams) => router.replace(`${pathname}${serializeSourcesParams(next)}`, { scroll: false }),
    [router, pathname],
  );

  const audit = useQuery({ ...auditQueryOptions(auditId ?? ""), enabled: auditId !== null });
  const gaps = useGaps(auditId);
  const sources = useSources(auditId, params);
  const detail = useSource(auditId, params.sourceId);

  const ranked = gaps.data ? rankGaps(gaps.data.entries) : [];
  const gapRows = visibleGaps(ranked, showAllGaps);
  const sourceRows = limitRows(sources.data ?? [], showAllRows);

  return {
    params,
    auditId,
    filtered: hasSourceFilters(params),
    domainInput,
    setDomainInput,
    setType: (sourceType: SourcesParams["sourceType"]) => replace({ ...params, sourceType, sourceId: null }),
    setStatus: (fetchStatus: SourcesParams["fetchStatus"]) => replace({ ...params, fetchStatus, sourceId: null }),
    applyDomain: () => replace({ ...params, domain: domainInput.trim(), sourceId: null }),
    clearFilters: () => {
      setDomainInput("");
      replace({ ...params, sourceType: null, fetchStatus: null, domain: "", sourceId: null });
    },
    closeDetail: () => replace({ ...params, sourceId: null }),
    banners: audit.data ? buildBanners(audit.data, undefined).filter((b) => SOURCE_BANNERS.has(b.id)) : [],
    audit,
    auditNotFound: audit.error instanceof ApiError && audit.error.status === 404,
    gaps: { query: gaps, rows: gapRows.shown, hidden: gapRows.hidden, total: ranked.length, showAll: showAllGaps, toggleAll: () => setShowAllGaps((v) => !v) },
    sources: {
      query: sources,
      rows: sourceRows.shown,
      hidden: sourceRows.hidden,
      summary: summarizeSources(sources.data ?? []),
      showAll: showAllRows,
      toggleAll: () => setShowAllRows((v) => !v),
    },
    detail: {
      query: detail,
      freshness: describeFreshness(detail.data?.snapshot_fetched_at, now),
      notFound: detail.error instanceof ApiError && detail.error.status === 404,
    },
  };
}
