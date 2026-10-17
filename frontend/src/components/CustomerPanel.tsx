import { AlertTriangle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardTitle } from "@/components/ui/card";
import { money, sentence, shortDate } from "@/lib/format";
import type { CustomerContext } from "@/lib/types";

/** Who the customer is, the order in question, and their recent orders. */
export function CustomerPanel({ context, error }: { context: CustomerContext | null; error: string | null }) {
  if (!context) {
    return (
      <p className="p-4 text-subtle" role="status">
        {error ? "Customer details are unavailable right now." : "No customer details."}
      </p>
    );
  }
  const { customer, order, recent_orders } = context;
  return (
    <div className="flex flex-col gap-4 p-4">
      <div className="flex flex-col gap-1">
        <p className="text-base font-semibold">{customer.name}</p>
        <div className="flex flex-wrap items-center gap-2 text-xs text-subtle">
          <Badge tone={customer.tier === "standard" ? "neutral" : "accent"}>{sentence(customer.tier)} tier</Badge>
          <span>Customer since {shortDate(customer.member_since)}</span>
        </div>
        {customer.fraud_flag ? (
          <p className="mt-1 flex items-center gap-1.5 text-xs text-danger">
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden /> Account flagged for review
          </p>
        ) : null}
      </div>

      {order ? (
        <Card className="bg-background">
          <CardContent className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <CardTitle>Order {order.order_id}</CardTitle>
              <Badge>{sentence(order.status)}</Badge>
            </div>
            <ul className="flex flex-col gap-1">
              {order.items.map((item) => (
                <li key={item.sku} className="flex justify-between gap-2">
                  <span className="truncate">
                    {item.name}
                    {item.quantity > 1 ? ` × ${item.quantity}` : ""}
                  </span>
                  <span className="shrink-0 tabular-nums">{money(item.unit_price)}</span>
                </li>
              ))}
            </ul>
            <dl className="grid grid-cols-2 gap-x-2 gap-y-1 border-t-hairline border-border pt-2 text-xs text-subtle">
              <dt>Total</dt>
              <dd className="text-right tabular-nums text-foreground">{money(order.total)}</dd>
              <dt>Delivered</dt>
              <dd className="text-right">{order.delivered_at ? shortDate(order.delivered_at) : "Not yet"}</dd>
              <dt>Card</dt>
              <dd className="text-right">ending {order.payment_method_last4}</dd>
              <dt>Previous remedy</dt>
              <dd className="text-right">{order.remedy ? sentence(order.remedy) : "None"}</dd>
            </dl>
          </CardContent>
        </Card>
      ) : null}

      <div className="flex flex-col gap-2">
        <CardTitle>Recent orders</CardTitle>
        <ul className="flex flex-col">
          {recent_orders.map((recent) => (
            <li key={recent.order_id} className="flex justify-between gap-2 border-b-hairline border-border py-2 text-xs">
              <span className="min-w-0">
                <span className="block font-medium">{recent.order_id}</span>
                <span className="block truncate text-subtle">{recent.items.join(", ")}</span>
              </span>
              <span className="shrink-0 text-right text-subtle">
                <span className="block tabular-nums text-foreground">{money(recent.total)}</span>
                {sentence(recent.status)}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
