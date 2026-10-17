import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type TicketQuery } from "@/lib/api";
import type { ChatResponse, Message, TicketDetail } from "@/lib/types";

export const ticketKeys = {
  all: ["tickets"] as const,
  list: (query: TicketQuery) => ["tickets", "list", query] as const,
  detail: (ticketId: string) => ["tickets", "detail", ticketId] as const,
};

export function useTicketList(query: TicketQuery, options: { refetchInterval?: number } = {}) {
  return useQuery({
    queryKey: ticketKeys.list(query),
    queryFn: () => api.listTickets(query),
    refetchInterval: options.refetchInterval,
    placeholderData: (previous) => previous,
  });
}

export function useTicket(ticketId: string | null) {
  return useQuery({
    queryKey: ticketKeys.detail(ticketId ?? "none"),
    queryFn: () => api.getTicket(ticketId as string),
    enabled: Boolean(ticketId),
  });
}

export function useCreateTicket() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (message: string) => api.createTicket(message),
    onSuccess: (response: ChatResponse) => {
      queryClient.setQueryData<TicketDetail>(ticketKeys.detail(response.ticket.id), {
        ticket: response.ticket,
        messages: response.messages,
        approvals: [],
        trace: [],
        customer_context: null,
        customer_context_error: null,
        usage: null,
      });
      void queryClient.invalidateQueries({ queryKey: ticketKeys.all });
    },
  });
}

/** Show the customer's message immediately; the server's copy replaces it on success. */
export function useSendMessage(ticketId: string) {
  const queryClient = useQueryClient();
  const key = ticketKeys.detail(ticketId);
  return useMutation({
    mutationFn: (message: string) => api.sendMessage(ticketId, message),
    onMutate: async (message: string) => {
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<TicketDetail>(key);
      if (previous) {
        const pending: Message = {
          id: `pending-${Date.now()}`,
          ticket_id: ticketId,
          sender: "customer",
          content: message,
          created_at: new Date().toISOString(),
        };
        queryClient.setQueryData<TicketDetail>(key, {
          ...previous,
          messages: [...previous.messages, pending],
        });
      }
      return { previous };
    },
    onError: (_error, _message, context) => {
      if (context?.previous) queryClient.setQueryData(key, context.previous);
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: key }),
  });
}
