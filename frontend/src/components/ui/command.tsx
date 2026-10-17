import { Command as CommandPrimitive } from "cmdk";
import { Search } from "lucide-react";
import type { ComponentPropsWithoutRef } from "react";

import { cn } from "@/lib/utils";

export function Command({ className, ...props }: ComponentPropsWithoutRef<typeof CommandPrimitive>) {
  return <CommandPrimitive className={cn("flex flex-col text-foreground", className)} {...props} />;
}

export function CommandInput({
  className,
  ...props
}: ComponentPropsWithoutRef<typeof CommandPrimitive.Input>) {
  return (
    <div className="flex items-center gap-2 border-b-hairline border-border px-3">
      <Search className="h-4 w-4 text-subtle" aria-hidden />
      <CommandPrimitive.Input
        className={cn(
          "h-11 w-full bg-transparent text-sm outline-none placeholder:text-subtle focus-visible:ring-0 focus-visible:ring-offset-0",
          className,
        )}
        {...props}
      />
    </div>
  );
}

export function CommandList({
  className,
  ...props
}: ComponentPropsWithoutRef<typeof CommandPrimitive.List>) {
  return <CommandPrimitive.List className={cn("max-h-80 overflow-y-auto p-1", className)} {...props} />;
}

export function CommandEmpty(props: ComponentPropsWithoutRef<typeof CommandPrimitive.Empty>) {
  return <CommandPrimitive.Empty className="px-3 py-6 text-center text-subtle" {...props} />;
}

export function CommandItem({
  className,
  ...props
}: ComponentPropsWithoutRef<typeof CommandPrimitive.Item>) {
  return (
    <CommandPrimitive.Item
      className={cn(
        "flex cursor-pointer flex-col gap-0.5 rounded-control px-3 py-2 data-[selected=true]:bg-muted",
        className,
      )}
      {...props}
    />
  );
}
