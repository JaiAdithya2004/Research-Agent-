import Link from "next/link";
import { Alert } from "@/components/ui/alert";
import { buttonVariants } from "@/components/ui/button";
import type { Banner } from "@/lib/audit-banners";
import { cn } from "@/lib/utils";

/** Every condition gets its own banner: what happened, and what it means for the numbers. */
export function AuditBanners({ banners }: { banners: readonly Banner[] }) {
  if (banners.length === 0) return null;
  return (
    <section aria-label="Notices about these results" className="mb-6 flex flex-col gap-3">
      {banners.map((b) => (
        <Alert key={b.id} tone={b.tone} title={b.title} data-testid={`banner-${b.id}`}>
          <p>{b.body}</p>
          <p>
            <strong>What it means for the numbers:</strong> {b.meaning}
          </p>
          {b.action ? (
            <Link href={b.action.href} className={cn(buttonVariants({ variant: "outline", size: "sm" }), "mt-2")}>
              {b.action.label}
            </Link>
          ) : null}
        </Alert>
      ))}
    </section>
  );
}
