import { Navigate, Route, Routes } from "react-router-dom";

import { STAFF_ROLES, homePath, RequireRole, useAuth } from "@/lib/auth";
import AuditLog from "@/pages/AuditLog";
import Chat from "@/pages/Chat";
import Console from "@/pages/Console";
import Login from "@/pages/Login";

function Home() {
  const { status, user } = useAuth();
  if (status === "loading") return null;
  return <Navigate to={user ? homePath(user.role) : "/login"} replace />;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/chat"
        element={
          <RequireRole roles={["customer"]}>
            <Chat />
          </RequireRole>
        }
      />
      <Route
        path="/console/:ticketId?"
        element={
          <RequireRole roles={STAFF_ROLES}>
            <Console />
          </RequireRole>
        }
      />
      <Route
        path="/audit"
        element={
          <RequireRole roles={STAFF_ROLES}>
            <AuditLog />
          </RequireRole>
        }
      />
      <Route path="*" element={<Home />} />
    </Routes>
  );
}
