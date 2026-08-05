import { useMutation } from "@tanstack/react-query";
import { Fingerprint, LockKeyhole } from "lucide-react";
import { FormEvent, useState } from "react";
import { apiPost, errorMessage } from "../api/client";
import { passkeysSupported, signInWithPasskey } from "../auth/passkeys";
import { Button, Notice } from "../components/ui";
import type { User } from "../types/domain";

type LoginResponse = {
  access_token: string;
  token_type: string;
  user: User;
};

export function Login({ onAuthenticated }: { onAuthenticated: (user: User) => void }) {
  const [email, setEmail] = useState("admin@example.com");
  const [password, setPassword] = useState("");
  const login = useMutation({
    mutationFn: () => apiPost<LoginResponse>("/api/auth/login", { email, password }),
    onSuccess: (data) => onAuthenticated(data.user)
  });
  const passkeyLogin = useMutation({
    mutationFn: () => signInWithPasskey(email),
    onSuccess: (data) => onAuthenticated(data.user)
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    login.mutate();
  }

  return (
    <div className="grid min-h-screen place-items-center bg-[#f7f4ee] px-5 text-ink">
      <main className="w-full max-w-sm border border-stone-200 bg-white p-6 shadow-sm">
        <div className="mb-6 flex items-center gap-3">
          <div className="grid h-10 w-10 place-items-center rounded-md bg-mint/15 text-mint">
            <LockKeyhole size={19} />
          </div>
          <div>
            <h1 className="text-xl font-semibold">Sami&apos;s Team</h1>
            <p className="text-sm text-stone-500">Dashboard sign in</p>
          </div>
        </div>
        {(login.isError || passkeyLogin.isError) && (
          <div className="mb-4">
            <Notice title="Sign in failed">{login.isError ? errorMessage(login.error) : errorMessage(passkeyLogin.error)}</Notice>
          </div>
        )}
        <form className="space-y-4" onSubmit={submit}>
          <label className="block text-sm font-medium">
            Email
            <input
              autoComplete="email"
              className="mt-1 h-10 w-full border border-stone-300 bg-white px-3 text-sm outline-none transition focus:border-ink"
              onChange={(event) => setEmail(event.target.value)}
              type="email"
              value={email}
            />
          </label>
          <label className="block text-sm font-medium">
            Password
            <input
              autoComplete="current-password"
              className="mt-1 h-10 w-full border border-stone-300 bg-white px-3 text-sm outline-none transition focus:border-ink"
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              value={password}
            />
          </label>
          <Button className="w-full" disabled={login.isPending} type="submit">
            {login.isPending ? "Signing in" : "Sign in"}
          </Button>
        </form>
        <div className="mt-4 border-t border-stone-100 pt-4">
          <Button
            className="w-full"
            disabled={!passkeysSupported() || passkeyLogin.isPending || !email}
            onClick={() => passkeyLogin.mutate()}
            type="button"
          >
            <Fingerprint size={16} />
            {passkeyLogin.isPending ? "Opening Face ID" : "Sign in with Face ID / Passkey"}
          </Button>
          {!passkeysSupported() && <p className="mt-2 text-xs text-stone-500">Passkeys are not available in this browser.</p>}
        </div>
      </main>
    </div>
  );
}
