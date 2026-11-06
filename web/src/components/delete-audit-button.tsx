"use client";

import { useState } from "react";
import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useDeleteAudit } from "@/hooks/use-delete-audit";

/** Delete with an explicit "are you sure" step, so one stray click never removes an audit. */
export function DeleteAuditButton({
  auditId,
  brand,
  onDeleted,
  disabled = false,
}: {
  auditId: string;
  brand: string;
  onDeleted: () => void;
  disabled?: boolean;
}) {
  const [asking, setAsking] = useState(false);
  const remove = useDeleteAudit();

  if (!asking) {
    return (
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={disabled}
        title={disabled ? "You can delete this audit once it has finished" : undefined}
        aria-label={`Delete audit for ${brand}`}
        onClick={() => setAsking(true)}
      >
        <Trash2 aria-hidden="true" />
        Delete
      </Button>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label={`Confirm deleting the audit for ${brand}`}>
      <span className="text-sm">Delete this audit? Its answers, sources and reviews are erased for good.</span>
      <Button
        type="button"
        variant="destructive"
        size="sm"
        disabled={remove.isPending}
        onClick={() => remove.mutate(auditId, { onSuccess: onDeleted })}
      >
        Yes, delete
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        disabled={remove.isPending}
        onClick={() => {
          remove.reset();
          setAsking(false);
        }}
      >
        Cancel
      </Button>
      {remove.isError ? (
        <p role="alert" className="w-full text-sm text-destructive">
          {remove.error.message}
        </p>
      ) : null}
    </div>
  );
}
