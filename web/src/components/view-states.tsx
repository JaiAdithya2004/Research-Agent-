import { Inbox, RefreshCw, SearchX } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";

export function ErrorState({
  title = "Something went wrong",
  message,
  onRetry,
  retrying = false,
}: {
  title?: string;
  message: string;
  onRetry?: () => void;
  retrying?: boolean;
}) {
  return (
    <Alert tone="danger" title={title} role="alert">
      <p>{message}</p>
      {onRetry ? (
        <Button variant="outline" size="sm" className="mt-3" onClick={onRetry} disabled={retrying}>
          <RefreshCw aria-hidden="true" />
          {retrying ? "Retrying…" : "Retry"}
        </Button>
      ) : null}
    </Alert>
  );
}

export function EmptyState({
  title,
  description,
  action,
  variant = "empty",
}: {
  title: string;
  description: string;
  action?: React.ReactNode;
  variant?: "empty" | "no-match";
}) {
  const Icon = variant === "no-match" ? SearchX : Inbox;
  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed px-6 py-12 text-center">
      <Icon aria-hidden="true" className="size-8 text-muted-foreground" />
      <h2 className="text-base font-semibold">{title}</h2>
      <p className="max-w-md text-sm text-muted-foreground">{description}</p>
      {action}
    </div>
  );
}
