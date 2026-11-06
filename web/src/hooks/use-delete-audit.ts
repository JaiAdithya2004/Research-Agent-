"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { getApi } from "@/lib/api/client";
import { ApiError, messageFromBody } from "@/lib/api/errors";
import { auditKey } from "@/hooks/use-audit";

/** Delete an audit. The server hides it everywhere; the list is refreshed on success. */
export function useDeleteAudit() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (auditId: string): Promise<void> => {
      const { response, error } = await getApi().DELETE("/audits/{audit_id}", {
        params: { path: { audit_id: auditId } },
      });
      if (!response.ok) {
        throw new ApiError(response.status, messageFromBody(error, "Could not delete this audit. Try again."));
      }
    },
    onSuccess: (_data, auditId) => {
      queryClient.removeQueries({ queryKey: auditKey(auditId) });
      void queryClient.invalidateQueries({ queryKey: ["audits", "list"] });
    },
  });
}
