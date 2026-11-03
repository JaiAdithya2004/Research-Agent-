import type { Metadata } from "next";
import { Suspense } from "react";
import { ReviewPageClient } from "@/components/review-page-client";
import { ReviewQueueSkeleton } from "@/components/review-queue-view";

export const metadata: Metadata = { title: "Review" };

export default function ReviewPage() {
  return (
    // useSearchParams (?audit_id=) needs a Suspense boundary for static rendering.
    <Suspense fallback={<ReviewQueueSkeleton />}>
      <ReviewPageClient />
    </Suspense>
  );
}
