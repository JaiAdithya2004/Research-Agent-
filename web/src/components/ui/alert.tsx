import { AlertTriangle, CheckCircle2, Info, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Tone } from "@/lib/audit-status";

const STYLES: Record<Tone, string> = {
  info: "border-info/50 bg-info-bg text-foreground [&_svg]:text-info",
  success: "border-success/50 bg-success-bg text-foreground [&_svg]:text-success",
  warning: "border-warning/60 bg-warning-bg text-foreground [&_svg]:text-warning",
  danger: "border-destructive/50 bg-destructive-bg text-foreground [&_svg]:text-destructive",
};

const ICONS = { info: Info, success: CheckCircle2, warning: AlertTriangle, danger: XCircle } as const;

/**
 * A banner with a title, an icon and text: meaning never depends on colour alone.
 * `role` is "alert" for errors that appear after an action, "status" for polite updates,
 * and omitted for static content.
 */
export function Alert({
  tone,
  title,
  children,
  role,
  className,
  ...rest
}: {
  tone: Tone;
  title: string;
  children?: React.ReactNode;
  role?: "alert" | "status";
  className?: string;
} & Omit<React.HTMLAttributes<HTMLDivElement>, "title" | "role">) {
  const Icon = ICONS[tone];
  return (
    <div role={role} className={cn("flex gap-3 rounded-lg border p-4 text-sm", STYLES[tone], className)} {...rest}>
      <Icon aria-hidden="true" className="mt-0.5 size-5 shrink-0" />
      <div className="min-w-0 space-y-1">
        <p className="font-semibold">{title}</p>
        {children ? <div className="text-foreground/90">{children}</div> : null}
      </div>
    </div>
  );
}
