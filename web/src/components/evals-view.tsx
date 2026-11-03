"use client";

import { Info } from "lucide-react";
import { CostWarnings, EvalTable } from "@/components/eval-table";
import { InjectionPanel } from "@/components/injection-panel";
import { PageHeader } from "@/components/page-header";
import { TrendChart } from "@/components/trend-chart";
import { Alert } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/view-states";
import { SelectField } from "@/components/form-fields";
import { useEvalsPage } from "@/hooks/use-evals";
import { COST_INCREASE_LIMIT } from "@/lib/evals";

export function EvalsSkeleton() {
  return (
    <div role="status" aria-label="Loading eval runs" className="space-y-4">
      <span className="sr-only">Loading eval runs</span>
      <Skeleton className="h-24" />
      <Skeleton className="h-64" />
    </div>
  );
}

export function EvalsView() {
  const e = useEvalsPage();
  const header = <PageHeader title="Evals" description="Extractor prompt versions compared on the labelled eval sets." />;

  if (e.query.isPending) {
    return (
      <>
        {header}
        <EvalsSkeleton />
      </>
    );
  }
  if (e.query.isError) {
    return (
      <>
        {header}
        <ErrorState title="Could not load the eval runs" message={e.query.error.message} onRetry={() => void e.query.refetch()} retrying={e.query.isRefetching} />
      </>
    );
  }
  if (e.isEmpty || !e.view) {
    return (
      <>
        {header}
        <EmptyState
          title="No eval runs stored yet"
          description="Run the eval suite with --save to store a result, for example: uv run python -m app.evals run --prompt-version v1 --repeats 3 --save. Run it again for another prompt version to compare them."
        />
      </>
    );
  }

  const v = e.view;
  return (
    <>
      {header}

      {v.capped ? (
        <div className="mb-4">
          <Alert tone="info" title="Showing the newest runs only">
            <p>The API returned its limit of {v.totalRuns} stored runs, so older runs are not included in the table or the trends.</p>
          </Alert>
        </div>
      ) : null}

      <InjectionPanel statuses={v.injection} />

      {!v.comparison.canCompare ? (
        <div className="mb-4">
          <Alert tone="info" title="Only one prompt version so far">
            <p>There is nothing to compare yet, so no version is marked best. Store a run for a second prompt version.</p>
          </Alert>
        </div>
      ) : null}

      {v.warnings.length > 0 ? (
        <div className="mb-4">
          <Alert tone="warning" title="These runs may not be like for like">
            <ul className="list-disc space-y-1 pl-5">
              {v.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          </Alert>
        </div>
      ) : null}

      {v.incomplete.length > 0 ? (
        <div className="mb-4">
          <Alert tone="warning" title="Some metrics are missing">
            <p>
              {v.incomplete.join(", ")} {v.incomplete.length === 1 ? "has" : "have"} no value for some metrics, shown as a
              dash. Those cells are not counted when picking the best version.
            </p>
          </Alert>
        </div>
      ) : null}

      {v.notes.map((n) => (
        <div key={n.version} className="mb-4">
          <Alert tone="info" title={`Notes from the ${n.version} run`}>
            <ul className="list-disc space-y-1 pl-5">
              {n.notes.map((note, i) => (
                <li key={i} className="break-words">{note}</li>
              ))}
            </ul>
          </Alert>
        </div>
      ))}

      <section aria-labelledby="compare-heading" className="mb-8">
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 id="compare-heading" className="text-lg font-semibold">Comparison</h2>
            <p className="text-sm text-muted-foreground">
              Latest stored run of each prompt version. Mean across repeats, with the spread. The best version per metric
              is marked. The cost limit is {Math.round(COST_INCREASE_LIMIT * 100)}% over the baseline (SPEC criterion 22).
            </p>
          </div>
          {v.comparison.canCompare ? (
            <div className="w-48">
              <SelectField
                id="baseline"
                label="Baseline version"
                value={e.baseline}
                options={v.comparison.versions.map((x) => ({ value: x, label: x }))}
                onChange={e.setBaseline}
              />
            </div>
          ) : null}
        </div>
        <CostWarnings comparison={v.comparison} />
        <EvalTable latest={v.latest} comparison={v.comparison} />
        <p className="mt-2 flex items-start gap-1 text-xs text-muted-foreground">
          <Info aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
          Differences between versions can be smaller than the run-to-run spread. Check the range before trusting a
          small gap.
        </p>
      </section>

      <section aria-labelledby="trends-heading">
        <h2 id="trends-heading" className="mb-1 text-lg font-semibold">Trends</h2>
        <p className="mb-3 flex items-start gap-1 text-sm text-muted-foreground">
          <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          One point per stored run; the bar is the min to max across that run&apos;s repeats.
        </p>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {v.trends.map((t) => (
            <TrendChart key={t.metric.key} metric={t.metric} series={t.series} />
          ))}
        </div>
      </section>
    </>
  );
}
