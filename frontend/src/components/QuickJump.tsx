import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Command, CommandEmpty, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { api } from "@/lib/api";
import { STATUS_LABEL, ticketTitle } from "@/lib/format";

interface QuickJumpProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** Cmd+K (Ctrl+K): jump to any ticket by number, order, customer, or summary. */
export function QuickJump({ open, onOpenChange }: QuickJumpProps) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(query.trim()), 200);
    return () => clearTimeout(timer);
  }, [query]);

  const results = useQuery({
    queryKey: ["tickets", "quick-jump", debounced],
    queryFn: () => api.listTickets({ q: debounced || undefined, page_size: 8 }),
    enabled: open,
  });

  const go = (ticketId: string) => {
    onOpenChange(false);
    setQuery("");
    navigate(`/console/${ticketId}`);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent aria-describedby={undefined}>
        <DialogTitle className="sr-only">Jump to a ticket</DialogTitle>
        <Command shouldFilter={false} label="Jump to a ticket">
          <CommandInput value={query} onValueChange={setQuery} placeholder="Ticket number, order, or customer" />
          <CommandList>
            <CommandEmpty>{results.isFetching ? "Searching…" : "No tickets match."}</CommandEmpty>
            {results.data?.items.map((ticket) => (
              <CommandItem key={ticket.id} value={ticket.id} onSelect={() => go(ticket.id)}>
                <span className="font-medium">
                  {ticket.public_id} · {ticketTitle(ticket.summary, ticket.category)}
                </span>
                <span className="text-xs text-subtle">
                  {ticket.customer_name ?? ticket.customer_id} · {STATUS_LABEL[ticket.status]}
                </span>
              </CommandItem>
            ))}
          </CommandList>
        </Command>
      </DialogContent>
    </Dialog>
  );
}

/** Open the quick-jump palette with Cmd+K or Ctrl+K. */
export function useQuickJumpShortcut(onOpen: () => void): void {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        onOpen();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onOpen]);
}
