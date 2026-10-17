import { LogOut, Search } from "lucide-react";
import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";

import { AcmeMark } from "@/components/AcmeMark";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

interface StaffShellProps {
  children: ReactNode;
  onOpenSearch?: () => void;
}

/** Top bar for staff pages: logo, quick-jump search, navigation, and sign out. */
export function StaffShell({ children, onOpenSearch }: StaffShellProps) {
  const { user, logout } = useAuth();
  const link = ({ isActive }: { isActive: boolean }) =>
    cn("whitespace-nowrap rounded-control px-2 py-1.5 text-sm sm:px-3", isActive ? "bg-muted text-foreground" : "text-subtle hover:text-foreground");
  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex h-14 shrink-0 items-center gap-3 border-b-hairline border-border bg-surface px-4">
        <AcmeMark />
        <span className="hidden font-semibold sm:inline">SupportPilot</span>
        <nav className="flex items-center gap-0.5 sm:ml-2 sm:gap-1" aria-label="Main">
          <NavLink to="/console" className={link}>Console</NavLink>
          <NavLink to="/audit" className={link}>Audit log</NavLink>
          {user?.role === "admin" ? (
            <NavLink to="/evals" className={link}>Evaluations</NavLink>
          ) : null}
        </nav>
        {onOpenSearch ? (
          <button
            type="button"
            onClick={onOpenSearch}
            className="ml-auto flex h-9 w-full max-w-xs items-center gap-2 rounded-control border-hairline border-border bg-background px-3 text-subtle"
          >
            <Search className="h-4 w-4" aria-hidden />
            <span className="truncate">Search tickets</span>
            <kbd className="ml-auto hidden rounded border-hairline border-border px-1.5 text-xs sm:inline">⌘K</kbd>
          </button>
        ) : (
          <span className="ml-auto" />
        )}
        <span className="hidden text-xs text-subtle md:inline">{user?.email}</span>
        <Button variant="ghost" size="icon" aria-label="Sign out" onClick={logout}>
          <LogOut className="h-4 w-4" aria-hidden />
        </Button>
      </header>
      <div className="min-h-0 flex-1">{children}</div>
    </div>
  );
}
