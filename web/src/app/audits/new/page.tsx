import type { Metadata } from "next";
import { NewAuditForm } from "@/components/new-audit-form";
import { PageHeader } from "@/components/page-header";

export const metadata: Metadata = { title: "New audit" };

export default function NewAuditPage() {
  return (
    <>
      <PageHeader
        title="New audit"
        description="Ask AI assistants your buyer questions and see whether your brand appears."
      />
      <NewAuditForm />
    </>
  );
}
