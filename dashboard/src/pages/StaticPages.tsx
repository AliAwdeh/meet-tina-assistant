import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, Fingerprint, MailCheck, MailX } from "lucide-react";
import { apiGet, apiPut, errorMessage, shouldRetry } from "../api/client";
import { passkeysSupported, registerPasskey } from "../auth/passkeys";
import { Button, Notice, secondaryButtonClass } from "../components/ui";
import type { NotificationSettings } from "../types/domain";

type Passkey = {
  id: string;
  device_name: string;
  created_at: string;
  last_used_at: string | null;
};

export function Emails() {
  return <OperationalPage title="Emails" items={["Draft review", "Approval queue", "n8n delivery status", "Retry failed emails"]} />;
}

export function Conversations() {
  return <OperationalPage title="Conversations" items={["WhatsApp threads", "Voice transcripts", "Image analysis", "Agent tool executions"]} />;
}

export function Settings() {
  const queryClient = useQueryClient();
  const settings = useQuery({
    queryKey: ["notification-settings"],
    queryFn: () => apiGet<NotificationSettings>("/api/dashboard/settings/notifications"),
    retry: shouldRetry
  });
  const passkeys = useQuery({
    queryKey: ["passkeys"],
    queryFn: () => apiGet<Passkey[]>("/api/auth/passkeys"),
    retry: shouldRetry
  });
  const save = useMutation({
    mutationFn: (payload: Pick<NotificationSettings, "task_change_email_notifications">) =>
      apiPut<NotificationSettings>("/api/dashboard/settings/notifications", payload),
    onSuccess: (data) => {
      queryClient.setQueryData(["notification-settings"], data);
    }
  });
  const addPasskey = useMutation({
    mutationFn: () => registerPasskey("Sami iPhone"),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["passkeys"] });
    }
  });
  const enabled = settings.data?.task_change_email_notifications ?? true;

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-xl font-semibold">Settings</h2>
        <p className="text-sm text-stone-500">Notification controls for Sami’s workspace</p>
      </div>
      {(settings.isError || save.isError || passkeys.isError || addPasskey.isError) && (
        <Notice title={addPasskey.isError || passkeys.isError ? "Face ID setup failed" : "Settings could not save"}>
          {settings.isError
            ? errorMessage(settings.error)
            : save.isError
              ? errorMessage(save.error)
              : passkeys.isError
                ? errorMessage(passkeys.error)
                : errorMessage(addPasskey.error)}
        </Notice>
      )}
      <section className="border-y border-stone-200 bg-white/80 p-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-2xl">
            <div className="flex items-center gap-2 font-semibold">
              <Bell className="text-mint" size={18} />
              Task change email notifications
            </div>
            <p className="mt-2 text-sm text-stone-600">
              Sends an email when a task action changes priority or project. Emails go only to related people with saved email addresses.
            </p>
            <p className="mt-2 text-xs font-medium text-stone-500">{settings.data?.task_change_email_recipients}</p>
          </div>
          <div className="flex gap-2">
            <Button
              className={enabled ? "" : secondaryButtonClass}
              disabled={settings.isLoading || save.isPending}
              onClick={() => save.mutate({ task_change_email_notifications: true })}
            >
              <MailCheck size={16} />
              On
            </Button>
            <Button
              className={!enabled ? "" : secondaryButtonClass}
              disabled={settings.isLoading || save.isPending}
              onClick={() => save.mutate({ task_change_email_notifications: false })}
            >
              <MailX size={16} />
              Off
            </Button>
          </div>
        </div>
      </section>
      <section className="border-y border-stone-200 bg-white/80 p-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-2xl">
            <div className="flex items-center gap-2 font-semibold">
              <Fingerprint className="text-mint" size={18} />
              Face ID / Passkey sign in
            </div>
            <p className="mt-2 text-sm text-stone-600">
              Register this iPhone once, then sign in from the login page using Face ID instead of the password.
            </p>
            <p className="mt-2 text-xs font-medium text-stone-500">
              {passkeys.data?.length
                ? `${passkeys.data.length} ${passkeys.data.length === 1 ? "passkey" : "passkeys"} registered`
                : "No passkey registered yet"}
            </p>
          </div>
          <Button disabled={!passkeysSupported() || passkeys.isLoading || addPasskey.isPending} onClick={() => addPasskey.mutate()}>
            <Fingerprint size={16} />
            {addPasskey.isPending ? "Opening Face ID" : "Set up Face ID"}
          </Button>
        </div>
      </section>
    </div>
  );
}

function OperationalPage({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <h2 className="mb-4 text-xl font-semibold">{title}</h2>
      <div className="grid gap-3 md:grid-cols-2">
        {items.map((item) => (
          <div className="border border-stone-200 bg-white p-4" key={item}>
            <div className="font-medium">{item}</div>
            <div className="mt-1 text-sm text-stone-500">Connected to the backend service surface for this workflow.</div>
          </div>
        ))}
      </div>
    </div>
  );
}
