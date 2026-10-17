import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ticketKeys } from "@/hooks/useTickets";
import { api } from "@/lib/api";
import type { Approval, TicketDetail } from "@/lib/types";

export const approvalKeys = {
  list: (status: Approval["status"]) => ["approvals", status] as const,
};

export function useApprovals(status: Approval["status"] = "pending") {
  return useQuery({
    queryKey: approvalKeys.list(status),
    queryFn: () => api.listApprovals(status),
    refetchInterval: 10_000,
  });
}

export interface DecisionInput {
  approvalId: string;
  decision: "approve" | "reject";
  note?: string;
}

/**
 * Record a supervisor decision. The approval flips immediately in the UI, and rolls back if
 * the API refuses (for example when someone else already decided).
 */
export function useDecision(ticketId: string) {
  const queryClient = useQueryClient();
  const key = ticketKeys.detail(ticketId);
  return useMutation({
    mutationFn: ({ approvalId, decision, note }: DecisionInput) =>
      api.decide(approvalId, decision, note),
    onMutate: async ({ approvalId, decision }: DecisionInput) => {
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<TicketDetail>(key);
      if (previous) {
        queryClient.setQueryData<TicketDetail>(key, {
          ...previous,
          approvals: previous.approvals.map((approval) =>
            approval.id === approvalId
              ? { ...approval, status: decision === "approve" ? "approved" : "rejected" }
              : approval,
          ),
        });
      }
      return { previous };
    },
    onError: (_error, _input, context) => {
      if (context?.previous) queryClient.setQueryData(key, context.previous);
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: key });
      void queryClient.invalidateQueries({ queryKey: ticketKeys.all });
      void queryClient.invalidateQueries({ queryKey: ["approvals"] });
    },
  });
}
