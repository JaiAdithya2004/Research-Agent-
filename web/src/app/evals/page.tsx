import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { EvalsView } from "@/components/evals-view";
import { evalsEnabled } from "@/lib/nav";

export const metadata: Metadata = { title: "Evals" };

export default function EvalsPage() {
  if (!evalsEnabled({ NEXT_PUBLIC_SHOW_EVALS: process.env.NEXT_PUBLIC_SHOW_EVALS })) notFound();
  return <EvalsView />;
}
