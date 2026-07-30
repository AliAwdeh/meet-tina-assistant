import {
  Bell,
  CalendarDays,
  CheckSquare,
  FolderKanban,
  LayoutDashboard,
  Mail,
  MessageCircle,
  Settings as SettingsIcon,
  Users
} from "lucide-react";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiPost, shouldRetry } from "../api/client";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { Button, LoadingPanel } from "../components/ui";
import { Login } from "../pages/Login";
import { Overview } from "../pages/Overview";
import { Records } from "../pages/Records";
import { Conversations, Emails, Settings } from "../pages/StaticPages";
import { TasksBoard } from "../pages/TasksBoard";
import type { User } from "../types/domain";

const nav = [
  { id: "Overview", icon: LayoutDashboard },
  { id: "People", icon: Users },
  { id: "Projects", icon: FolderKanban },
  { id: "Tasks", icon: CheckSquare },
  { id: "Meetings", icon: CalendarDays },
  { id: "Reminders", icon: Bell },
  { id: "Emails", icon: Mail },
  { id: "Conversations", icon: MessageCircle },
  { id: "Settings", icon: SettingsIcon }
] as const;

type Page = (typeof nav)[number]["id"];

export function App() {
  const [page, setPage] = useState<Page>("Overview");
  const queryClient = useQueryClient();
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => apiGet<User>("/api/auth/me"),
    retry: shouldRetry
  });
  const logout = useMutation({
    mutationFn: () => apiPost<{ status: string }>("/api/auth/logout", {}),
    onSettled: () => {
      queryClient.clear();
      void queryClient.invalidateQueries({ queryKey: ["me"] });
    }
  });

  if (me.isLoading) {
    return (
      <div className="grid min-h-screen place-items-center bg-[#f7f4ee] px-5">
        <div className="w-full max-w-sm">
          <LoadingPanel label="Checking session" />
        </div>
      </div>
    );
  }

  if (!me.data) {
    return <Login onAuthenticated={(user) => queryClient.setQueryData(["me"], user)} />;
  }

  return (
    <div className="min-h-screen bg-[#f7f4ee] text-ink">
      <header className="border-b border-stone-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-5 py-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-normal">Meet Tina</h1>
            <p className="text-sm text-stone-500">{me.data.name}</p>
          </div>
          <div className="flex items-center gap-2">
            <Button onClick={() => setPage("Tasks")}>New task</Button>
            <Button className="bg-white text-ink hover:bg-stone-100" disabled={logout.isPending} onClick={() => logout.mutate()}>
              Sign out
            </Button>
          </div>
        </div>
      </header>
      <div className="mx-auto grid max-w-7xl gap-6 px-5 py-6 lg:grid-cols-[220px_1fr]">
        <nav className="flex gap-2 overflow-x-auto lg:block lg:space-y-1">
          {nav.map((item) => {
            const Icon = item.icon;
            return (
              <button
                className={`flex h-10 min-w-fit items-center gap-2 rounded-md px-3 text-sm transition ${
                  page === item.id ? "bg-ink text-white" : "text-stone-600 hover:bg-white"
                }`}
                key={item.id}
                onClick={() => setPage(item.id)}
                title={item.id}
              >
                <Icon size={17} />
                <span>{item.id}</span>
              </button>
            );
          })}
        </nav>
        <main>
          <ErrorBoundary key={page}>{renderPage(page)}</ErrorBoundary>
        </main>
      </div>
    </div>
  );
}

function renderPage(page: Page) {
  if (page === "Overview") return <Overview />;
  if (page === "Tasks") return <TasksBoard />;
  if (page === "People" || page === "Projects" || page === "Meetings" || page === "Reminders") return <Records title={page} />;
  if (page === "Emails") return <Emails />;
  if (page === "Conversations") return <Conversations />;
  return <Settings />;
}
