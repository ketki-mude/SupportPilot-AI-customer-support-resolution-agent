import { ResultBadge } from "@/components/StatusBadge";
import { dateTime, sentence, stepName } from "@/lib/format";
import type { AuditEntry } from "@/lib/types";

function describe(entry: AuditEntry): string {
  if (entry.action === "tool_call" && entry.tool_name) return `Tool: ${entry.tool_name}`;
  if (entry.action.startsWith("step:")) return `Step: ${stepName(entry.action)}`;
  const rule = typeof entry.output.rule === "string" ? ` (${entry.output.rule.replace(/_/g, " ")})` : "";
  return `${sentence(entry.action)}${rule}`;
}

export function AuditTable({ entries }: { entries: AuditEntry[] }) {
  return (
    <div className="overflow-x-auto rounded-card border-hairline border-border bg-surface">
      <table className="w-full min-w-[640px] text-left">
        <thead className="text-xs text-subtle">
          <tr className="border-b-hairline border-border">
            <th scope="col" className="px-4 py-2.5 font-medium">Time</th>
            <th scope="col" className="px-4 py-2.5 font-medium">Ticket</th>
            <th scope="col" className="px-4 py-2.5 font-medium">Actor</th>
            <th scope="col" className="px-4 py-2.5 font-medium">Action</th>
            <th scope="col" className="px-4 py-2.5 font-medium">Result</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => (
            <tr key={entry.id} className="border-b-hairline border-border last:border-0">
              <td className="whitespace-nowrap px-4 py-2.5 tabular-nums text-subtle">{dateTime(entry.created_at)}</td>
              <td className="whitespace-nowrap px-4 py-2.5">{entry.ticket_public_id ?? "—"}</td>
              <td className="px-4 py-2.5">{entry.actor_label ?? entry.actor}</td>
              <td className="px-4 py-2.5">{describe(entry)}</td>
              <td className="px-4 py-2.5">
                <ResultBadge result={entry.status} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
