import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CalendarDays, CheckSquare, Edit3, FolderKanban, GripVertical, Plus, Save, Search, UserRound, X } from "lucide-react";
import { useMemo, useState } from "react";
import { apiGet, apiPost, apiPut, errorMessage, shouldRetry } from "../api/client";
import { Button, LoadingPanel, Notice, secondaryButtonClass, smallEditButtonClass } from "../components/ui";
import type { Person, Project, Task } from "../types/domain";

const priorities = [
  { id: "urgent", label: "Urgent", tone: "border-coral/40 bg-coral/5 text-coral" },
  { id: "high", label: "High", tone: "border-amber/50 bg-amber/10 text-amber" },
  { id: "medium", label: "Medium", tone: "border-mint/40 bg-mint/10 text-mint" },
  { id: "low", label: "Low", tone: "border-stone-300 bg-stone-50 text-stone-600" }
] as const;

type Priority = (typeof priorities)[number]["id"];
type DragState = { taskId: string; fromPriority: string; fromPersonId: string | null } | null;

export function TasksBoard() {
  const queryClient = useQueryClient();
  const [dragged, setDragged] = useState<DragState>(null);
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const [editing, setEditing] = useState<Task | null>(null);
  const [creating, setCreating] = useState(false);
  const [search, setSearch] = useState("");
  const tasks = useQuery({
    queryKey: ["Tasks"],
    queryFn: () => apiGet<Task[]>("/api/dashboard/tasks"),
    retry: shouldRetry
  });
  const projects = useQuery({
    queryKey: ["Projects"],
    queryFn: () => apiGet<Project[]>("/api/dashboard/projects"),
    retry: shouldRetry
  });
  const people = useQuery({
    queryKey: ["People"],
    queryFn: () => apiGet<Person[]>("/api/dashboard/people"),
    retry: shouldRetry
  });
  const saveTask = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      editing ? apiPut<Task>(`/api/dashboard/tasks/${editing.id}`, payload) : apiPost<Task>("/api/dashboard/tasks", payload),
    onSuccess: () => {
      setEditing(null);
      setCreating(false);
      void queryClient.invalidateQueries({ queryKey: ["Tasks"] });
      void queryClient.invalidateQueries({ queryKey: ["summary"] });
    }
  });
  const moveTask = useMutation({
    mutationFn: ({ taskId, priority, personId }: { taskId: string; priority: Priority; personId: string | null }) =>
      apiPut<Task>(`/api/dashboard/tasks/${taskId}`, { priority, assigned_person_id: personId }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["Tasks"] });
      void queryClient.invalidateQueries({ queryKey: ["summary"] });
    }
  });

  const personGroups = useMemo(() => {
    const normalizedSearch = search.trim().toLowerCase();
    const rows = tasks.data ?? [];
    const projectLookup = new Map((projects.data ?? []).map((project) => [project.id, project]));
    const filteredRows = normalizedSearch
      ? rows.filter((task) => {
          const project = task.project_id ? projectLookup.get(task.project_id) : null;
          const haystack = [
            task.title,
            task.description,
            task.status,
            task.priority,
            task.assigned_person_name,
            task.project_name,
            project?.name,
            project?.person_name
          ]
            .filter(Boolean)
            .join(" ")
            .toLowerCase();
          return normalizedSearch.split(/\s+/).every((term) => haystack.includes(term));
        })
      : rows;
    const groups = new Map<string, { id: string; personId: string | null; name: string; detail?: string | null; tasks: Task[] }>();
    for (const person of people.data ?? []) {
      groups.set(person.id, {
        id: person.id,
        personId: person.id,
        name: person.full_name,
        detail: person.company ?? person.email ?? null,
        tasks: []
      });
    }
    for (const task of filteredRows) {
      const key = task.assigned_person_id ?? "none";
      if (!groups.has(key)) {
        groups.set(key, {
          id: key,
          personId: task.assigned_person_id ?? null,
          name: task.assigned_person_name ?? "Unassigned",
          detail: null,
          tasks: []
        });
      }
      groups.get(key)?.tasks.push(task);
    }
    if (!groups.has("none")) groups.set("none", { id: "none", personId: null, name: "Unassigned", tasks: [] });
    const visibleGroups = Array.from(groups.values()).filter((group) => !normalizedSearch || group.tasks.length > 0);
    return visibleGroups.sort((a, b) => {
      if (a.id === "none") return 1;
      if (b.id === "none") return -1;
      return a.name.localeCompare(b.name);
    });
  }, [people.data, projects.data, search, tasks.data]);

  const onDrop = (personId: string | null, priority: Priority) => {
    if (!dragged || moveTask.isPending) return;
    if (dragged.fromPriority === priority && dragged.fromPersonId === personId) return;
    moveTask.mutate({ taskId: dragged.taskId, priority, personId });
    setDragged(null);
    setDropTarget(null);
  };

  if (tasks.isLoading || projects.isLoading || people.isLoading) return <LoadingPanel label="Loading task board" />;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold">Tasks</h2>
          <p className="text-sm text-stone-500">People task board</p>
        </div>
        <Button
          className={secondaryButtonClass}
          onClick={() => {
            setEditing(null);
            setCreating(true);
          }}
        >
          <Plus size={16} />
          New task
        </Button>
      </div>
      <div className="flex max-w-xl items-center gap-2 border border-stone-300 bg-white px-3 py-2 shadow-sm focus-within:border-ink">
        <Search className="shrink-0 text-stone-500" size={17} />
        <input
          className="h-8 w-full bg-transparent text-sm outline-none"
          placeholder="Search tasks, projects, or people"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>

      {(tasks.isError || projects.isError || people.isError || saveTask.isError || moveTask.isError) && (
        <Notice title="Task board needs attention">
          {tasks.isError
            ? errorMessage(tasks.error)
            : projects.isError
              ? errorMessage(projects.error)
              : people.isError
                ? errorMessage(people.error)
                : saveTask.isError
                  ? errorMessage(saveTask.error)
                  : errorMessage(moveTask.error)}
        </Notice>
      )}

      {(creating || editing) && (
        <div className="border-y border-stone-200 bg-white/80 p-4">
          <TaskForm
            initial={editing}
            isSaving={saveTask.isPending}
            people={people.data ?? []}
            projects={projects.data ?? []}
            onCancel={() => {
              setCreating(false);
              setEditing(null);
            }}
            onSave={(payload) => saveTask.mutate(payload)}
          />
        </div>
      )}

      {personGroups.length === 0 && (
        <div className="border-y border-stone-200 bg-white/80 px-4 py-8 text-sm text-stone-500">No tasks match the current search.</div>
      )}

      {personGroups.map((person) => (
        <section className="border-y border-stone-200 bg-white/80 py-4" key={person.id}>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2 px-4">
            <div className="flex min-w-0 items-center gap-2">
              <UserRound className="shrink-0 text-mint" size={18} />
              <h3 className="truncate text-base font-semibold">{person.name}</h3>
            </div>
            <span className="inline-flex items-center gap-1 text-sm text-stone-500">
              <CheckSquare size={15} />
              {person.detail ? `${person.detail} · ${person.tasks.length} tasks` : `${person.tasks.length} tasks`}
            </span>
          </div>

          <div className="grid gap-3 px-4 xl:grid-cols-4">
            {priorities.map((priority) => {
              const rows = person.tasks.filter((task) => task.priority === priority.id);
              const targetKey = `${person.id}:${priority.id}`;
              const isTarget = dropTarget === targetKey;
              return (
                <div
                  className={`min-h-44 border-2 bg-white transition ${
                    isTarget ? "border-ink bg-mint/5 shadow-sm" : "border-stone-200"
                  }`}
                  key={priority.id}
                  onDragLeave={() => setDropTarget(null)}
                  onDragOver={(event) => {
                    event.preventDefault();
                    setDropTarget(targetKey);
                  }}
                  onDrop={() => onDrop(person.personId, priority.id)}
                >
                  <div className={`flex h-10 items-center justify-between border-b px-3 text-sm font-medium ${priority.tone}`}>
                    <span>{priority.label}</span>
                    <span>{rows.length}</span>
                  </div>
                  <div className="space-y-2 p-2">
                    {rows.map((task) => (
                      <TaskTile
                        key={task.id}
                        task={task}
                        onDragStart={() => setDragged({ taskId: task.id, fromPriority: task.priority, fromPersonId: task.assigned_person_id ?? null })}
                        onEdit={() => {
                          setCreating(false);
                          setEditing(task);
                        }}
                      />
                    ))}
                    {!rows.length && (
                      <div className="grid h-20 place-items-center border border-dashed border-stone-300 bg-stone-50 text-xs font-medium text-stone-500">
                        Drop task here
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}

function TaskTile({ task, onDragStart, onEdit }: { task: Task; onDragStart: () => void; onEdit: () => void }) {
  return (
    <article
      className="min-h-28 cursor-grab border border-stone-200 bg-[#fffdf8] p-3 shadow-sm active:cursor-grabbing"
      draggable
      onDragStart={onDragStart}
    >
      <div className="flex items-start gap-2">
        <GripVertical className="mt-0.5 shrink-0 text-stone-400" size={16} />
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <h4 className="line-clamp-2 text-sm font-semibold leading-5">{task.title}</h4>
            <button
              className={smallEditButtonClass}
              onClick={onEdit}
              title="Edit task"
            >
              <Edit3 size={14} />
              Edit
            </button>
          </div>
          {task.assigned_person_name && (
            <div className="mt-2 flex items-center gap-1 text-xs text-stone-500">
              <UserRound size={13} />
              <span className="truncate">{task.assigned_person_name}</span>
            </div>
          )}
          {task.project_name && (
            <div className="mt-1 flex items-center gap-1 text-xs text-stone-500">
              <FolderKanban size={13} />
              <span className="truncate">{task.project_name}</span>
            </div>
          )}
          {task.due_date && (
            <div className="mt-1 flex items-center gap-1 text-xs text-stone-500">
              <CalendarDays size={13} />
              <span>{new Date(task.due_date).toLocaleDateString()}</span>
            </div>
          )}
          {task.status !== "open" && (
            <div className="mt-2 inline-flex items-center gap-1 border border-stone-200 bg-white px-2 py-0.5 text-xs text-stone-500">
              <AlertCircle size={12} />
              {task.status}
            </div>
          )}
        </div>
      </div>
    </article>
  );
}

const inputClass = "mt-1 h-10 w-full border border-stone-300 bg-white px-3 text-sm outline-none transition focus:border-ink";
const textareaClass = "mt-1 min-h-20 w-full border border-stone-300 bg-white px-3 py-2 text-sm outline-none transition focus:border-ink";

function TaskForm({
  initial,
  isSaving,
  people,
  projects,
  onCancel,
  onSave
}: {
  initial: Task | null;
  isSaving: boolean;
  people: Person[];
  projects: Project[];
  onCancel: () => void;
  onSave: (payload: Record<string, unknown>) => void;
}) {
  const [form, setForm] = useState({
    title: initial?.title ?? "",
    description: initial?.description ?? "",
    assigned_person_id: initial?.assigned_person_id ?? "",
    project_id: initial?.project_id ?? "",
    priority: initial?.priority ?? "medium",
    status: initial?.status ?? "open",
    due_date: initial?.due_date ? initial.due_date.slice(0, 10) : ""
  });
  return (
    <form
      className="grid gap-3 md:grid-cols-3"
      onSubmit={(event) => {
        event.preventDefault();
        onSave({
          title: form.title,
          description: form.description || null,
          assigned_person_id: form.assigned_person_id || null,
          project_id: form.project_id || null,
          priority: form.priority,
          status: form.status,
          due_date: form.due_date ? new Date(`${form.due_date}T12:00:00`).toISOString() : null
        });
      }}
    >
      <label className="block text-sm font-medium md:col-span-2">
        Title
        <input className={inputClass} required value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} />
      </label>
      <label className="block text-sm font-medium">
        Priority
        <select className={inputClass} value={form.priority} onChange={(event) => setForm({ ...form, priority: event.target.value })}>
          {priorities.map((priority) => (
            <option key={priority.id} value={priority.id}>
              {priority.label}
            </option>
          ))}
        </select>
      </label>
      <label className="block text-sm font-medium">
        Assigned person
        <select className={inputClass} value={form.assigned_person_id} onChange={(event) => setForm({ ...form, assigned_person_id: event.target.value })}>
          <option value="">Unassigned</option>
          {people.map((person) => (
            <option key={person.id} value={person.id}>
              {person.full_name}
            </option>
          ))}
        </select>
      </label>
      <label className="block text-sm font-medium">
        Project
        <select className={inputClass} value={form.project_id} onChange={(event) => setForm({ ...form, project_id: event.target.value })}>
          <option value="">No project</option>
          {projects.map((project) => (
            <option key={project.id} value={project.id}>
              {project.name}
            </option>
          ))}
        </select>
      </label>
      <label className="block text-sm font-medium">
        Status
        <select className={inputClass} value={form.status} onChange={(event) => setForm({ ...form, status: event.target.value })}>
          {["open", "pending", "in_progress", "completed", "cancelled"].map((status) => (
            <option key={status} value={status}>
              {status}
            </option>
          ))}
        </select>
      </label>
      <label className="block text-sm font-medium">
        Due date
        <input className={inputClass} type="date" value={form.due_date} onChange={(event) => setForm({ ...form, due_date: event.target.value })} />
      </label>
      <label className="block text-sm font-medium md:col-span-3">
        Description
        <textarea className={textareaClass} value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} />
      </label>
      <div className="flex items-end gap-2 md:col-span-3">
        <Button disabled={isSaving} type="submit">
          <Save size={16} />
          {isSaving ? "Saving" : "Save"}
        </Button>
        <Button className={secondaryButtonClass} disabled={isSaving} onClick={onCancel} type="button">
          <X size={16} />
          Cancel
        </Button>
      </div>
    </form>
  );
}
