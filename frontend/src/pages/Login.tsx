import { type FormEvent, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { AcmeMark } from "@/components/AcmeMark";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api";
import { homePath, useAuth } from "@/lib/auth";

export default function Login() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to={homePath(user.role)} replace />;

  const from = (location.state as { from?: string } | null)?.from;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const signedIn = await login(email.trim(), password);
      const target = from && from !== "/login" ? from : homePath(signedIn.role);
      navigate(target, { replace: true });
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.status === 401
          ? "That email and password don't match an account."
          : caught instanceof ApiError
            ? caught.message
            : "Sign in failed. Please try again.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="grid min-h-full place-items-center px-4 py-10">
      <Card className="w-full max-w-sm">
        <CardContent className="flex flex-col gap-6 p-6">
          <div className="flex items-center gap-3">
            <AcmeMark className="h-9 w-9" />
            <div>
              <h1 className="text-lg font-semibold">Sign in to Acme support</h1>
              <p className="text-subtle">Customers and support staff</p>
            </div>
          </div>
          <form className="flex flex-col gap-4" onSubmit={submit} noValidate>
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-medium text-subtle">Email</span>
              <Input
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-medium text-subtle">Password</span>
              <Input
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </label>
            {error ? (
              <p role="alert" className="text-danger">
                {error}
              </p>
            ) : null}
            <Button type="submit" variant="primary" disabled={busy || !email || !password}>
              {busy ? "Signing in…" : "Sign in"}
            </Button>
          </form>
          <p className="text-xs text-subtle">
            Demo accounts: customer@demo.com, agent@demo.com, supervisor@demo.com. Passwords are
            in your .env file.
          </p>
        </CardContent>
      </Card>
    </main>
  );
}
