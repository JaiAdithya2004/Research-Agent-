import { AlertTriangle } from "lucide-react";
import { ASSUMPTIONS, type Estimate } from "@/lib/estimate";
import { formatUsd, pluralize } from "@/lib/format";

export function CostEstimate({ estimate }: { estimate: Estimate }) {
  const A = ASSUMPTIONS;
  return (
    <section aria-labelledby="estimate-heading" className="rounded-lg border bg-card p-4">
      <h2 id="estimate-heading" className="text-base font-semibold">
        Cost estimate
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">
        This is an estimate, not a quote. The audit stops at the {formatUsd(estimate.capUsd)} cap whatever the estimate says.
      </p>

      <table className="mt-3 w-full text-sm">
        <caption className="sr-only">Estimated cost breakdown</caption>
        <thead className="text-left text-muted-foreground">
          <tr>
            <th scope="col" className="py-1 font-medium">Item</th>
            <th scope="col" className="py-1 text-right font-medium">Count</th>
            <th scope="col" className="py-1 text-right font-medium">Cost</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          <Row label="Model answers" count={estimate.modelCalls.count} usd={estimate.modelCalls.usd} />
          <Row label="Extraction calls" count={estimate.extractionCalls.count} usd={estimate.extractionCalls.usd} />
          <Row label="Search queries" count={estimate.searchCalls.count} usd={estimate.searchCalls.usd} />
          <Row label="Source classification" count={estimate.classificationCalls.count} usd={estimate.classificationCalls.usd} />
          <tr>
            <th scope="row" className="py-1.5 text-left font-normal">Page fetches</th>
            <td className="py-1.5 text-right tabular-nums">{estimate.pageFetches.count}</td>
            <td className="py-1.5 text-right text-muted-foreground">free</td>
          </tr>
        </tbody>
        <tfoot>
          <tr className="border-t font-semibold">
            <th scope="row" className="pt-2 text-left">
              Estimated total ({pluralize(estimate.runs, "run")})
            </th>
            <td />
            <td className="pt-2 text-right tabular-nums" data-testid="estimate-total">
              {formatUsd(estimate.totalUsd)}
            </td>
          </tr>
        </tfoot>
      </table>

      <div aria-live="polite">
        {estimate.exceedsCap ? (
          <p className="mt-3 flex gap-2 rounded-md border border-warning/60 bg-warning-bg p-3 text-sm">
            <AlertTriangle aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warning" />
            <span>
              <strong>Over the {formatUsd(estimate.capUsd)} cap.</strong> The audit will stop at the cap and return partial
              results. Reduce the questions, repeats or search mode to finish everything.
            </span>
          </p>
        ) : null}
      </div>

      <details className="mt-3 text-sm">
        <summary className="cursor-pointer font-medium">Assumptions</summary>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-muted-foreground">
          <li>Runs = questions x repeats x {A.models} models x modes (2 modes when search mode is &quot;both&quot;).</li>
          <li>
            Each answer: {A.answerTokens.input.toLocaleString("en-US")} input and {A.answerTokens.output} output tokens at $
            {A.inputUsdPerMillion} / ${A.outputUsdPerMillion} per million. Each extraction:{" "}
            {A.extractionTokens.input.toLocaleString("en-US")} input and {A.extractionTokens.output} output tokens.
          </li>
          <li>
            Each searched run makes {A.queriesPerGroundedRun} search queries at ${A.searchUsdPerQuery} each.
          </li>
          <li>
            Cited-page research fetches up to min({A.maxPageFetches}, {A.pagesPerGroundedRun} x searched runs) pages. Fetching
            is free but counts toward the {A.maxPageFetches}-fetch cap. One classification call is assumed per fetched page
            (an upper bound).
          </li>
        </ul>
      </details>
    </section>
  );
}

function Row({ label, count, usd }: { label: string; count: number; usd: number }) {
  return (
    <tr>
      <th scope="row" className="py-1.5 text-left font-normal">{label}</th>
      <td className="py-1.5 text-right tabular-nums">{count}</td>
      <td className="py-1.5 text-right tabular-nums">{formatUsd(usd)}</td>
    </tr>
  );
}
