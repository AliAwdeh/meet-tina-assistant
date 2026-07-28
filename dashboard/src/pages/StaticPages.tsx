export function Emails() {
  return <OperationalPage title="Emails" items={["Draft review", "Approval queue", "n8n delivery status", "Retry failed emails"]} />;
}

export function Conversations() {
  return <OperationalPage title="Conversations" items={["WhatsApp threads", "Voice transcripts", "Image analysis", "Agent tool executions"]} />;
}

export function Settings() {
  return <OperationalPage title="Settings" items={["Default timezone", "Email approval policy", "Model names", "Retention preferences"]} />;
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
