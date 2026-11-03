import type { Metadata } from "next";
import Link from "next/link";
import { PageHeader } from "@/components/page-header";

export const metadata: Metadata = { title: "Answer" };

// Placeholder: the answer viewer (raw answer, highlighted quote, citations) is built separately.
export default async function RunPage({ params }: { params: Promise<{ id: string; runId: string }> }) {
  const { id } = await params;
  return (
    <>
      <PageHeader title="Answer" description="The answer viewer is not built yet." />
      <Link href={`/audits/${id}`} className="text-sm underline underline-offset-4">
        Back to the audit
      </Link>
    </>
  );
}
