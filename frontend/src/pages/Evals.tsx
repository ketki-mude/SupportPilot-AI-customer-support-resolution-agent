import { AlertTriangle, Play, ShieldAlert, ShieldCheck } from "lucide-react";
import { type FormEvent, type ReactNode, useEffect, useState } from "react";

import { EvalCategoryBars } from "@/components/EvalCategoryBars";
import { StaffShell } from "@/components/StaffShell";
import { StateMessage } from "@/components/StateMessage";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { useEvalOverview, useEvalReport, useStartEval } from "@/hooks/useEvals";
import { ApiError } from "@/lib/api";
import {
  categoryName,
  categoryRows,
  estimateFor,
  exactUsd,
  seconds,
  temperatureLabel,
  truncatedPercent,
} from "@/lib/evals";
import { dateTime } from "@/lib/format";
import type { EvalJob, EvalLlm, EvalOverview, EvalReport, EvalRunSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

const SMOKE_CASES = 10;

export default function Evals() {
  const overview = useEvalOverview();
  const [selected, setSelected] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);

  const runs = overview.data?.runs ?? [];
  const current = overview.data?.current ?? null;
  const shownId = selected ?? runs[0]?.id ?? null;
  const summary = runs.find((run) => run.id === shownId) ?? null;
  const report = useEvalReport(shownId);

  // When a run finishes, show its results.
  const finishedId = current?.status === "finished" ? current.report_id : null;
  useEffect(() => {
    if (finishedId) setSelected(finishedId);
  }, [finishedId]);

  return (
    <StaffShell>
      <main className="mx-auto flex h-full max-w-6xl flex-col gap-4 overflow-y-auto p-4 sm:p-6">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold">Evaluations</h1>
            <p className="max-w-2xl text-subtle">
              The agent against scripted cases, each with its own shop data. Numbers are shown as recorded;
              percentages are truncated, never rounded up.
            </p>
          </div>
          <Button
            variant="primary"
            onClick={() => setDialogOpen(true)}
            disabled={!overview.data || current?.status === "running"}
          >
            <Play className="h-4 w-4" aria-hidden />
            Run evaluation
          </Button>
        </div>

        {current ? <JobStatus job={current} /> : null}

        {overview.isPending ? (
          <StateMessage kind="loading" title="Loading evaluations" />
        ) : overview.isError ? (
          <StateMessage kind="error" title="Evaluations didn't load" onRetry={() => void overview.refetch()} />
        ) : runs.length === 0 ? (
          <StateMessage
            kind="empty"
            title="No evaluation runs yet"
            detail="Run the offline smoke set to check the wiring, or the dataset against the live model."
          />
        ) : summary ? (
          <>
            <RunHeader summary={summary} />
            <MetricCards summary={summary} />
            <EvalCategoryBars rows={categoryRows(summary.metrics)} />
            <FailedCases report={report.data?.id === summary.id ? report.data : undefined} failed={report.isError} />
            <RunList runs={runs} shownId={summary.id} onSelect={setSelected} />
          </>
        ) : null}

        {overview.data ? (
          <RunDialog open={dialogOpen} onOpenChange={setDialogOpen} overview={overview.data} />
        ) : null}
      </main>
    </StaffShell>
  );
}

function modelLabel(run: Pick<EvalRunSummary, "llm_mode" | "model">): string {
  return run.llm_mode === "mock" ? "Offline stand-in" : run.model;
}

function JobStatus({ job }: { job: EvalJob }) {
  if (job.status === "failed") {
    return (
      <p role="alert" className="flex items-center gap-2 rounded-card bg-danger-soft px-4 py-3 text-danger">
        <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden />
        The last evaluation failed: {job.error}
      </p>
    );
  }
  if (job.status !== "running") return null;
  const share = job.cases_planned ? (job.cases_done / job.cases_planned) * 100 : 0;
  return (
    <Card className="flex flex-col gap-2 px-4 py-3" role="status">
      <p>
        Running {job.llm === "mock" ? "with the offline stand-in" : "against the live model"}:{" "}
        <span className="tabular-nums">
          {job.cases_done} of {job.cases_planned}
        </span>{" "}
        cases scored
      </p>
      <div
        role="progressbar"
        aria-label="Cases scored"
        aria-valuemin={0}
        aria-valuemax={job.cases_planned}
        aria-valuenow={job.cases_done}
        className="h-2 rounded-r-[4px] bg-accent-soft"
      >
        <div className="h-full rounded-r-[4px] bg-accent transition-[width]" style={{ width: `${share}%` }} />
      </div>
    </Card>
  );
}

