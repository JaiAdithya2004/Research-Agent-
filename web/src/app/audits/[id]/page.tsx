import type { Metadata } from "next";
import { AuditDetailView } from "@/components/audit-detail-view";

export const metadata: Metadata = { title: "Audit" };

export default async function AuditPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <AuditDetailView auditId={id} />;
}
