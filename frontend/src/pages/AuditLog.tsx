import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { AuditTable } from "@/components/AuditTable";
import { StaffShell } from "@/components/StaffShell";
import { StateMessage } from "@/components/StateMessage";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";
import { auditToCsv, downloadText } from "@/lib/csv";
import type { AuditEntry } from "@/lib/types";

const PAGE_SIZE = 50;
const EXPORT_PAGE_SIZE = 200;
const EXPORT_MAX_ROWS = 5_000;

export default function AuditLog() {
  const [params, setParams] = useSearchParams();
  const applied = params.get("ticket") ?? "";
  const [draft, setDraft] = useState(applied);
  const [page, setPage] = useState(1);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const audit = useQuery({
    queryKey: ["audit", applied, page],
    queryFn: () => api.listAudit({ ticket_id: applied || undefined, page, page_size: PAGE_SIZE }),
    placeholderData: (previous) => previous,
  });

  const apply = (event: FormEvent) => {
    event.preventDefault();
    setPage(1);
    setParams(draft.trim() ? { ticket: draft.trim() } : {});
  };

  const exportCsv = async () => {
    setExporting(true);
    setExportError(null);
    try {
      const rows: AuditEntry[] = [];
      for (let next = 1; rows.length < EXPORT_MAX_ROWS; next += 1) {
        const batch = await api.listAudit({ ticket_id: applied || undefined, page: next, page_size: EXPORT_PAGE_SIZE });
        rows.push(...batch.items);
        if (rows.length >= batch.total || batch.items.length === 0) break;
      }
      downloadText(`audit-${applied || "all"}.csv`, auditToCsv(rows));
    } catch (error) {
      setExportError(error instanceof Error ? error.message : "Export failed");
    } finally {
      setExporting(false);
    }
  };

  const notFound = audit.error instanceof ApiError && audit.error.status === 404;
  const totalPages = audit.data ? Math.max(1, Math.ceil(audit.data.total / PAGE_SIZE)) : 1;

  return (
    <StaffShell>
      <main className="mx-auto flex h-full max-w-6xl flex-col gap-4 overflow-y-auto p-4 sm:p-6">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold">Audit log</h1>
            <p className="text-subtle">Every agent step, tool call, and human decision, newest first.</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <form onSubmit={apply} className="flex gap-2" role="search">
              <label className="sr-only" htmlFor="audit-ticket">
                Filter by ticket
              </label>
              <Input
                id="audit-ticket"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder="Ticket, e.g. TCK-1001"
                className="w-48"
              />
              <Button type="submit" variant="outline">
                Filter
              </Button>
              {applied ? (
                <Button
                  variant="ghost"
                  onClick={() => {
                    setDraft("");
                    setPage(1);
                    setParams({});
                  }}
                >
                  Clear
                </Button>
              ) : null}
            </form>
            <Button variant="primary" onClick={() => void exportCsv()} disabled={exporting || !audit.data?.total}>
              <Download className="h-4 w-4" aria-hidden />
              {exporting ? "Exporting…" : "Export CSV"}
            </Button>
          </div>
        </div>
        {exportError ? (
          <p role="alert" className="text-danger">
            {exportError}
          </p>
        ) : null}

        {audit.isPending ? (
          <StateMessage kind="loading" title="Loading the audit log" />
        ) : notFound ? (
          <StateMessage kind="empty" title={`No ticket matches ${applied}`} detail="Use a ticket number like TCK-1001." />
        ) : audit.isError ? (
          <StateMessage kind="error" title="The audit log didn't load" onRetry={() => void audit.refetch()} />
        ) : audit.data.items.length === 0 ? (
          <StateMessage kind="empty" title="No audit entries yet" />
        ) : (
          <>
            <AuditTable entries={audit.data.items} />
            <div className="flex items-center justify-between text-xs text-subtle">
              <span>
                {audit.data.total} entries · page {page} of {totalPages}
              </span>
              <div className="flex gap-1">
                <Button variant="ghost" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                  Previous
                </Button>
                <Button variant="ghost" size="sm" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>
                  Next
                </Button>
              </div>
            </div>
          </>
        )}
      </main>
    </StaffShell>
  );
}
