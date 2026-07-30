import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ArrowDown, ArrowUp, CalendarDays, CheckSquare, Edit3, FolderKanban, Plus, Save, Search, UserRound, X } from "lucide-react";
import { useMemo, useState } from "react";
import { apiGet, apiPost, apiPut, errorMessage, shouldRetry } from "../api/client";
import { Button, LoadingPanel, Notice, secondaryButtonClass, smallEditButtonClass } from "../components/ui";
import type { Person, Project, Task } from "../types/domain";

const priorities = [
  { id: "urgent", label: "Urgent", tone: "border-coral/50 bg-coral/10 text-coral" },
  { id: "high", label: "High", tone: "border-amber/60 bg-amber/10 text-amber" },
  { id: "medium", label: "Medium", tone: "border-mint/50 bg-mint/10 text-ink" },
  { id: "low", label: "Low", tone: "border-stone-300 bg-stone-50 text-stone-700" }
] as const;

type ProjectChoice = Project | { id: "none"; name: "No project"; person_id: string; status: "active" };

function taskOrder(task: Task, index: number) {
  return task.priority_order && task.priority_order > 0 ? task.priority_order : index + 1;
}

export function TasksBoard() {
  const queryClient = useQueryClient();
  const [selectedPersonId, setSelectedPersonId] = useState("");
  const [selectedProjectId, setSelectedProjectId] = useState("");
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
  const reorderTask = useMutation({
    mutationFn: ({ taskId, priorityOrder }: { taskId: string; priorityOrder: number }) =>
      apiPut<Task>(`/api/dashboard/tasks/${taskId}`, { priority_order: priorityOrder }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["Tasks"] });
      void queryClient.invalidateQueries({ queryKey: ["summary"] });
    }
  });

  const activePeople = useMemo(() => (people.data ?? []).filter((person) => person.active), [people.data]);
  const effectivePersonId = selectedPersonId || activePeople[0]?.id || "";
  const selectedPerson = activePeople.find((person) => person.id === effectivePersonId) ?? null;
  const projectsForPerson = useMemo(
    () => (projects.data ?? []).filter((project) => project.person_id === effectivePersonId && project.status !== "cancelled"),
    [effectivePersonId, projects.data]
  );
  const projectChoices: ProjectChoice[] = useMemo(
    () => [
      ...projectsForPerson.sort((a, b) => a.name.localeCompare(b.name)),
      { id: "none", name: "No project", person_id: effectivePersonId, status: "active" }
    ],
    [effectivePersonId, projectsForPerson]
  );
  const effectiveProjectId = projectChoices.some((project) => project.id === selectedProjectId)
    ? selectedProjectId
    : projectChoices[0]?.id || "none";
  const selectedProject = projectChoices.find((project) => project.id === effectiveProjectId) ?? projectChoices[0] ?? null;

  const visibleTasks = useMemo(() => {
    const normalizedSearch = search.trim().toLowerCase();
    return (tasks.data ?? [])
      .filter((task) => task.assigned_person_id === effectivePersonId)
      .filter((task) => (effectiveProjectId === "none" ? !task.project_id : task.project_id === effectiveProjectId))
      .filter((task) => {
        if (!normalizedSearch) return true;
        const haystack = [task.title, task.description, task.status, task.priority, task.project_name, task.assigned_person_name]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        return normalizedSearch.split(/\s+/).every((term) => haystack.includes(term));
      })
      .sort((a, b) => {
        const orderA = a.priority_order && a.priority_order > 0 ? a.priority_order : 1_000_000;
        const orderB = b.priority_order && b.priority_order > 0 ? b.priority_order : 1_000_000;
        if (orderA !== orderB) return orderA - orderB;
        return new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
      });
  }, [effectivePersonId, effectiveProjectId, search, tasks.data]);

  if (tasks.isLoading || projects.isLoading || people.isLoading) return <LoadingPanel label="Loading task page" />;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold">Tasks</h2>
          <p className="text-sm text-stone-500">Person, project, numbered priority sequence</p>
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

      <div className="grid gap-3 border-y border-stone-200 bg-white/80 p-4 lg:grid-cols-[minmax(180px,1fr)_minmax(180px,1fr)_minmax(220px,1.2fr)]">
        <label className="block text-sm font-medium">
          Person
          <select
            className={inputClass}
            value={effectivePersonId}
            onChange={(event) => {
              setSelectedPersonId(event.target.value);
              setSelectedProjectId("");
            }}
          >
            {activePeople.map((person) => (
              <option key={person.id} value={person.id}>
                {person.full_name}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-sm font-medium">
          Project
          <select className={inputClass} value={effectiveProjectId} onChange={(event) => setSelectedProjectId(event.target.value)}>
            {projectChoices.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-sm font-medium">
          Search
          <span className="mt-1 flex h-10 items-center gap-2 border border-stone-300 bg-white px-3">
            <Search className="shrink-0 text-stone-500" size={17} />
            <input
              className="h-8 w-full bg-transparent text-sm outline-none"
              placeholder="Task title, status, priority"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </span>
        </label>
      </div>

      {(tasks.isError || projects.isError || people.isError || saveTask.isError || reorderTask.isError) && (
        <Notice title="Task page needs attention">
          {tasks.isError
            ? errorMessage(tasks.error)
            : projects.isError
              ? errorMessage(projects.error)
              : people.isError
                ? errorMessage(people.error)
                : saveTask.isError
                  ? errorMessage(saveTask.error)
                  : errorMessage(reorderTask.error)}
        </Notice>
      )}

      {(creating || editing) && (
        <div className="border-y border-stone-200 bg-white/80 p-4">
          <TaskForm
            key={`${editing?.id ?? "new"}:${effectivePersonId}:${effectiveProjectId}`}
            initial={editing}
            isSaving={saveTask.isPending}
            people={activePeople}
            projects={projects.data ?? []}
            defaultPersonId={effectivePersonId}
            defaultProjectId={effectiveProjectId === "none" ? "" : effectiveProjectId}
            onCancel={() => {
              setCreating(false);
              setEditing(null);
            }}
            onSave={(payload) => saveTask.mutate(payload)}
          />
        </div>
      )}

      <section className="border-y border-stone-200 bg-white/80">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-stone-200 px-4 py-3">
          <div className="flex min-w-0 items-center gap-2">
            {selectedProject?.id === "none" ? <UserRound className="shrink-0 text-mint" size={18} /> : <FolderKanban className="shrink-0 text-mint" size={18} />}
            <div className="min-w-0">
              <h3 className="truncate text-base font-semibold">{selectedProject?.name ?? "No project selected"}</h3>
              <p className="text-sm text-stone-500">{selectedPerson?.full_name ?? "No person selected"}</p>
            </div>
          </div>
          <span className="inline-flex items-center gap-1 text-sm text-stone-500">
            <CheckSquare size={15} />
            {visibleTasks.length} tasks
          </span>
        </div>

        {visibleTasks.length === 0 ? (
          <div className="px-4 py-8 text-sm text-stone-500">No tasks in this project.</div>
        ) : (
          <div className="divide-y divide-stone-200">
            {visibleTasks.map((task, index) => (
              <TaskRow
                canMoveDown={index < visibleTasks.length - 1 && effectiveProjectId !== "none"}
                canMoveUp={index > 0 && effectiveProjectId !== "none"}
                index={index}
                isMoving={reorderTask.isPending}
                key={task.id}
                onEdit={() => {
                  setCreating(false);
                  setEditing(task);
                }}
                onMoveDown={() => reorderTask.mutate({ taskId: task.id, priorityOrder: index + 2 })}
                onMoveUp={() => reorderTask.mutate({ taskId: task.id, priorityOrder: index })}
                task={task}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function TaskRow({
  task,
  index,
  canMoveUp,
  canMoveDown,
  isMoving,
  onMoveUp,
  onMoveDown,
  onEdit
}: {
  task: Task;
  index: number;
  canMoveUp: boolean;
  canMoveDown: boolean;
  isMoving: boolean;
  onMoveUp: () => void;
  onMoveDown: () => void;
  onEdit: () => void;
}) {
  const priorityTone = priorities.find((priority) => priority.id === task.priority)?.tone ?? "border-stone-300 bg-stone-50 text-stone-700";
  return (
    <article className="grid gap-3 px-4 py-3 md:grid-cols-[64px_1fr_auto] md:items-center">
      <div className="flex h-10 w-14 items-center justify-center border border-ink bg-mint text-sm font-bold text-ink">
        #{taskOrder(task, index)}
      </div>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <h4 className="text-sm font-semibold leading-5">{task.title}</h4>
          <span className={`inline-flex h-7 items-center border px-2 text-xs font-semibold ${priorityTone}`}>{task.priority}</span>
          {task.status !== "open" && (
            <span className="inline-flex h-7 items-center gap-1 border border-stone-200 bg-white px-2 text-xs text-stone-600">
              <AlertCircle size={12} />
              {task.status}
            </span>
          )}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-stone-500">
          {task.due_date && (
            <span className="inline-flex items-center gap-1">
              <CalendarDays size={13} />
              {new Date(task.due_date).toLocaleDateString()}
            </span>
          )}
          {task.description && <span className="truncate">{task.description}</span>}
        </div>
      </div>
      <div className="flex items-center gap-2">
        <button className={smallEditButtonClass} disabled={!canMoveUp || isMoving} onClick={onMoveUp} title="Move up" type="button">
          <ArrowUp size={14} />
          Up
        </button>
        <button className={smallEditButtonClass} disabled={!canMoveDown || isMoving} onClick={onMoveDown} title="Move down" type="button">
          <ArrowDown size={14} />
          Down
        </button>
        <button className={smallEditButtonClass} onClick={onEdit} title="Edit task" type="button">
          <Edit3 size={14} />
          Edit
        </button>
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
  defaultPersonId,
  defaultProjectId,
  onCancel,
  onSave
}: {
  initial: Task | null;
  isSaving: boolean;
  people: Person[];
  projects: Project[];
  defaultPersonId: string;
  defaultProjectId: string;
  onCancel: () => void;
  onSave: (payload: Record<string, unknown>) => void;
}) {
  const [form, setForm] = useState({
    title: initial?.title ?? "",
    description: initial?.description ?? "",
    assigned_person_id: initial?.assigned_person_id ?? defaultPersonId,
    project_id: initial?.project_id ?? defaultProjectId,
    priority: initial?.priority ?? "medium",
    priority_order: initial?.priority_order ? String(initial.priority_order) : "",
    status: initial?.status ?? "open",
    due_date: initial?.due_date ? initial.due_date.slice(0, 10) : ""
  });
  const filteredProjects = projects.filter((project) => project.person_id === form.assigned_person_id && project.status !== "cancelled");
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
          priority_order: form.priority_order ? Number(form.priority_order) : null,
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
        Label priority
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
        <select
          className={inputClass}
          value={form.assigned_person_id}
          onChange={(event) => setForm({ ...form, assigned_person_id: event.target.value, project_id: "" })}
        >
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
          {filteredProjects.map((project) => (
            <option key={project.id} value={project.id}>
              {project.name}
            </option>
          ))}
        </select>
      </label>
      <label className="block text-sm font-medium">
        Project order
        <input
          className={inputClass}
          min="1"
          placeholder="Auto"
          type="number"
          value={form.priority_order}
          onChange={(event) => setForm({ ...form, priority_order: event.target.value })}
        />
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
