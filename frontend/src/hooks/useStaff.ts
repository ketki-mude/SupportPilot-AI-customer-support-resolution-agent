import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ticketKeys } from "@/hooks/useTickets";
import { api } from "@/lib/api";
import type { StaffActionInput } from "@/lib/types";

/** What the rules say about an action, refreshed whenever the action or the items change. */
export function useActionCheck(ticketId: string, input: StaffActionInput, enabled: boolean) {
  return useQuery({
    queryKey: ["staff-check", ticketId, input.action, input.order_id ?? null, input.skus],
    queryFn: () => api.checkStaffAction(ticketId, input),
    enabled,
    retry: false,
    placeholderData: (previous) => previous,
  });
}

function useRefreshTicket(ticketId: string) {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ticketKeys.detail(ticketId) });
    void queryClient.invalidateQueries({ queryKey: ticketKeys.all });
    void queryClient.invalidateQueries({ queryKey: ["staff-check", ticketId] });
  };
}

export function useStaffReply(ticketId: string) {
  const refresh = useRefreshTicket(ticketId);
  return useMutation({
    mutationFn: (message: string) => api.staffReply(ticketId, message),
    onSettled: refresh,
  });
}

export function useStaffAction(ticketId: string) {
  const refresh = useRefreshTicket(ticketId);
  return useMutation({
    mutationFn: (input: StaffActionInput & { note: string | null }) =>
      api.takeStaffAction(ticketId, input),
    onSettled: refresh,
  });
}

export function useCloseTicket(ticketId: string) {
  const refresh = useRefreshTicket(ticketId);
  return useMutation({
    mutationFn: ({ outcome, note }: { outcome: "resolved" | "declined"; note: string | null }) =>
      api.closeTicket(ticketId, outcome, note),
    onSettled: refresh,
  });
}
