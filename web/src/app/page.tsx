import { Suspense } from "react";
import { AuditsView } from "@/components/audits-view";
import { AuditsTableSkeleton } from "@/components/audits-table";

export default function AuditsPage() {
  return (
    // useSearchParams (the URL filters) needs a Suspense boundary for static rendering.
    <Suspense fallback={<AuditsTableSkeleton />}>
      <AuditsView />
    </Suspense>
  );
}
