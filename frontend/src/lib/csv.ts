import type { AuditEntry } from "@/lib/types";

const HEADER = ["time", "ticket", "actor", "action", "tool", "result", "details"];

function cell(value: unknown): string {
  const text = value === null || value === undefined ? "" : String(value);
  // Quote everything; double embedded quotes. Prefix formulas so spreadsheets never run them.
  const safe = /^[=+\-@]/.test(text) ? `'${text}` : text;
  return `"${safe.replace(/"/g, '""')}"`;
}

export function auditToCsv(entries: AuditEntry[]): string {
  const rows = entries.map((entry) => [
    entry.created_at,
    entry.ticket_public_id ?? "",
    entry.actor_label ?? entry.actor,
    entry.action,
    entry.tool_name ?? "",
    entry.status,
    JSON.stringify(entry.output),
  ]);
  return [HEADER, ...rows].map((row) => row.map(cell).join(",")).join("\r\n") + "\r\n";
}

export function downloadText(filename: string, text: string, type = "text/csv"): void {
  const url = URL.createObjectURL(new Blob([text], { type: `${type};charset=utf-8` }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