function RunHeader({ summary }: { summary: EvalRunSummary }) {
  const m = summary.metrics;
  const facts = [
    `${modelLabel(summary)}, ${temperatureLabel(summary)}`,
    `${dateTime(summary.started_at)}`,
    `commit ${summary.git_commit}`,
    `dataset ${summary.dataset.sha256.slice(0, 8)}`,
    `${summary.cases_scored} of ${summary.cases_planned} cases`,
    `${exactUsd(m.total_cost_usd)} actual cost`,
    `tool calls ${truncatedPercent(m.tool_successes, m.tool_calls)} successful`,
  ];
  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs text-subtle">{facts.join(" · ")}</p>
      {!summary.complete ? (
        <p className="flex items-start gap-2 rounded-card bg-warning-soft px-4 py-3 text-warning">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <span>
            Incomplete run. {summary.stopped_reason ?? "Some planned cases were not scored."} Only scored cases are
            counted below.
          </span>
        </p>
      ) : null}
    </div>
  );
}

function MetricCard({ label, value, detail }: { label: string; value: string; detail: ReactNode }) {
  return (
    <Card className="flex flex-col gap-1 p-4">
      <h2 className="text-xs font-medium text-subtle">{label}</h2>
      <p className="text-2xl font-semibold">{value}</p>
      <div className="text-xs text-subtle">{detail}</div>
    </Card>
  );
}

function MetricCards({ summary }: { summary: EvalRunSummary }) {
  const m = summary.metrics;
  const clean = m.unauthorized === 0;
  return (
    <section className="grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label="Headline results">
      <MetricCard
        label="Correct resolution"
        value={truncatedPercent(m.passed, m.cases)}
        detail={`${m.passed} of ${m.cases} cases`}
      />
      <MetricCard
        label="Policy compliance"
        value={truncatedPercent(m.policy_compliant, m.cases)}
        detail={`${m.policy_compliant} of ${m.cases} cases`}
      />
      <MetricCard
        label="Unauthorized actions"
        value={String(m.unauthorized)}
        detail={
          <Badge tone={clean ? "success" : "danger"}>
            {clean ? <ShieldCheck className="h-3 w-3" aria-hidden /> : <ShieldAlert className="h-3 w-3" aria-hidden />}
            {clean ? "On target (0)" : `Target is 0, out of ${m.cases} cases`}
          </Badge>
        }
      />
      <MetricCard
        label="Average latency"
        value={seconds(m.avg_latency_ms)}
        detail={`per ticket · ${exactUsd(m.avg_cost_usd)} average cost`}
      />
    </section>
  );
}

function yesNo(value: boolean): string {
  return value ? "yes" : "no";
}

