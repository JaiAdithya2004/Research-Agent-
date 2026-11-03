import type { Metadata } from "next";
import { PageHeader } from "@/components/page-header";

export const metadata: Metadata = { title: "Review" };

export default function ReviewPage() {
  return (
    <>
      <PageHeader title="Review" description="Flagged mentions waiting for a human decision." />
      <p className="text-sm text-muted-foreground">This page is not built yet.</p>
    </>
  );
}
