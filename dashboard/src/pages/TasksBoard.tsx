import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowDown,
  ArrowUp,
  CalendarDays,
  CheckSquare,
  ChevronDown,
  ChevronRight,
  Edit3,
  FolderKanban,
  Plus,
  Save,
  Search,
  UserRound,
  X
} from "lucide-react";
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

function taskOrder(task: Task, index: number) {
  return task.priority_order && task.priority_order > 0 ? task.priority_order : index + 1;
}

function groupKey(personId: string, projectId: string) {
  return `${personId}:${projectId}`;
}

export function TasksBoard() {
  const queryClient = useQueryClient();
  const [collapsedPeople, setCollapsedPeople] = useState<Record<string, boolean>>({});
  const [collapsedProjects, setCollapsedProjects] = useState<Record<string, boolean>>({});
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

  const totalProjects = groups.reduce((count, group) => count + group.projects.filter((project) => project.id !== "none").length, 0);
  const totalTasks = groups.reduce((count, group) => count + group.taskCount, 0);

  const openCreateForm = (defaults: FormDefaults) => {
    setEditing(null);
    setFormDefaults(defaults);
    setCreating(true);
  };

  if (tasks.isLoading || projects.isLoading || people.isLoading) return <LoadingPanel label="Loading task page" />;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold">Tasks</h2>
          <p className="text-sm text-stone-500">People, projects, numbered priority sequence</p>
        </div>
        <Button className={secondaryButtonClass} onClick={() => openCreateForm({ personId: groups[0]?.person.id ?? "", projectId: "" })}>
          <Plus size={16} />
          New task
        </Button>
      </div>

      <div className="grid gap-3 md:grid-cols-[1fr_auto_auto]">
        <label className="flex h-11 min-w-0 items-center gap-2 border border-stone-300 bg-white px-3 text-sm shadow-sm focus-within:border-ink">
          <Search className="shrink-0 text-stone-500" size={17} />
          <input
            className="h-9 w-full bg-transparent outline-none"
            placeholder="Search people, projects, or tasks"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <div className="flex h-11 items-center gap-2 border border-stone-200 bg-white px-3 text-sm text-stone-600">
          <UserRound className="text-mint" size={16} />
          {groups.length} people
        </div>
        <div className="flex h-11 items-center gap-2 border border-stone-200 bg-white px-3 text-sm text-stone-600">
          <FolderKanban className="text-mint" size={16} />
          {totalProjects} projects · {totalTasks} tasks
        </div>
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
        <div className="border-y border-stone-200 bg-white/80 px-4 py-8 text-sm text-stone-500">No people or tasks match the current search.</div>
      ) : (
        <div className="space-y-4">
          {groups.map((group) => {
            const personCollapsed = collapsedPeople[group.person.id] ?? false;
            return (
              <section className="overflow-hidden border border-stone-200 bg-white shadow-sm" key={group.person.id}>
                <button
                  className="flex w-full items-center justify-between gap-3 bg-[#fbfaf6] px-4 py-4 text-left transition hover:bg-mint/10"
                  onClick={() => setCollapsedPeople((current) => ({ ...current, [group.person.id]: !personCollapsed }))}
                  type="button"
                >
                  <span className="flex min-w-0 items-center gap-3">
                    <span className="grid h-10 w-10 shrink-0 place-items-center border border-mint/50 bg-mint/15 text-mint">
                      <UserRound size={19} />
                    </span>
                    <span className="min-w-0">
                      <span className="block truncate text-base font-semibold">{group.person.full_name}</span>
                      <span className="block truncate text-sm text-stone-500">
                        {group.person.company || group.person.email || "No company or email"} · {group.taskCount} tasks
                      </span>
                    </span>
                  </span>
                  <span className="flex shrink-0 items-center gap-3 text-sm text-stone-500">
                    {group.projects.filter((project) => project.id !== "none").length} projects
                    {personCollapsed ? <ChevronRight size={18} /> : <ChevronDown size={18} />}
                  </span>
                </button>
                {!personCollapsed && (
                  <div className="divide-y divide-stone-200">
                    {group.projects.map((project) => {
                      const key = groupKey(group.person.id, project.id);
                      const projectCollapsed = collapsedProjects[key] ?? false;
                      return (
                        <ProjectSection
                          isCollapsed={projectCollapsed}
                          isMoving={reorderTask.isPending}
                          key={key}
                          onAddTask={() => openCreateForm({ personId: group.person.id, projectId: project.id === "none" ? "" : project.id })}
                          onEditTask={(task) => {
                            setCreating(false);
                            setFormDefaults({ personId: task.assigned_person_id ?? group.person.id, projectId: task.project_id ?? "" });
                            setEditing(task);
                          }}
                          onMoveDown={(task, index) => reorderTask.mutate({ taskId: task.id, priorityOrder: index + 2 })}
                          onMoveUp={(task, index) => reorderTask.mutate({ taskId: task.id, priorityOrder: index })}
                          onToggle={() => setCollapsedProjects((current) => ({ ...current, [key]: !projectCollapsed }))}
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
  isCollapsed,
  isMoving,
  onToggle,
  onAddTask,
  onMoveUp,
  onMoveDown,
  onEditTask
}: {
  project: ProjectGroup;
  isCollapsed: boolean;
  isMoving: boolean;
  onToggle: () => void;
  onAddTask: () => void;
  onMoveUp: (task: Task, index: number) => void;
  onMoveDown: (task: Task, index: number) => void;
  onEditTask: (task: Task) => void;
}) {
  return (
    <div className="bg-white">
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
        <button className="flex min-w-0 items-center gap-2 text-left" onClick={onToggle} type="button">
          {isCollapsed ? <ChevronRight className="shrink-0 text-stone-500" size={17} /> : <ChevronDown className="shrink-0 text-stone-500" size={17} />}
          <FolderKanban className="shrink-0 text-mint" size={18} />
          <span className="min-w-0">
            <span className="block truncate text-sm font-semibold">{project.name}</span>
            <span className="block text-xs text-stone-500">
              {project.tasks.length} tasks{project.id === "none" ? "" : ` · ${project.status}`}
            </span>
          </span>
        </button>
        <Button className="h-9 px-3" onClick={onAddTask} type="button">
          <Plus size={15} />
          Task
        </Button>
      </div>
      {!isCollapsed && (
        <div className="border-t border-stone-100">
          {project.tasks.length === 0 ? (
            <div className="px-4 py-6 text-sm text-stone-500">No tasks here yet.</div>
          ) : (
            <div className="divide-y divide-stone-100">
              {project.tasks.map((task, index) => (
                <TaskRow
                  canMoveDown={index < project.tasks.length - 1 && project.id !== "none"}
                  canMoveUp={index > 0 && project.id !== "none"}
                  index={index}
                  isMoving={isMoving}
                  key={task.id}
                  onEdit={() => onEditTask(task)}
                  onMoveDown={() => onMoveDown(task, index)}
                  onMoveUp={() => onMoveUp(task, index)}
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
