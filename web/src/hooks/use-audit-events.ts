"use client";

import { useEffect, useReducer, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  AuditEventStream,
  TERMINAL_EVENT_TYPES,
  auditEventsUrl,
  type ConnectionState,
  type EventSourceLike,
  type StreamEvent,
} from "@/lib/audit-events";
import { INITIAL_PROGRESS, reduceProgress, type ProgressState } from "@/lib/audit-progress";
import { auditKey, auditQueryOptions } from "@/hooks/use-audit";

export interface UseAuditEventsOptions {
  /** Connect only while the audit is running. */
  enabled: boolean;
  /** Event bursts are folded into one refetch per this many ms (terminal events refetch at once). */
  throttleMs?: number;
}

/**
 * Live progress for one audit. Each event invalidates the audit and run queries (no polling);
 * the connection reconnects with backoff and resumes from the last seen event id.
 */
export function useAuditEvents(auditId: string, { enabled, throttleMs = 500 }: UseAuditEventsOptions) {
  const queryClient = useQueryClient();
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [progress, onEvent] = useReducer(
    (state: ProgressState, event: StreamEvent) => reduceProgress(state, event),
    INITIAL_PROGRESS,
  );

  useEffect(() => {
    if (!enabled) return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const refetch = () => {
      if (timer !== null) clearTimeout(timer);
      timer = null;
      void queryClient.invalidateQueries({ queryKey: auditKey(auditId) });
    };
    const stream = new AuditEventStream({
      url: (lastId) => auditEventsUrl(auditId, lastId),
      createSource: (url) => new EventSource(url) as unknown as EventSourceLike,
      onEvent: (event) => {
        onEvent(event);
        if (TERMINAL_EVENT_TYPES.has(event.type) || throttleMs <= 0) refetch();
        else timer ??= setTimeout(refetch, throttleMs);
      },
      onState: setConnection,
      // After a dropped connection: has the audit finished meanwhile (the server then answers 204)?
      isFinished: async () => {
        const audit = await queryClient.fetchQuery({ ...auditQueryOptions(auditId), staleTime: 0 });
        return audit.status !== "running";
      },
    });
    stream.start();
    return () => {
      stream.stop();
      if (timer !== null) clearTimeout(timer);
    };
  }, [auditId, enabled, throttleMs, queryClient]);

  return { connection, progress };
}
