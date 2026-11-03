"use client";

import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReviewTray } from "@/components/review-tray";
import { ReviewOutboxProvider } from "@/hooks/use-review-outbox";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { staleTime: 15_000, refetchOnWindowFocus: false, retry: 1 } },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <ReviewOutboxProvider>
        {children}
        <ReviewTray />
      </ReviewOutboxProvider>
    </QueryClientProvider>
  );
}
