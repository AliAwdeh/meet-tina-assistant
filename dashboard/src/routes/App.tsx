import {
  Bell,
  CalendarDays,
  CheckSquare,
  FolderKanban,
  LayoutDashboard,
  Mail,
  Menu,
  MessageCircle,
  Settings as SettingsIcon,
  Users,
  X
} from "lucide-react";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiPost, shouldRetry } from "../api/client";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { LoadingPanel } from "../components/ui";
import { Login } from "../pages/Login";
import { Overview } from "../pages/Overview";
import { Records } from "../pages/Records";
import { Conversations, Emails, Settings } from "../pages/StaticPages";
import { TasksBoard } from "../pages/TasksBoard";
import type { User } from "../types/domain";

const HOME: Page = "Home";

const menu = [
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

type Page = "Home" | (typeof menu)[number]["id"];

export function App() {
  const [page, setPage] = useState<Page>(HOME);
  const [menuOpen, setMenuOpen] = useState(false);
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

  const goTo = (next: Page) => {
    setPage(next);
    setMenuOpen(false);
  };

  return (
    <div className="min-h-screen bg-[#f7f4ee] text-ink">
      <header className="sticky top-0 z-30 border-b border-stone-200/70 bg-[#f7f4ee]/85 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-3 px-5 py-3.5">
          <button className="flex items-center gap-2.5 text-left" onClick={() => goTo(HOME)} type="button">
            <span className="grid h-9 w-9 place-items-center rounded-lg bg-ink text-sm font-bold text-mint">MT</span>
            <span className="text-lg font-semibold tracking-tight">Meet Tina</span>
          </button>
          <button
            aria-label="Open menu"
            className="inline-flex h-10 items-center gap-2 rounded-lg border border-stone-300/80 bg-white px-3.5 text-sm font-medium text-stone-700 shadow-sm transition hover:border-ink hover:text-ink"
            onClick={() => setMenuOpen(true)}
            type="button"
          >
            <Menu size={18} />
            <span className="hidden sm:inline">Menu</span>
          </button>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-5 py-6">
        <ErrorBoundary key={page}>{renderPage(page, me.data)}</ErrorBoundary>
      </main>

      {menuOpen && (
        <div className="fixed inset-0 z-40">
          <button
            aria-label="Close menu"
            className="absolute inset-0 bg-ink/30 backdrop-blur-sm"
            onClick={() => setMenuOpen(false)}
            type="button"
          />
          <aside className="absolute right-0 top-0 flex h-full w-80 max-w-[85vw] flex-col border-l border-stone-200 bg-[#f7f4ee] shadow-xl">
            <div className="flex items-center justify-between px-5 py-4">
              <div>
                <p className="text-sm text-stone-500">Signed in as</p>
                <p className="text-base font-semibold">{me.data.name}</p>
              </div>
              <button
                aria-label="Close menu"
                className="grid h-9 w-9 place-items-center rounded-lg border border-stone-300/80 bg-white text-stone-600 transition hover:border-ink hover:text-ink"
                onClick={() => setMenuOpen(false)}
                type="button"
              >
                <X size={18} />
              </button>
            </div>
            <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-2">
              {menu.map((item) => {
                const Icon = item.icon;
                const active = page === item.id || (page === HOME && item.id === "Tasks");
                return (
                  <button
                    className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition ${
                      active ? "bg-ink text-white" : "text-stone-700 hover:bg-white"
                    }`}
                    key={item.id}
                    onClick={() => goTo(item.id)}
                    type="button"
                  >
                    <Icon size={18} />
                    <span>{item.id}</span>
                  </button>
                );
              })}
            </nav>
            <div className="border-t border-stone-200 px-3 py-3">
              <button
                className="flex w-full items-center justify-center gap-2 rounded-lg border border-stone-300/80 bg-white px-3 py-2.5 text-sm font-medium text-stone-700 transition hover:border-coral hover:text-coral disabled:opacity-50"
                disabled={logout.isPending}
                onClick={() => logout.mutate()}
                type="button"
              >
                Sign out
              </button>
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}

function renderPage(page: Page, user: User) {
  if (page === "Home" || page === "Tasks") return <TasksBoard userName={user.name} />;
  if (page === "Overview") return <Overview />;
  if (page === "People" || page === "Projects" || page === "Meetings" || page === "Reminders") return <Records title={page} />;
  if (page === "Emails") return <Emails />;
  if (page === "Conversations") return <Conversations />;
  return <Settings />;
}
