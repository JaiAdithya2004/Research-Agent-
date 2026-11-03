import type { Metadata } from "next";
import { RunViewer } from "@/components/run-viewer";

export const metadata: Metadata = { title: "Answer" };

export default async function RunPage({ params }: { params: Promise<{ id: string; runId: string }> }) {
  const { id, runId } = await params;
  return <RunViewer auditId={id} runId={runId} />;
}
