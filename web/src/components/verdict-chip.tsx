import { CheckCircle2, Clock, Flag, MinusCircle, UserCheck, XCircle } from "lucide-react";
import type { VerdictIcon, VerdictPresentation } from "@/lib/verdict";
import { cn } from "@/lib/utils";

const ICON: Record<VerdictIcon, typeof Flag> = {
  check: CheckCircle2,
  flag: Flag,
  "user-check": UserCheck,
  x: XCircle,
  clock: Clock,
  minus: MinusCircle,
};

const TONE = {
  info: "border-info/50 bg-info-bg text-info",
  success: "border-success/50 bg-success-bg text-success",
  warning: "border-warning/60 bg-warning-bg text-warning",
  danger: "border-destructive/50 bg-destructive-bg text-destructive",
  neutral: "border-border bg-muted text-muted-foreground",
} as const;

export function VerdictChip({ verdict, className }: { verdict: VerdictPresentation; className?: string }) {
  const Icon = ICON[verdict.icon];
  return (
    <span
      data-verdict={verdict.kind}
      className={cn("inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-sm font-semibold", TONE[verdict.tone], className)}
    >
      <Icon aria-hidden="true" className="size-4" />
      {verdict.label}
    </span>
  );
}
