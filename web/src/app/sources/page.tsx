import type { Metadata } from "next";
import { Suspense } from "react";
import { SourcesView } from "@/components/sources-view";
import { SourcesTableSkeleton } from "@/components/sources-table";

export const metadata: Metadata = { title: "Sources" };

export default function SourcesPage() {
  return (
    // useSearchParams (the filters and the open source live in the URL) needs a Suspense boundary.
    <Suspense fallback={<SourcesTableSkeleton />}>
      <SourcesView />
    </Suspense>
  );
}