function FailedCases({ report, failed }: { report: EvalReport | undefined; failed: boolean }) {
  if (failed) return <StateMessage kind="error" title="The case results didn't load" />;
  if (!report) return <StateMessage kind="loading" title="Loading case results" />;
  const rows = report.results.filter((result) => !result.passed);
  return (
    <Card>
      <div className="px-4 pt-4">
        <h2 className="font-semibold">Failed cases ({rows.length})</h2>
        <p className="text-xs text-subtle">Every case that did not resolve exactly as expected, with the reasons.</p>
      </div>
      {rows.length === 0 ? (
        <p className="p-4 text-subtle">Every scored case passed.</p>
      ) : (
        <div className="overflow-x-auto p-4">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead className="text-xs text-subtle">
              <tr className="border-b-hairline border-border">
                <th className="py-2 pr-4 font-medium">Case</th>
                <th className="py-2 pr-4 font-medium">Expected</th>
                <th className="py-2 pr-4 font-medium">Observed</th>
                <th className="py-2 font-medium">Reasons</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((result) => (
                <tr key={result.id} className="border-b-hairline border-border align-top last:border-0">
                  <td className="py-2 pr-4">
                    <div className="font-mono text-xs">{result.id}</div>
                    <div className="text-xs text-subtle">{categoryName(result.category)}</div>
                    {result.unauthorized ? (
                      <Badge tone="danger" className="mt-1">
                        <ShieldAlert className="h-3 w-3" aria-hidden />
                        Unauthorized
                      </Badge>
                    ) : null}
                  </td>
                  <td className="py-2 pr-4">
                    {result.expected_action}
                    <div className="text-xs text-subtle">approval {yesNo(result.expected_requires_approval)}</div>
                  </td>
                  <td className="py-2 pr-4">
                    {result.observed_action}
                    <div className="text-xs text-subtle">
                      approval {yesNo(result.observed_requires_approval)}
                      {result.rule ? ` · ${result.rule}` : ""}
                    </div>
                  </td>
                  <td className="py-2">
                    <ul className="list-disc pl-4">
                      {result.reasons.map((reason) => (
                        <li key={reason}>{reason}</li>
                      ))}
                    </ul>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function RunList({
  runs,
  shownId,
  onSelect,
}: {
  runs: EvalRunSummary[];
  shownId: string;
  onSelect: (id: string) => void;
}) {
  return (
    <Card>
      <h2 className="px-4 pt-4 font-semibold">All runs</h2>
      <div className="overflow-x-auto p-4">
        <table className="w-full min-w-[640px] text-left text-sm">
          <thead className="text-xs text-subtle">
            <tr className="border-b-hairline border-border">
              <th className="py-2 pr-4 font-medium">Started</th>
              <th className="py-2 pr-4 font-medium">Model</th>
              <th className="py-2 pr-4 text-right font-medium">Cases</th>
              <th className="py-2 pr-4 text-right font-medium">Correct</th>
              <th className="py-2 pr-4 text-right font-medium">Unauthorized</th>
              <th className="py-2 text-right font-medium">Cost</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr
                key={run.id}
                className={cn("border-b-hairline border-border last:border-0", run.id === shownId && "bg-muted")}
              >
                <td className="py-1.5 pr-4">
                  <button
                    type="button"
                    className="text-accent hover:underline"
                    aria-current={run.id === shownId ? "true" : undefined}
                    onClick={() => onSelect(run.id)}
                  >
                    {dateTime(run.started_at)}
                  </button>
                  {!run.complete ? <span className="ml-2 text-xs text-warning">incomplete</span> : null}
                </td>
                <td className="py-1.5 pr-4">{modelLabel(run)}</td>
                <td className="py-1.5 pr-4 text-right tabular-nums">
                  {run.cases_scored}/{run.cases_planned}
                </td>
                <td className="py-1.5 pr-4 text-right tabular-nums">
                  {truncatedPercent(run.metrics.passed, run.metrics.cases)}
                </td>
                <td className="py-1.5 pr-4 text-right tabular-nums">{run.metrics.unauthorized}</td>
                <td className="py-1.5 text-right tabular-nums">{exactUsd(run.metrics.total_cost_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function Choice({
  name,
  value,
  checked,
  onChange,
  label,
  detail,
}: {
  name: string;
  value: string;
  checked: boolean;
  onChange: () => void;
  label: string;
  detail: string;
}) {
  return (
    <label
      className={cn(
        "flex cursor-pointer items-start gap-3 rounded-control border-hairline px-3 py-2",
        checked ? "border-accent bg-accent-soft" : "border-border",
      )}
    >
      <input type="radio" name={name} value={value} checked={checked} onChange={onChange} className="mt-1" />
      <span>
        <span className="block font-medium">{label}</span>
        <span className="block text-xs text-subtle">{detail}</span>
      </span>
    </label>
  );
}

function RunDialog({
  open,
  onOpenChange,
  overview,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  overview: EvalOverview;
}) {
  const [llm, setLlm] = useState<EvalLlm>("gemini");
  const [size, setSize] = useState<"smoke" | "full">("smoke");
  const start = useStartEval();
  const cases = size === "smoke" ? Math.min(SMOKE_CASES, overview.dataset_cases) : overview.dataset_cases;
  const cost = llm === "mock" ? "no cost" : estimateFor(cases, overview.full_run_estimate_usd, overview.dataset_cases);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    start.mutate(
      { llm, limit: size === "smoke" ? SMOKE_CASES : null },
      { onSuccess: () => onOpenChange(false) },
    );
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) start.reset();
        onOpenChange(next);
      }}
    >
      <DialogContent aria-describedby={undefined}>
        <form onSubmit={submit} className="flex flex-col gap-4 p-5">
          <DialogTitle className="text-base font-semibold">Run evaluation</DialogTitle>
          <fieldset className="flex flex-col gap-2">
            <legend className="mb-2 text-xs font-medium text-subtle">Model</legend>
            <Choice
              name="llm"
              value="gemini"
              checked={llm === "gemini"}
              onChange={() => setLlm("gemini")}
              label={`Live model (${overview.model})`}
              detail="Measures the real agent. Uses the configured API key and costs money."
            />
            <Choice
              name="llm"
              value="mock"
              checked={llm === "mock"}
              onChange={() => setLlm("mock")}
              label="Offline stand-in"
              detail="Keyword rules instead of a model. Checks the wiring, not quality. Free."
            />
          </fieldset>
          <fieldset className="flex flex-col gap-2">
            <legend className="mb-2 text-xs font-medium text-subtle">Cases</legend>
            <Choice
              name="size"
              value="smoke"
              checked={size === "smoke"}
              onChange={() => setSize("smoke")}
              label={`Smoke set (${Math.min(SMOKE_CASES, overview.dataset_cases)} cases)`}
              detail="One case per major path."
            />
            <Choice
              name="size"
              value="full"
              checked={size === "full"}
              onChange={() => setSize("full")}
              label={`Full dataset (${overview.dataset_cases} cases)`}
              detail="Every category. A live run takes several minutes."
            />
          </fieldset>
          <p className="text-subtle">
            Estimated cost: <span className="font-medium text-foreground">{cost}</span>. The report records the
            actual cost.
          </p>
          {start.error ? (
            <p role="alert" className="text-danger">
              {start.error instanceof ApiError ? start.error.message : "The evaluation could not start."}
            </p>
          ) : null}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={start.isPending}>
              {start.isPending ? "Starting…" : `Start ${cases} cases`}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
