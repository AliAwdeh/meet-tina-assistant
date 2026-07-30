import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createColumnHelper, flexRender, getCoreRowModel, useReactTable } from "@tanstack/react-table";
import { Edit3, Plus, Save, X } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";
import { apiGet, apiPost, apiPut, errorMessage, shouldRetry } from "../api/client";
import { Button, LoadingPanel, Notice } from "../components/ui";
import type { Meeting, Person, Project, Reminder, Task } from "../types/domain";

type DataType = Person | Project | Task | Meeting | Reminder;

const endpoints = {
  People: "/api/dashboard/people",
  Projects: "/api/dashboard/projects",
  Tasks: "/api/dashboard/tasks",
  Meetings: "/api/dashboard/meetings",
  Reminders: "/api/dashboard/reminders"
} as const;

const columnsByPage: Record<keyof typeof endpoints, string[]> = {
  People: ["full_name", "company", "job_title", "email", "whatsapp_number"],
  Projects: ["name", "person_name", "status", "description"],
  Tasks: ["title", "project_name", "assigned_person_name", "status", "priority", "due_date"],
  Meetings: ["title", "status", "start_time", "timezone", "preparation_status"],
  Reminders: ["title", "status", "trigger_time", "timezone", "delivery_channel"]
};

export function Records({ title }: { title: keyof typeof endpoints }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<DataType | null>(null);
  const [creating, setCreating] = useState(false);
  const query = useQuery({
    queryKey: [title],
    queryFn: () => apiGet<DataType[]>(endpoints[title]),
    retry: shouldRetry
  });
  const rows = query.data ?? [];
  const helper = createColumnHelper<DataType>();
  const keys = columnsByPage[title];
  const table = useReactTable({
    data: rows,
    columns: keys.map((key) =>
      helper.accessor((row) => String((row as unknown as Record<string, unknown>)[key] ?? ""), {
        id: key,
        header: key.replaceAll("_", " ")
      })
    ),
    getCoreRowModel: getCoreRowModel()
  });
  const people = useQuery({
    queryKey: ["People"],
    queryFn: () => apiGet<Person[]>("/api/dashboard/people"),
    retry: shouldRetry,
    enabled: title === "Projects"
  });
  const mutation = useMutation<DataType, Error, Record<string, unknown>>({
    mutationFn: (payload: Record<string, unknown>) => {
      if (title === "People") {
        return editing ? apiPut<Person>(`/api/dashboard/people/${(editing as Person).id}`, payload) : apiPost<Person>("/api/dashboard/people", payload);
      }
      if (title === "Projects") {
        return editing
          ? apiPut<Project>(`/api/dashboard/projects/${(editing as Project).id}`, payload)
          : apiPost<Project>("/api/dashboard/projects", payload);
      }
      throw new Error(`${title} cannot be edited here yet.`);
    },
    onSuccess: () => {
      setEditing(null);
      setCreating(false);
      void queryClient.invalidateQueries({ queryKey: [title] });
      void queryClient.invalidateQueries({ queryKey: ["summary"] });
      if (title === "Projects") void queryClient.invalidateQueries({ queryKey: ["Tasks"] });
    }
  });
  const canEdit = title === "People" || title === "Projects";

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold">{title}</h2>
          <span className="text-sm text-stone-500">{query.isLoading ? "Loading" : `${rows.length} records`}</span>
        </div>
        {canEdit && (
          <Button
            className="bg-white text-ink hover:bg-stone-100"
            onClick={() => {
              setEditing(null);
              setCreating(true);
            }}
          >
            <Plus size={16} />
            New
          </Button>
        )}
      </div>
      {query.isError && (
        <div className="mb-4">
          <Notice title={`${title} could not load`}>{errorMessage(query.error)}</Notice>
        </div>
      )}
      {mutation.isError && (
        <div className="mb-4">
          <Notice title={`${title} could not save`}>{errorMessage(mutation.error)}</Notice>
        </div>
      )}
      {canEdit && (creating || editing) && (
        <div className="mb-4 border-y border-stone-200 bg-white/80 p-4">
          {title === "People" ? (
            <PersonForm
              initial={editing as Person | null}
              isSaving={mutation.isPending}
              onCancel={() => {
                setCreating(false);
                setEditing(null);
              }}
              onSave={(payload) => mutation.mutate(payload)}
            />
          ) : (
            <ProjectForm
              initial={editing as Project | null}
              isSaving={mutation.isPending}
              people={people.data ?? []}
              onCancel={() => {
                setCreating(false);
                setEditing(null);
              }}
              onSave={(payload) => mutation.mutate(payload)}
            />
          )}
        </div>
      )}
      {query.isLoading && <LoadingPanel label={`Loading ${title.toLowerCase()}`} />}
      {!query.isLoading && (
        <div className="overflow-hidden border border-stone-200 bg-white">
          <table className="min-w-full text-left text-sm">
            <thead className="bg-stone-100 text-stone-600">
              {table.getHeaderGroups().map((headerGroup) => (
                <tr key={headerGroup.id}>
                  {headerGroup.headers.map((header) => (
                    <th className="px-4 py-3 font-medium capitalize" key={header.id}>
                      {flexRender(header.column.columnDef.header, header.getContext())}
                    </th>
                  ))}
                  {canEdit && <th className="w-16 px-3 py-3" />}
                </tr>
              ))}
            </thead>
            <tbody>
              {table.getRowModel().rows.map((row) => (
                <tr className="border-t border-stone-100" key={row.id}>
                  {row.getVisibleCells().map((cell) => (
                    <td className="max-w-80 truncate px-4 py-3" key={cell.id}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                  {canEdit && (
                    <td className="w-28 px-3 py-2 text-right">
                      <button
                        className="inline-flex h-9 items-center gap-1 rounded-md border border-stone-300 bg-white px-3 text-sm font-medium text-ink shadow-sm transition hover:border-ink hover:bg-stone-100"
                        onClick={() => {
                          setCreating(false);
                          setEditing(row.original);
                        }}
                        title="Edit"
                      >
                        <Edit3 size={15} />
                        Edit
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          {!rows.length && !query.isError && <div className="px-4 py-10 text-center text-sm text-stone-500">No records yet</div>}
        </div>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block text-sm font-medium">
      {label}
      {children}
    </label>
  );
}

const inputClass = "mt-1 h-10 w-full border border-stone-300 bg-white px-3 text-sm outline-none transition focus:border-ink";
const textareaClass = "mt-1 min-h-20 w-full border border-stone-300 bg-white px-3 py-2 text-sm outline-none transition focus:border-ink";

function PersonForm({
  initial,
  isSaving,
  onCancel,
  onSave
}: {
  initial: Person | null;
  isSaving: boolean;
  onCancel: () => void;
  onSave: (payload: Record<string, unknown>) => void;
}) {
  const [form, setForm] = useState({
    full_name: initial?.full_name ?? "",
    company: initial?.company ?? "",
    job_title: initial?.job_title ?? "",
    email: initial?.email ?? "",
    whatsapp_number: initial?.whatsapp_number ?? "",
    active: initial?.active ?? true
  });
  return (
    <form
      className="grid gap-3 md:grid-cols-3"
      onSubmit={(event) => {
        event.preventDefault();
        onSave({
          ...form,
          company: form.company || null,
          job_title: form.job_title || null,
          email: form.email || null,
          whatsapp_number: form.whatsapp_number || null
        });
      }}
    >
      <Field label="Name">
        <input className={inputClass} required value={form.full_name} onChange={(event) => setForm({ ...form, full_name: event.target.value })} />
      </Field>
      <Field label="Email">
        <input className={inputClass} type="email" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} />
      </Field>
      <Field label="WhatsApp">
        <input className={inputClass} value={form.whatsapp_number} onChange={(event) => setForm({ ...form, whatsapp_number: event.target.value })} />
      </Field>
      <Field label="Company">
        <input className={inputClass} value={form.company} onChange={(event) => setForm({ ...form, company: event.target.value })} />
      </Field>
      <Field label="Job title">
        <input className={inputClass} value={form.job_title} onChange={(event) => setForm({ ...form, job_title: event.target.value })} />
      </Field>
      <label className="mt-7 flex h-10 items-center gap-2 text-sm">
        <input checked={form.active} onChange={(event) => setForm({ ...form, active: event.target.checked })} type="checkbox" />
        Active
      </label>
      <FormActions isSaving={isSaving} onCancel={onCancel} />
    </form>
  );
}

function ProjectForm({
  initial,
  isSaving,
  people,
  onCancel,
  onSave
}: {
  initial: Project | null;
  isSaving: boolean;
  people: Person[];
  onCancel: () => void;
  onSave: (payload: Record<string, unknown>) => void;
}) {
  const [form, setForm] = useState({
    person_id: initial?.person_id ?? people[0]?.id ?? "",
    name: initial?.name ?? "",
    description: initial?.description ?? "",
    status: initial?.status ?? "active"
  });
  return (
    <form
      className="grid gap-3 md:grid-cols-3"
      onSubmit={(event) => {
        event.preventDefault();
        onSave({ ...form, description: form.description || null });
      }}
    >
      <Field label="Project">
        <input className={inputClass} required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
      </Field>
      <Field label="Owner">
        <select className={inputClass} required value={form.person_id} onChange={(event) => setForm({ ...form, person_id: event.target.value })}>
          <option value="" disabled>
            Select person
          </option>
          {people.map((person) => (
            <option key={person.id} value={person.id}>
              {person.full_name}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Status">
        <select className={inputClass} value={form.status} onChange={(event) => setForm({ ...form, status: event.target.value })}>
          {["active", "paused", "completed", "cancelled"].map((status) => (
            <option key={status} value={status}>
              {status}
            </option>
          ))}
        </select>
      </Field>
      <div className="md:col-span-3">
        <Field label="Description">
          <textarea className={textareaClass} value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} />
        </Field>
      </div>
      <FormActions isSaving={isSaving} onCancel={onCancel} />
    </form>
  );
}

function FormActions({ isSaving, onCancel }: { isSaving: boolean; onCancel: () => void }) {
  return (
    <div className="flex items-end gap-2 md:col-span-3">
      <Button disabled={isSaving} type="submit">
        <Save size={16} />
        {isSaving ? "Saving" : "Save"}
      </Button>
      <Button className="bg-white text-ink hover:bg-stone-100" disabled={isSaving} onClick={onCancel} type="button">
        <X size={16} />
        Cancel
      </Button>
    </div>
  );
}
