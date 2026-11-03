import type { Metadata } from "next";
import { PageHeader } from "@/components/page-header";

export const metadata: Metadata = { title: "Evals" };

export default function EvalsPage() {
  return (
    <>
      <PageHeader title="Evals" description="Extractor versions compared on the labelled eval sets." />
      <p className="text-sm text-muted-foreground">This page is not built yet.</p>
    </>
  );
}
