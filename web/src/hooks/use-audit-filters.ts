"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { NO_FILTERS, parseFilters, serializeFilters, type AuditFilters } from "@/lib/audit-filters";
import type { AuditStatus } from "@/lib/audit-filters";
import { useDebouncedValue } from "@/hooks/use-debounced-value";

export const BRAND_DEBOUNCE_MS = 300;

/**
 * The filters live in the URL (?status=...&brand=...) so reload and back/forward work.
 * The brand text box is local state that is debounced into the URL.
 */
export function useAuditFilters() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const urlFilters = parseFilters(searchParams);

  const [brandInput, setBrandInput] = useState(urlFilters.brand);
  const debouncedBrand = useDebouncedValue(brandInput, BRAND_DEBOUNCE_MS);
  const lastPushedBrand = useRef(urlFilters.brand);

  const replace = useCallback(
    (next: AuditFilters) => router.replace(`${pathname}${serializeFilters(next)}`, { scroll: false }),
    [router, pathname],
  );

  // Text box -> URL, after the debounce.
  useEffect(() => {
    const trimmed = debouncedBrand.trim();
    if (trimmed === urlFilters.brand || trimmed === lastPushedBrand.current) return;
    lastPushedBrand.current = trimmed;
    replace({ ...urlFilters, brand: trimmed });
  }, [debouncedBrand, urlFilters, replace]);

  // URL -> text box, only when the URL changed from outside (back button, clear filters).
  useEffect(() => {
    if (urlFilters.brand !== lastPushedBrand.current) {
      lastPushedBrand.current = urlFilters.brand;
      setBrandInput(urlFilters.brand);
    }
  }, [urlFilters.brand]);

  const setStatus = useCallback(
    (status: AuditStatus | null) => replace({ ...urlFilters, brand: lastPushedBrand.current, status }),
    [replace, urlFilters],
  );

  const clear = useCallback(() => {
    lastPushedBrand.current = "";
    setBrandInput("");
    replace(NO_FILTERS);
  }, [replace]);

  return { filters: urlFilters, brandInput, setBrandInput, setStatus, clear };
}
