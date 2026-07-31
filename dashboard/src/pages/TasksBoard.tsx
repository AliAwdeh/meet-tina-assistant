import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  Building2,
  CalendarDays,
  ChevronRight,
  FolderKanban,
  GripVertical,
  Mail,
  Pencil,
  Plus,
  Save,
  Search,
  UserRound,
  X
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { apiGet, apiPost, apiPut, errorMessage, shouldRetry } from "../api/client";
import { Button, LoadingPanel, Notice, secondaryButtonClass } from "../components/ui";
import type { Person, Project, Task } from "../types/domain";

const priorities = [
  { id: "urgent", label: "Urgent", dot: "bg-coral", tone: "border-coral/40 bg-coral/10 text-coral" },
  { id: "high", label: "High", dot: "bg-amber", tone: "border-amber/50 bg-amber/10 text-amber" },
  { id: "medium", label: "Medium", dot: "bg-mint", tone: "border-mint/50 bg-mint/10 text-ink" },
  { id: "low", label: "Low", dot: "bg-stone-400", tone: "border-stone-300 bg-stone-50 text-stone-600" }
] as const;

type ProjectGroup = {
  id: string;
  name: string;
  personId: string;
  status: string;
  tasks: Task[];
};

type PersonGroup = {
  person: Person;
  projects: ProjectGroup[];
  taskCount: number;
};

type FormDefaults = { personId: string; projectId: string };

function firstName(name: string) {
  return name.trim().split(/\s+/)[0] || "there";
}

function groupKey(personId: string, projectId: string) {
  return `${personId}:${projectId}`;
}

function initials(name: string) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "")).toUpperCase();
}

function personDetail(person: Person) {
  if (person.company) return { icon: Building2, value: person.company };
  if (person.email) return { icon: Mail, value: person.email };
  return { icon: UserRound, value: "No company or email" };
}

