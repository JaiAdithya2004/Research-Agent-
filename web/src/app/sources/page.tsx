import type { Metadata } from "next";
import { PageHeader } from "@/components/page-header";

export const metadata: Metadata = { title: "Sources" };

export default function SourcesPage() {
  return (
    <>
      <PageHeader title="Sources" description="Researched pages and the source gap report." />
      <p className="text-sm text-muted-foreground">This page is not built yet.</p>
    </>
  );
}
