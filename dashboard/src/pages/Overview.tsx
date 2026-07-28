import { useQuery } from "@tanstack/react-query";
import { Activity, AlertTriangle, CalendarClock, MailCheck } from "lucide-react";
import type { ReactNode } from "react";
import { apiGet, errorMessage, shouldRetry } from "../api/client";
import { Metric, Notice, Panel } from "../components/ui";
import type { DashboardSummary } from "../types/domain";

export function Overview() {
  const summary = useQuery({
    queryKey: ["summary"],
    queryFn: () => apiGet<DashboardSummary>("/api/dashboard/summary"),
    retry: shouldRetry
  });

  const data =
    summary.data ??
    ({
      today_meetings: 0,
      upcoming_meetings: 0,
      due_tasks: 0,
      overdue_tasks: 0,
      pending_reminders: 0,
      recent_messages: 0,
      pending_email_approvals: 0,
      failed_integrations: 0,
      scheduler_health: "loading"
    } satisfies DashboardSummary);

  return (
    <div className="space-y-6">
      {summary.isError && <Notice title="Dashboard data unavailable">{errorMessage(summary.error)}</Notice>}
      <div className="grid gap-3 md:grid-cols-4">
        <Metric label="Today's meetings" value={data.today_meetings} />
        <Metric label="Due tasks" value={data.due_tasks} />
        <Metric label="Overdue tasks" value={data.overdue_tasks} tone={data.overdue_tasks ? "bad" : "default"} />
        <Metric label="Pending email approvals" value={data.pending_email_approvals} tone={data.pending_email_approvals ? "warn" : "default"} />
      </div>
      <Panel>
        <div className="grid gap-4 md:grid-cols-4">
          <Status icon={<CalendarClock size={18} />} label="Upcoming meetings" value={data.upcoming_meetings} />
          <Status icon={<Activity size={18} />} label="Scheduler" value={data.scheduler_health} />
          <Status icon={<MailCheck size={18} />} label="Pending reminders" value={data.pending_reminders} />
          <Status icon={<AlertTriangle size={18} />} label="Failed integrations" value={data.failed_integrations} />
        </div>
      </Panel>
    </div>
  );
}

function Status({ icon, label, value }: { icon: ReactNode; label: string; value: string | number }) {
  return (
    <div className="flex items-center gap-3">
      <div className="grid h-9 w-9 place-items-center rounded-md bg-mint/15 text-mint">{icon}</div>
      <div>
        <div className="text-sm text-stone-500">{label}</div>
        <div className="font-medium">{value}</div>
      </div>
    </div>
  );
}