export function TasksBoard({ userName }: { userName: string }) {
  const queryClient = useQueryClient();
  const [openPeople, setOpenPeople] = useState<Record<string, boolean>>({});
  const [openProjects, setOpenProjects] = useState<Record<string, boolean>>({});
  const [editing, setEditing] = useState<Task | null>(null);
  const [creating, setCreating] = useState(false);
  const [formDefaults, setFormDefaults] = useState<FormDefaults>({ personId: "", projectId: "" });
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

  const groups = useMemo(() => {
    const normalizedSearch = search.trim().toLowerCase();
    const activePeople = (people.data ?? []).filter((person) => person.active).sort((a, b) => a.full_name.localeCompare(b.full_name));
    const allTasks = tasks.data ?? [];
    return activePeople
      .map<PersonGroup>((person) => {
        const personProjects = (projects.data ?? [])
          .filter((project) => project.person_id === person.id && project.status !== "cancelled")
          .sort((a, b) => a.name.localeCompare(b.name));
        const projectGroups: ProjectGroup[] = [
          ...personProjects.map((project) => ({
            id: project.id,
            name: project.name,
            personId: person.id,
            status: project.status,
            tasks: [] as Task[]
          })),
          { id: "none", name: "No project", personId: person.id, status: "active", tasks: [] }
        ];
        const projectMap = new Map(projectGroups.map((project) => [project.id, project]));
        for (const task of allTasks) {
          if (task.assigned_person_id !== person.id) continue;
          const target = task.project_id && projectMap.has(task.project_id) ? projectMap.get(task.project_id) : projectMap.get("none");
          target?.tasks.push(task);
        }
        for (const project of projectGroups) {
          project.tasks.sort((a, b) => {
            const orderA = a.priority_order && a.priority_order > 0 ? a.priority_order : 1_000_000;
            const orderB = b.priority_order && b.priority_order > 0 ? b.priority_order : 1_000_000;
            if (orderA !== orderB) return orderA - orderB;
            return new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
          });
        }
        const filteredProjects = normalizedSearch
          ? projectGroups
              .map((project) => ({
                ...project,
                tasks: project.tasks.filter((task) => {
                  const haystack = [person.full_name, project.name, task.title, task.description, task.status, task.priority]
                    .filter(Boolean)
                    .join(" ")
                    .toLowerCase();
                  return normalizedSearch.split(/\s+/).every((term) => haystack.includes(term));
                })
              }))
              .filter((project) => project.tasks.length > 0 || project.name.toLowerCase().includes(normalizedSearch))
          : projectGroups;
        return {
          person,
          projects: filteredProjects,
          taskCount: projectGroups.reduce((count, project) => count + project.tasks.length, 0)
        };
      })
      .filter((group) => !normalizedSearch || group.projects.length > 0 || group.person.full_name.toLowerCase().includes(normalizedSearch));
  }, [people.data, projects.data, search, tasks.data]);

  const totalTasks = groups.reduce((count, group) => count + group.taskCount, 0);

  const openCreateForm = (defaults: FormDefaults) => {
    setEditing(null);
    setFormDefaults(defaults);
    setCreating(true);
  };

  if (tasks.isLoading || projects.isLoading || people.isLoading) return <LoadingPanel label="Loading your board" />;

  return (
    <div className="space-y-6">
      <div className="overflow-hidden rounded-lg border border-stone-200/70 bg-white shadow-sm">
        <div className="border-l-4 border-mint p-6 sm:p-8">
          <p className="text-sm font-medium text-mint">Meet Tina</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">
            Hello {firstName(userName)}, what are we doing today?
          </h1>
          <p className="mt-3 max-w-xl text-sm text-stone-500">
            {groups.length} {groups.length === 1 ? "person" : "people"} · {totalTasks} {totalTasks === 1 ? "task" : "tasks"} in play. Project
            priority lists are lined up for Sami.
          </p>
        </div>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <label className="flex h-12 min-w-0 flex-1 items-center gap-2.5 rounded-lg border border-stone-200/80 bg-white px-4 text-sm shadow-sm transition focus-within:border-ink">
          <Search className="shrink-0 text-stone-400" size={18} />
          <input
            className="h-10 w-full bg-transparent outline-none placeholder:text-stone-400"
            placeholder="Search people, projects, or tasks"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          {search && (
            <button className="text-stone-400 transition hover:text-ink" onClick={() => setSearch("")} type="button">
              <X size={16} />
            </button>
          )}
        </label>
        <Button className="h-12 rounded-lg" onClick={() => openCreateForm({ personId: groups[0]?.person.id ?? "", projectId: "" })}>
          <Plus size={17} />
          New task
        </Button>
      </div>

      {(tasks.isError || projects.isError || people.isError || saveTask.isError || reorderTask.isError) && (
        <Notice title="Something needs attention">
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
        <div className="rounded-lg border border-stone-200/80 bg-white p-5 shadow-sm">
          <TaskForm
            key={`${editing?.id ?? "new"}:${formDefaults.personId}:${formDefaults.projectId}`}
            initial={editing}
            isSaving={saveTask.isPending}
            people={(people.data ?? []).filter((person) => person.active)}
            projects={projects.data ?? []}
            defaultPersonId={formDefaults.personId}
            defaultProjectId={formDefaults.projectId}
            onCancel={() => {
              setCreating(false);
              setEditing(null);
            }}
            onSave={(payload) => saveTask.mutate(payload)}
          />
        </div>
      )}

      {groups.length === 0 ? (
        <div className="rounded-lg border border-dashed border-stone-300 bg-white px-5 py-14 text-center text-sm text-stone-500">
          No people or tasks match the current search.
        </div>
      ) : (
        <div className="space-y-3">
          {groups.map((group) => {
            const personOpen = openPeople[group.person.id] ?? (search.trim().length > 0 || group.taskCount > 0);
            const detail = personDetail(group.person);
            const DetailIcon = detail.icon;
            const projectCount = group.projects.filter((project) => project.id !== "none").length;
            return (
              <section className="overflow-hidden rounded-lg border border-stone-200/80 bg-white shadow-sm" key={group.person.id}>
                <button
                  className="flex w-full items-center gap-4 px-4 py-4 text-left transition hover:bg-stone-50/70"
                  onClick={() => setOpenPeople((current) => ({ ...current, [group.person.id]: !personOpen }))}
                  type="button"
                >
                  <ChevronRight className={`shrink-0 text-stone-400 transition-transform ${personOpen ? "rotate-90" : ""}`} size={20} />
                  <span className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-mint/15 text-sm font-bold text-ink">
                    {initials(group.person.full_name)}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-base font-semibold">{group.person.full_name}</span>
                    <span className="mt-0.5 flex min-w-0 items-center gap-1.5 text-sm text-stone-500">
                      <DetailIcon className="shrink-0" size={14} />
                      <span className="truncate">{detail.value}</span>
                    </span>
                  </span>
                  <span className="shrink-0 rounded-md bg-stone-100 px-3 py-1 text-xs font-medium text-stone-600">
                    {projectCount} {projectCount === 1 ? "project" : "projects"} · {group.taskCount} {group.taskCount === 1 ? "task" : "tasks"}
                  </span>
                </button>
                {personOpen && (
                  <div className="space-y-1.5 border-t border-stone-100 bg-stone-50/40 px-3 py-3">
                    {group.projects.map((project) => {
                      const key = groupKey(group.person.id, project.id);
                      const projectOpen = openProjects[key] ?? (search.trim().length > 0 || project.tasks.length > 0);
                      return (
                        <ProjectSection
                          isMoving={reorderTask.isPending}
                          isOpen={projectOpen}
                          key={key}
                          onAddTask={() => openCreateForm({ personId: group.person.id, projectId: project.id === "none" ? "" : project.id })}
                          onEditTask={(task) => {
                            setCreating(false);
                            setFormDefaults({ personId: task.assigned_person_id ?? group.person.id, projectId: task.project_id ?? "" });
                            setEditing(task);
                          }}
                          onReorder={(taskId, priorityOrder) => reorderTask.mutate({ taskId, priorityOrder })}
                          onToggle={() => setOpenProjects((current) => ({ ...current, [key]: !projectOpen }))}
                          project={project}
                        />
                      );
                    })}
                  </div>
                )}
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}

function ProjectSection({
  project,
  isOpen,
  isMoving,
  onToggle,
  onAddTask,
  onReorder,
  onEditTask
}: {
  project: ProjectGroup;
  isOpen: boolean;
  isMoving: boolean;
  onToggle: () => void;
  onAddTask: () => void;
  onReorder: (taskId: string, priorityOrder: number) => void;
  onEditTask: (task: Task) => void;
}) {
  const draggable = project.id !== "none";
  // Local optimistic order so drag feels instant; resync when server data changes.
  const [order, setOrder] = useState<Task[]>(project.tasks);
  const [dragId, setDragId] = useState<string | null>(null);
  const [overId, setOverId] = useState<string | null>(null);
  useEffect(() => {
    setOrder(project.tasks);
  }, [project.tasks]);

  const highestPriority = order.find((task) => task.priority === "urgent" || task.priority === "high");

  const handleDrop = (targetId: string) => {
    if (!dragId || dragId === targetId) {
      setDragId(null);
      setOverId(null);
      return;
    }
    const current = [...order];
    const from = current.findIndex((task) => task.id === dragId);
    const to = current.findIndex((task) => task.id === targetId);
    setDragId(null);
    setOverId(null);
    if (from === -1 || to === -1) return;
    const [moved] = current.splice(from, 1);
    current.splice(to, 0, moved);
    setOrder(current);
    const newIndex = current.findIndex((task) => task.id === moved.id);
    onReorder(moved.id, newIndex + 1);
  };

  return (
    <div className="overflow-hidden rounded-lg border border-stone-200/70 bg-white">
      <div className="flex items-center gap-2 px-3 py-2.5">
        <button className="flex min-w-0 flex-1 items-center gap-3 text-left" onClick={onToggle} type="button">
          <ChevronRight className={`shrink-0 text-stone-400 transition-transform ${isOpen ? "rotate-90" : ""}`} size={17} />
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-md bg-mint/10 text-mint">
            <FolderKanban size={16} />
          </span>
          <span className="min-w-0">
            <span className="flex min-w-0 flex-wrap items-center gap-2">
              <span className="truncate text-sm font-semibold">{project.name}</span>
              {highestPriority && (
                <span className="rounded-md border border-amber/50 bg-amber/10 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-amber">
                  {highestPriority.priority}
                </span>
              )}
            </span>
            <span className="mt-0.5 block text-xs text-stone-400">
              {order.length} {order.length === 1 ? "task" : "tasks"}
              {project.id === "none" ? "" : ` · ${project.status}`}
            </span>
          </span>
        </button>
        <button
          className="inline-flex h-8 items-center gap-1 rounded-md border border-stone-200 px-2.5 text-xs font-medium text-stone-600 transition hover:border-ink hover:text-ink"
          onClick={onAddTask}
          type="button"
        >
          <Plus size={14} />
          Task
        </button>
      </div>
      {isOpen && (
        <div className="border-t border-stone-100 bg-stone-50/50 p-2.5">
          {order.length === 0 ? (
            <div className="rounded-md border border-dashed border-stone-300 bg-white px-4 py-6 text-center text-sm text-stone-400">
              No tasks here yet.
            </div>
          ) : (
            <div className="space-y-2">
              {order.map((task, index) => (
                <TaskRow
                  draggable={draggable}
                  index={index}
                  isDragging={dragId === task.id}
                  isMoving={isMoving}
                  isOver={overId === task.id && dragId !== null && dragId !== task.id}
                  key={task.id}
                  onDragEnd={() => {
                    setDragId(null);
                    setOverId(null);
                  }}
                  onDragOver={() => setOverId(task.id)}
                  onDragStart={() => setDragId(task.id)}
                  onDrop={() => handleDrop(task.id)}
                  onEdit={() => onEditTask(task)}
                  task={task}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TaskRow({
  task,
  index,
  draggable,
  isDragging,
  isOver,
  isMoving,
  onDragStart,
  onDragOver,
  onDrop,
  onDragEnd,
  onEdit
}: {
  task: Task;
  index: number;
  draggable: boolean;
  isDragging: boolean;
  isOver: boolean;
  isMoving: boolean;
  onDragStart: () => void;
  onDragOver: () => void;
  onDrop: () => void;
  onDragEnd: () => void;
  onEdit: () => void;
}) {
  const priority = priorities.find((item) => item.id === task.priority) ?? priorities[3];
  return (
    <article
      className={`group flex items-center gap-3 rounded-lg border bg-white px-3 py-2.5 shadow-sm transition ${
        isDragging ? "border-mint opacity-50" : isOver ? "border-mint ring-2 ring-mint/40" : "border-stone-200/80 hover:border-stone-300"
      }`}
      onDragEnd={onDragEnd}
      onDragOver={(event) => {
        if (!draggable) return;
        event.preventDefault();
        onDragOver();
      }}
      onDrop={(event) => {
        event.preventDefault();
        onDrop();
      }}
    >
      {draggable ? (
        <span
          aria-label="Move task"
          className="grid h-8 w-8 shrink-0 cursor-grab place-items-center rounded-md text-stone-300 transition group-hover:text-stone-500 active:cursor-grabbing"
          draggable={!isMoving}
          onDragStart={(event) => {
            event.dataTransfer.effectAllowed = "move";
            onDragStart();
          }}
          role="button"
          tabIndex={0}
        >
          <GripVertical size={17} />
        </span>
      ) : (
        <span className="h-8 w-8 shrink-0" />
      )}
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-md bg-ink text-xs font-bold text-white">{index + 1}</span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <h4 className="text-sm font-semibold leading-5 text-ink">{task.title}</h4>
          <span className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-semibold ${priority.tone}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${priority.dot}`} />
            {priority.label}
          </span>
          {task.status !== "open" && (
            <span className="inline-flex items-center gap-1 rounded-md border border-stone-200 bg-stone-50 px-2 py-0.5 text-[11px] text-stone-500">
              <AlertCircle size={11} />
              {task.status}
            </span>
          )}
        </div>
        {(task.due_date || task.description) && (
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-stone-400">
            {task.due_date && (
              <span className="inline-flex items-center gap-1">
                <CalendarDays size={13} />
                {new Date(task.due_date).toLocaleDateString()}
              </span>
            )}
            {task.description && <span className="truncate">{task.description}</span>}
          </div>
        )}
      </div>
      <button
        className="grid h-8 w-8 shrink-0 place-items-center rounded-md text-stone-400 transition hover:bg-stone-100 hover:text-ink sm:opacity-0 sm:group-hover:opacity-100"
        onClick={onEdit}
        title="Edit task"
        type="button"
      >
        <Pencil size={15} />
      </button>
    </article>
  );
}

const inputClass = "mt-1 h-10 w-full rounded-lg border border-stone-300 bg-white px-3 text-sm outline-none transition focus:border-ink";
const textareaClass = "mt-1 min-h-20 w-full rounded-lg border border-stone-300 bg-white px-3 py-2 text-sm outline-none transition focus:border-ink";

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
      <div className="border-b border-stone-200 pb-2 md:col-span-3">
        <h3 className="text-base font-semibold">{initial ? "Edit task" : "New task"}</h3>
        <p className="mt-1 text-sm text-stone-500">Assign it to a person, optionally place it inside a project, and set its sequence.</p>
      </div>
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
