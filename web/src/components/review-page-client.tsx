"use client";

import { useSearchParams } from "next/navigation";
import { ReviewQueueView } from "@/components/review-queue-view";

/** Reads ?audit_id= and hands it to the queue view. An empty value means every audit. */
export function ReviewPageClient() {
  const params = useSearchParams();
  const raw = params.get("audit_id")?.trim();
  return <ReviewQueueView auditId={raw ? raw : null} />;
}
