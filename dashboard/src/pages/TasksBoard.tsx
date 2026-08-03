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
import { useEffect, useMemo, useRef, useState } from "react";
import { apiGet, apiPost, apiPostKeepalive, apiPut, errorMessage, shouldRetry } from "../api/client";
import { Button, LoadingPanel, Notice, Sheet, secondaryButtonClass } from "../components/ui";
import { useDragSort } from "../components/useDragSort";
import type { DragHandleProps } from "../components/useDragSort";
import type { Person, Project, Task } from "../types/domain";

const priorities = [
  { id: "urgent", label: "Urgent", dot: "bg-coral", tone: "border-coral/40 bg-coral/10 text-coral" },
  { id: "high", label: "High", dot: "bg-amber", tone: "border-amber/50 bg-amber/10 text-amber" },
  { id: "medium", label: "Medium", dot: "bg-mint", tone: "border-mint/50 bg-mint/10 text-ink" },
  { id: "low", label: "Low", dot: "bg-stone-400", tone: "border-stone-300 bg-stone-50 text-stone-600" }
] as const;

const sequencePriorityNames = [
  priorities[0],
  priorities[1],
  priorities[2],
  priorities[3]
] as const;
const DRAG_SCROLL_EDGE = 88;
const DRAG_SCROLL_MAX_STEP = 18;

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

type FormDefaults = { personId: string; projectId: string; compact?: boolean };
type ProjectFormDefaults = { personId: string };
type PendingNotifications = { pending: number };

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

function projectPriority(order: number) {
  const named = sequencePriorityNames[order - 1];
  if (named) return named;
  return { id: `p${order}`, label: String(order), dot: "bg-stone-500", tone: "border-stone-300 bg-stone-50 text-stone-700" };
}

function taskPriority(task: Task, order: number, isProjectTask: boolean) {
  if (isProjectTask) return projectPriority(order);
  return priorities.find((item) => item.id === task.priority) ?? priorities[2];
}

function orderedProjectTasks(tasks: Task[]) {
  return tasks.filter((task) => (task.priority_order ?? 0) > 0);
}

function unprioritizedProjectTasks(tasks: Task[]) {
  return tasks
    .filter((task) => (task.priority_order ?? 0) <= 0)
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
}

export function TasksBoard() {
  const queryClient = useQueryClient();
  const [openPeople, setOpenPeople] = useState<Record<string, boolean>>({});
  const [openProjects, setOpenProjects] = useState<Record<string, boolean>>({});
  const [editing, setEditing] = useState<Task | null>(null);
  const [creating, setCreating] = useState(false);
  const [creatingProject, setCreatingProject] = useState(false);
  const [formDefaults, setFormDefaults] = useState<FormDefaults>({ personId: "", projectId: "" });
  const [projectDefaults, setProjectDefaults] = useState<ProjectFormDefaults>({ personId: "" });
  const [hasPendingNotificationChanges, setHasPendingNotificationChanges] = useState(false);
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
  const pendingNotifications = useQuery({
    queryKey: ["task-notifications"],
    queryFn: () => apiGet<PendingNotifications>("/api/dashboard/tasks/notifications/pending"),
    retry: shouldRetry
  });
  const saveTask = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      editing ? apiPut<Task>(`/api/dashboard/tasks/${editing.id}`, payload) : apiPost<Task>("/api/dashboard/tasks", payload),
    onSuccess: () => {
      setEditing(null);
      setCreating(false);
      setHasPendingNotificationChanges(true);
      void queryClient.invalidateQueries({ queryKey: ["Tasks"] });
      void queryClient.invalidateQueries({ queryKey: ["summary"] });
      void queryClient.invalidateQueries({ queryKey: ["task-notifications"] });
    }
  });
  const saveProject = useMutation({
    mutationFn: (payload: Record<string, unknown>) => apiPost<Project>("/api/dashboard/projects", payload),
    onSuccess: (project) => {
      setCreatingProject(false);
      setOpenPeople((current) => ({ ...current, [project.person_id]: true }));
      setOpenProjects((current) => ({ ...current, [groupKey(project.person_id, project.id)]: true }));
      void queryClient.invalidateQueries({ queryKey: ["Projects"] });
      void queryClient.invalidateQueries({ queryKey: ["summary"] });
    }
  });
  const reorderTask = useMutation({
    mutationFn: ({ taskId, priorityOrder }: { taskId: string; priorityOrder: number }) =>
      apiPut<Task>(`/api/dashboard/tasks/${taskId}`, { priority_order: priorityOrder }),
    onSuccess: () => {
      setHasPendingNotificationChanges(true);
      void queryClient.invalidateQueries({ queryKey: ["Tasks"] });
      void queryClient.invalidateQueries({ queryKey: ["summary"] });
      void queryClient.invalidateQueries({ queryKey: ["task-notifications"] });
    }
  });
  const notify = useMutation({
    mutationFn: () => apiPost<{ sent: number; pending: number }>("/api/dashboard/tasks/notifications/flush", {}),
    onSuccess: () => {
      setHasPendingNotificationChanges(false);
      void queryClient.invalidateQueries({ queryKey: ["task-notifications"] });
    }
  });

  useEffect(() => {
    const flush = () => {
      if (hasPendingNotificationChanges || (pendingNotifications.data?.pending ?? 0) > 0) {
        apiPostKeepalive("/api/dashboard/tasks/notifications/flush", {});
        setHasPendingNotificationChanges(false);
      }
    };
    const flushWhenHidden = () => {
      if (document.visibilityState === "hidden") flush();
      if (document.visibilityState === "visible") {
        void queryClient.invalidateQueries({ queryKey: ["task-notifications"] });
      }
    };
    window.addEventListener("pagehide", flush);
    document.addEventListener("visibilitychange", flushWhenHidden);
    return () => {
      window.removeEventListener("pagehide", flush);
      document.removeEventListener("visibilitychange", flushWhenHidden);
    };
  }, [hasPendingNotificationChanges, pendingNotifications.data?.pending, queryClient]);

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
                  const projectPriorityLabel =
                    task.project_id && task.priority_order ? projectPriority(task.priority_order).label : task.project_id ? "unprioritized" : undefined;
                  const haystack = [
                    person.full_name,
                    project.name,
                    task.title,
                    task.description,
                    task.status,
                    task.priority,
                    projectPriorityLabel
                  ]
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

  const formPeople = (people.data ?? []).filter((person) => person.active);
  const totalTasks = groups.reduce((count, group) => count + group.taskCount, 0);
  const totalProjects = groups.reduce((count, group) => count + group.projects.filter((project) => project.id !== "none").length, 0);
  const defaultOpenPersonId = groups[0]?.person.id;
  const defaultOpenProjectKey = groups[0]
    ? groupKey(groups[0].person.id, groups[0].projects.find((project) => project.tasks.length > 0)?.id ?? groups[0].projects[0]?.id ?? "none")
    : undefined;
  const searchActive = search.trim().length > 0;

  const openCreateForm = (defaults: FormDefaults) => {
    setEditing(null);
    setCreatingProject(false);
    setFormDefaults(defaults);
    setCreating(true);
  };

  const openCreateProject = (defaults: ProjectFormDefaults) => {
    setCreating(false);
    setEditing(null);
    setProjectDefaults(defaults);
    setCreatingProject(true);
  };

  if (tasks.isLoading || projects.isLoading || people.isLoading) return <LoadingPanel label="Loading your board" />;

  return (
    <div className="space-y-4 sm:space-y-6">
      <div className="overflow-hidden rounded-lg border border-stone-200/70 bg-white shadow-sm">
        <div className="border-l-4 border-mint p-5 sm:p-8">
          <p className="text-xs font-medium text-mint sm:text-sm">Meet Tina</p>
          <h1 className="mt-1.5 text-[26px] font-semibold leading-tight tracking-tight sm:mt-2 sm:text-3xl md:text-4xl">
            Hello Sami, what are we doing today?
          </h1>
          <p className="mt-2.5 max-w-xl text-sm text-stone-500 sm:mt-3">
            {groups.length} {groups.length === 1 ? "person" : "people"} · {totalProjects} {totalProjects === 1 ? "project" : "projects"} ·{" "}
            {totalTasks} {totalTasks === 1 ? "task" : "tasks"} in play.
          </p>
        </div>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <label className="flex h-12 min-w-0 flex-1 items-center gap-2.5 rounded-lg border border-stone-200/80 bg-white px-4 shadow-sm transition focus-within:border-ink">
          <Search className="shrink-0 text-stone-400" size={18} />
          {/* 16px on phones: anything smaller makes iOS zoom the page on focus. */}
          <input
            className="h-10 w-full bg-transparent text-base outline-none placeholder:text-stone-400 sm:text-sm"
            placeholder="Search people, projects, tasks"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          {search && (
            <button
              aria-label="Clear search"
              className="grid h-8 w-8 shrink-0 place-items-center rounded-md text-stone-400 transition hover:bg-stone-100 hover:text-ink"
              onClick={() => setSearch("")}
              type="button"
            >
              <X size={16} />
            </button>
          )}
        </label>
        {/* Phones get these as a fixed bottom bar instead. */}
        <div className="hidden gap-2 sm:flex">
          <Button className="h-12 rounded-lg" onClick={() => openCreateForm({ personId: formPeople[0]?.id ?? "", projectId: "" })}>
            <Plus size={17} />
            New task
          </Button>
          <Button
            className={`${secondaryButtonClass} h-12 rounded-lg`}
            disabled={(!hasPendingNotificationChanges && (pendingNotifications.data?.pending ?? 0) === 0) || notify.isPending}
            onClick={() => notify.mutate()}
          >
            <Mail size={17} />
            Notify{pendingNotifications.data?.pending ? ` (${pendingNotifications.data.pending})` : ""}
          </Button>
          <Button className={`${secondaryButtonClass} h-12 rounded-lg`} onClick={() => openCreateProject({ personId: formPeople[0]?.id ?? "" })}>
            <FolderKanban size={17} />
            New project
          </Button>
        </div>
      </div>

      {(tasks.isError || projects.isError || people.isError || saveTask.isError || saveProject.isError || reorderTask.isError || notify.isError) && (
        <Notice title="Something needs attention">
          {tasks.isError
            ? errorMessage(tasks.error)
            : projects.isError
              ? errorMessage(projects.error)
              : people.isError
                ? errorMessage(people.error)
                : saveTask.isError
                  ? errorMessage(saveTask.error)
                  : saveProject.isError
                    ? errorMessage(saveProject.error)
                    : reorderTask.isError
                      ? errorMessage(reorderTask.error)
                      : errorMessage(notify.error)}
        </Notice>
      )}

      {(creating || editing) && (
        <Sheet
          description={
            creating && formDefaults.compact
              ? "Create it here with a title and description."
              : "Assign it to a person, optionally place it inside a project, and set its priority position."
          }
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
          title={editing ? "Edit task" : "New task"}
        >
          <TaskForm
            key={`${editing?.id ?? "new"}:${formDefaults.personId}:${formDefaults.projectId}:${formDefaults.compact ? "compact" : "full"}`}
            initial={editing}
            isSaving={saveTask.isPending}
            people={formPeople}
            projects={projects.data ?? []}
            defaultPersonId={formDefaults.personId}
            defaultProjectId={formDefaults.projectId}
            compactCreate={Boolean(formDefaults.compact)}
            onCancel={() => {
              setCreating(false);
              setEditing(null);
            }}
            onSave={(payload) => saveTask.mutate(payload)}
          />
        </Sheet>
      )}

      {creatingProject && (
        <Sheet
          description="Create it under a person, then add tasks inside it."
          onClose={() => setCreatingProject(false)}
          title="New project"
        >
          <ProjectForm
            key={projectDefaults.personId}
            defaultPersonId={projectDefaults.personId}
            isSaving={saveProject.isPending}
            people={formPeople}
            onCancel={() => setCreatingProject(false)}
            onSave={(payload) => saveProject.mutate(payload)}
          />
        </Sheet>
      )}

      {groups.length === 0 ? (
        <div className="rounded-lg border border-dashed border-stone-300 bg-white px-5 py-14 text-center text-sm text-stone-500">
          No people or tasks match the current search.
        </div>
      ) : (
        <div className="space-y-3">
          {groups.map((group) => {
            const personOpen = searchActive || (openPeople[group.person.id] ?? group.person.id === defaultOpenPersonId);
            const detail = personDetail(group.person);
            const DetailIcon = detail.icon;
            const projectCount = group.projects.filter((project) => project.id !== "none").length;
            return (
              <section className="overflow-hidden rounded-lg border border-stone-200/80 bg-white shadow-sm" key={group.person.id}>
                <div className="flex items-center gap-1.5 px-2.5 py-2.5 transition hover:bg-stone-50/70 sm:gap-2 sm:px-4 sm:py-4">
                  <button
                    className="flex min-h-11 min-w-0 flex-1 items-center gap-2.5 text-left sm:gap-4"
                    onClick={() => setOpenPeople((current) => ({ ...current, [group.person.id]: !personOpen }))}
                    type="button"
                  >
                    <ChevronRight className={`shrink-0 text-stone-400 transition-transform ${personOpen ? "rotate-90" : ""}`} size={18} />
                    <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-mint/15 text-xs font-bold text-ink sm:h-11 sm:w-11 sm:text-sm">
                      {initials(group.person.full_name)}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[15px] font-semibold sm:text-base">{group.person.full_name}</span>
                      <span className="mt-0.5 flex min-w-0 items-center gap-1.5 text-xs text-stone-500 sm:text-sm">
                        <DetailIcon className="shrink-0" size={13} />
                        <span className="truncate">{detail.value}</span>
                      </span>
                    </span>
                  </button>
                  <span className="shrink-0 rounded-md bg-stone-100 px-2 py-1 text-[11px] font-medium text-stone-600 sm:px-3 sm:text-xs">
                    <span className="sm:hidden">
                      {projectCount}p · {group.taskCount}t
                    </span>
                    <span className="hidden sm:inline">
                      {projectCount} {projectCount === 1 ? "project" : "projects"} · {group.taskCount} {group.taskCount === 1 ? "task" : "tasks"}
                    </span>
                  </span>
                  <button
                    aria-label={`New project for ${group.person.full_name}`}
                    className="inline-flex h-10 w-10 shrink-0 items-center justify-center gap-1 rounded-md border border-stone-200 text-xs font-medium text-stone-600 transition hover:border-ink hover:text-ink sm:h-9 sm:w-auto sm:px-2.5"
                    onClick={() => openCreateProject({ personId: group.person.id })}
                    type="button"
                  >
                    <Plus size={15} />
                    <span className="hidden sm:inline">Project</span>
                  </button>
                </div>
                {personOpen && (
                  <div className="space-y-1.5 border-t border-stone-100 bg-stone-50/40 px-2 py-2 sm:px-3 sm:py-3">
                    {group.projects.map((project) => {
                      const key = groupKey(group.person.id, project.id);
                      const projectOpen = searchActive || (openProjects[key] ?? key === defaultOpenProjectKey);
                      return (
                        <ProjectSection
                          isMoving={reorderTask.isPending}
                          isOpen={projectOpen}
                          key={key}
                          onAddTask={() =>
                            openCreateForm({ personId: group.person.id, projectId: project.id === "none" ? "" : project.id, compact: true })
                          }
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

      {/* Clears the fixed mobile action bar. */}
      <div aria-hidden className="h-20 sm:hidden" />

      <div
        className="fixed inset-x-0 bottom-0 z-30 flex gap-2 border-t border-stone-200/70 bg-[#f7f4ee]/92 px-4 pt-3 backdrop-blur sm:hidden"
        style={{ paddingBottom: "calc(0.75rem + var(--safe-bottom))" }}
      >
        <Button className="h-12 flex-1 rounded-lg" onClick={() => openCreateForm({ personId: formPeople[0]?.id ?? "", projectId: "" })}>
          <Plus size={17} />
          New task
        </Button>
        <Button
          className={`${secondaryButtonClass} h-12 flex-1 rounded-lg`}
          disabled={(!hasPendingNotificationChanges && (pendingNotifications.data?.pending ?? 0) === 0) || notify.isPending}
          onClick={() => notify.mutate()}
        >
          <Mail size={17} />
          Notify{pendingNotifications.data?.pending ? ` (${pendingNotifications.data.pending})` : ""}
        </Button>
        <Button
          className={`${secondaryButtonClass} h-12 flex-1 rounded-lg`}
          onClick={() => openCreateProject({ personId: formPeople[0]?.id ?? "" })}
        >
          <FolderKanban size={17} />
          New project
        </Button>
      </div>
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
  const isRealProject = project.id !== "none";
  const rankedProjectTasks = isRealProject ? orderedProjectTasks(project.tasks) : project.tasks;
  const unrankedProjectTasks = isRealProject ? unprioritizedProjectTasks(project.tasks) : [];
  // Local optimistic order so drag feels instant; resync when server data changes.
  const [order, setOrder] = useState<Task[]>(rankedProjectTasks);
  const [locallyRankedIds, setLocallyRankedIds] = useState<Set<string>>(() => new Set());
  const [pendingDrag, setPendingDrag] = useState<{ taskId: string; dy: number } | null>(null);
  const pendingRowRefs = useRef<Record<string, HTMLElement | null>>({});
  useEffect(() => {
    setOrder(rankedProjectTasks);
    setLocallyRankedIds(new Set());
    setPendingDrag(null);
  }, [project.tasks]);

  const floatingTasks = unrankedProjectTasks.filter((task) => !locallyRankedIds.has(task.id));
  const canReorder = isRealProject && order.length > 1;
  const sort = useDragSort({
    itemCount: order.length,
    enabled: canReorder && !isMoving,
    onCommit: (from, to) => {
      const next = [...order];
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      setOrder(next);
      onReorder(moved.id, to + 1);
    }
  });

  const topPriority = order.length > 0 && isRealProject ? projectPriority(1) : null;
  const taskCount = order.length + floatingTasks.length;

  const dropIndexFromPointer = (clientY: number) => {
    let index = 0;
    const rows = order.map((_task, rowIndex) => sort.containerRef.current?.children.item(rowIndex) as HTMLElement | null);
    for (const row of rows) {
      if (!row) continue;
      const rect = row.getBoundingClientRect();
      if (clientY > rect.top + rect.height / 2) index += 1;
    }
    return index;
  };

  const isInsidePriorityList = (clientX: number, clientY: number) => {
    const target = sort.containerRef.current;
    if (!target) return false;
    const rect = target.getBoundingClientRect();
    const verticalPadding = order.length === 0 ? 64 : 24;
    return (
      clientX >= rect.left &&
      clientX <= rect.right &&
      clientY >= rect.top - verticalPadding &&
      clientY <= rect.bottom + verticalPadding
    );
  };

  const pendingHandleProps = (task: Task): DragHandleProps => ({
    onPointerDown: (event) => {
      if (!isRealProject || isMoving) return;
      if (event.pointerType === "mouse" && event.button !== 0) return;
      const row = pendingRowRefs.current[task.id];
      if (!row) return;
      event.preventDefault();
      const startY = event.clientY;
      const startScrollY = window.scrollY;
      const pointerId = event.pointerId;
      const handle = event.currentTarget;
      let latestClientY = event.clientY;
      let scrollFrame: number | null = null;
      const setDragPosition = () => {
        setPendingDrag({ taskId: task.id, dy: latestClientY - startY + window.scrollY - startScrollY });
      };
      const scrollTick = () => {
        const viewport = window.innerHeight;
        let step = 0;
        if (latestClientY < DRAG_SCROLL_EDGE) {
          step = -Math.ceil(((DRAG_SCROLL_EDGE - latestClientY) / DRAG_SCROLL_EDGE) * DRAG_SCROLL_MAX_STEP);
        } else if (latestClientY > viewport - DRAG_SCROLL_EDGE) {
          step = Math.ceil(((latestClientY - (viewport - DRAG_SCROLL_EDGE)) / DRAG_SCROLL_EDGE) * DRAG_SCROLL_MAX_STEP);
        }
        if (step !== 0) {
          window.scrollBy(0, step);
          setDragPosition();
        }
        scrollFrame = requestAnimationFrame(scrollTick);
      };
      try {
        handle.setPointerCapture(pointerId);
      } catch {
        // Window listeners below keep the drag alive even if capture is unavailable.
      }
      document.body.classList.add("app-dragging");
      setDragPosition();
      scrollFrame = requestAnimationFrame(scrollTick);

      const finish = (commit: boolean, clientX: number, clientY: number) => {
        if (scrollFrame !== null) cancelAnimationFrame(scrollFrame);
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        window.removeEventListener("pointercancel", onCancel);
        try {
          handle.releasePointerCapture(pointerId);
        } catch {
          // Already released.
        }
        document.body.classList.remove("app-dragging");
        setPendingDrag(null);
        if (!commit || !isInsidePriorityList(clientX, clientY)) return;
        const toIndex = dropIndexFromPointer(clientY);
        const next = [...order];
        next.splice(toIndex, 0, task);
        setOrder(next);
        setLocallyRankedIds((current) => new Set(current).add(task.id));
        onReorder(task.id, toIndex + 1);
      };

      const onMove = (moveEvent: PointerEvent) => {
        if (moveEvent.pointerId !== pointerId) return;
        moveEvent.preventDefault();
        latestClientY = moveEvent.clientY;
        setDragPosition();
      };
      const onUp = (upEvent: PointerEvent) => {
        if (upEvent.pointerId !== pointerId) return;
        finish(true, upEvent.clientX, upEvent.clientY);
      };
      const onCancel = (cancelEvent: PointerEvent) => {
        if (cancelEvent.pointerId !== pointerId) return;
        finish(false, cancelEvent.clientX, cancelEvent.clientY);
      };

      window.addEventListener("pointermove", onMove, { passive: false });
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onCancel);
      navigator.vibrate?.(8);
    }
  });

  return (
    <div className="overflow-hidden rounded-lg border border-stone-200/70 bg-white">
      <div className="flex items-center gap-1.5 px-2 py-2 sm:gap-2 sm:px-3 sm:py-2.5">
        <button className="flex min-h-11 min-w-0 flex-1 items-center gap-2 text-left sm:gap-3" onClick={onToggle} type="button">
          <ChevronRight className={`shrink-0 text-stone-400 transition-transform ${isOpen ? "rotate-90" : ""}`} size={16} />
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-md bg-mint/10 text-mint">
            <FolderKanban size={16} />
          </span>
          <span className="min-w-0">
            <span className="flex min-w-0 items-center gap-2">
              <span className="truncate text-sm font-semibold">{project.name}</span>
              {topPriority && (
                <span className="hidden shrink-0 rounded-md border border-amber/50 bg-amber/10 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-amber sm:inline-block">
                  {topPriority.label}
                </span>
              )}
            </span>
            <span className="mt-0.5 block truncate text-xs text-stone-400">
              {taskCount} {taskCount === 1 ? "task" : "tasks"}
              {isRealProject ? ` · ${project.status}` : ""}
            </span>
          </span>
        </button>
        <button
          aria-label={`New task in ${project.name}`}
          className="inline-flex h-10 w-10 shrink-0 items-center justify-center gap-1 rounded-md border border-stone-200 text-xs font-medium text-stone-600 transition hover:border-ink hover:text-ink sm:h-8 sm:w-auto sm:px-2.5"
          onClick={onAddTask}
          type="button"
        >
          <Plus size={15} />
          <span className="hidden sm:inline">Task</span>
        </button>
      </div>
      {isOpen && (
        <div className="border-t border-stone-100 bg-stone-50/50 p-2 sm:p-2.5">
          {taskCount === 0 ? (
            <div className="rounded-md border border-dashed border-stone-300 bg-white px-4 py-6 text-center text-sm text-stone-400">
              No tasks here yet.
            </div>
          ) : (
            <div className="space-y-3">
              {floatingTasks.length > 0 && (
                <div className="rounded-lg border border-dashed border-mint/60 bg-mint/5 p-2">
                  <div className="mb-2 flex items-center justify-between gap-2 px-1">
                    <span className="text-xs font-semibold text-ink">Unprioritized</span>
                    <span className="text-[11px] text-stone-500">Drag into the list below</span>
                  </div>
                  <div className="space-y-2">
                    {floatingTasks.map((task) => (
                      <TaskRow
                        canReorder
                        handleProps={pendingHandleProps(task)}
                        innerRef={(el) => {
                          pendingRowRefs.current[task.id] = el;
                        }}
                        isDragging={pendingDrag?.taskId === task.id}
                        isSorting={Boolean(pendingDrag)}
                        key={task.id}
                        onEdit={() => onEditTask(task)}
                        position={-1}
                        task={task}
                        transform={pendingDrag?.taskId === task.id ? `translate3d(0, ${pendingDrag.dy}px, 0)` : undefined}
                        unprioritized
                        useProjectPriority={false}
                      />
                    ))}
                  </div>
                </div>
              )}
              {(canReorder || floatingTasks.length > 0) && (
                <p className="px-1 pb-2 text-[11px] text-stone-400">
                  Drag <GripVertical className="inline align-text-bottom" size={12} /> to change priority.
                </p>
              )}
              {/* `relative` makes this the offsetParent the drag hook measures against. */}
              <div
                className={`relative space-y-2 rounded-lg ${
                  order.length === 0 && floatingTasks.length > 0 ? "border border-dashed border-stone-300 bg-white/80 px-3 py-6" : ""
                }`}
                ref={sort.containerRef}
              >
                {order.length === 0 && floatingTasks.length > 0 ? (
                  <div className="text-center text-sm text-stone-400">Drop here to set priority 1.</div>
                ) : (
                  order.map((task, index) => (
                    <TaskRow
                      canReorder={canReorder}
                      handleProps={sort.handleProps(index)}
                      innerRef={sort.registerRow(index)}
                      isDragging={sort.draggingIndex === index}
                      isSorting={sort.isDragging}
                      key={task.id}
                      onEdit={() => onEditTask(task)}
                      position={sort.positionOf(index)}
                      task={task}
                      transform={sort.transformFor(index)}
                      useProjectPriority={isRealProject}
                    />
                  ))
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TaskRow({
  task,
  position,
  canReorder,
  isDragging,
  isSorting,
  transform,
  innerRef,
  handleProps,
  onEdit,
  unprioritized = false,
  useProjectPriority
}: {
  task: Task;
  position: number;
  canReorder: boolean;
  isDragging: boolean;
  isSorting: boolean;
  transform: string | undefined;
  innerRef: (el: HTMLElement | null) => void;
  handleProps: DragHandleProps;
  onEdit: () => void;
  unprioritized?: boolean;
  useProjectPriority: boolean;
}) {
  const priority = unprioritized
    ? { label: "No priority", dot: "bg-stone-300", tone: "border-stone-300 bg-white text-stone-500" }
    : taskPriority(task, position + 1, useProjectPriority);
  return (
    <article
      className={`group relative flex items-start gap-2 rounded-lg border bg-white px-2 py-2.5 shadow-sm sm:gap-3 sm:px-3 ${
        isDragging ? "border-mint shadow-lg ring-2 ring-mint/40" : "border-stone-200/80 hover:border-stone-300"
      }`}
      ref={innerRef}
      style={{
        transform,
        // The dragged row tracks the finger exactly; the others glide into their new slot.
        transition: isDragging ? "none" : "transform 180ms cubic-bezier(0.2, 0, 0, 1)",
        zIndex: isDragging ? 20 : undefined,
        touchAction: isSorting ? "none" : undefined
      }}
    >
      {canReorder ? (
        <span
          aria-label="Drag to change priority"
          className="drag-handle -ml-1 grid h-11 w-8 shrink-0 cursor-grab place-items-center rounded-md text-stone-300 transition hover:text-stone-500 active:cursor-grabbing sm:h-9 sm:w-7"
          role="button"
          tabIndex={-1}
          {...handleProps}
        >
          <GripVertical size={18} />
        </span>
      ) : (
        <span aria-hidden className="w-1 shrink-0 sm:w-2" />
      )}
      <span className="mt-0.5 grid h-7 min-w-7 shrink-0 place-items-center rounded-md bg-ink px-1.5 text-[11px] font-bold text-white sm:h-8 sm:min-w-8 sm:px-2 sm:text-xs">
        {unprioritized ? "New" : useProjectPriority ? priority.label : position + 1}
      </span>
      <div className="min-w-0 flex-1">
        <h4 className="break-words text-sm font-semibold leading-5 text-ink">{task.title}</h4>
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          <span className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-semibold ${priority.tone}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${priority.dot}`} />
            {unprioritized ? "No priority" : useProjectPriority ? `Priority ${priority.label}` : priority.label}
          </span>
          {task.status !== "open" && (
            <span className="inline-flex items-center gap-1 rounded-md border border-stone-200 bg-stone-50 px-2 py-0.5 text-[11px] text-stone-500">
              <AlertCircle size={11} />
              {task.status}
            </span>
          )}
          {task.due_date && (
            <span className="inline-flex items-center gap-1 text-[11px] text-stone-400">
              <CalendarDays size={12} />
              {new Date(task.due_date).toLocaleDateString()}
            </span>
          )}
        </div>
        {task.description && <p className="mt-1 line-clamp-2 text-xs text-stone-400">{task.description}</p>}
      </div>
      <button
        aria-label="Edit task"
        className="grid h-10 w-10 shrink-0 place-items-center rounded-md text-stone-400 transition hover:bg-stone-100 hover:text-ink sm:h-9 sm:w-9 sm:opacity-0 sm:group-hover:opacity-100"
        onClick={onEdit}
        type="button"
      >
        <Pencil size={15} />
      </button>
    </article>
  );
}

// text-base on phones (16px) keeps iOS from zooming the viewport when a field is focused.
const inputClass =
  "mt-1 h-11 w-full rounded-lg border border-stone-300 bg-white px-3 text-base outline-none transition focus:border-ink sm:h-10 sm:text-sm";
const textareaClass =
  "mt-1 min-h-20 w-full rounded-lg border border-stone-300 bg-white px-3 py-2 text-base outline-none transition focus:border-ink sm:text-sm";

function ProjectForm({
  isSaving,
  people,
  defaultPersonId,
  onCancel,
  onSave
}: {
  isSaving: boolean;
  people: Person[];
  defaultPersonId: string;
  onCancel: () => void;
  onSave: (payload: Record<string, unknown>) => void;
}) {
  const [form, setForm] = useState({
    person_id: defaultPersonId || people[0]?.id || "",
    name: "",
    description: "",
    status: "active"
  });
  return (
    <form
      className="grid gap-3 md:grid-cols-3"
      onSubmit={(event) => {
        event.preventDefault();
        onSave({
          person_id: form.person_id,
          name: form.name,
          description: form.description || null,
          status: form.status
        });
      }}
    >
      <label className="block text-sm font-medium">
        Person
        <select className={inputClass} required value={form.person_id} onChange={(event) => setForm({ ...form, person_id: event.target.value })}>
          {people.map((person) => (
            <option key={person.id} value={person.id}>
              {person.full_name}
            </option>
          ))}
        </select>
      </label>
      <label className="block text-sm font-medium md:col-span-2">
        Project name
        <input className={inputClass} required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
      </label>
      <label className="block text-sm font-medium">
        Status
        <select className={inputClass} value={form.status} onChange={(event) => setForm({ ...form, status: event.target.value })}>
          {["active", "paused", "completed", "cancelled"].map((status) => (
            <option key={status} value={status}>
              {status}
            </option>
          ))}
        </select>
      </label>
      <label className="block text-sm font-medium md:col-span-2">
        Description
        <textarea className={textareaClass} value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} />
      </label>
      <div className="flex items-end gap-2 pt-1 md:col-span-3">
        <Button className="h-12 flex-1 sm:h-10 sm:flex-none" disabled={isSaving || !people.length} type="submit">
          <Save size={16} />
          {isSaving ? "Saving" : "Save"}
        </Button>
        <Button
          className={`${secondaryButtonClass} h-12 flex-1 sm:h-10 sm:flex-none`}
          disabled={isSaving}
          onClick={onCancel}
          type="button"
        >
          <X size={16} />
          Cancel
        </Button>
      </div>
    </form>
  );
}

function TaskForm({
  initial,
  isSaving,
  people,
  projects,
  defaultPersonId,
  defaultProjectId,
  compactCreate,
  onCancel,
  onSave
}: {
  initial: Task | null;
  isSaving: boolean;
  people: Person[];
  projects: Project[];
  defaultPersonId: string;
  defaultProjectId: string;
  compactCreate: boolean;
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
  const simpleCreate = compactCreate && !initial;
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
      <label className={`block text-sm font-medium ${simpleCreate ? "md:col-span-3" : "md:col-span-2"}`}>
        Title
        <input className={inputClass} required value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} />
      </label>
      {!simpleCreate && (
        <>
          <label className="block text-sm font-medium">
            Priority label
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
            Project priority position
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
            <input
              className={inputClass}
              type="date"
              value={form.due_date}
              onChange={(event) => setForm({ ...form, due_date: event.target.value })}
            />
          </label>
        </>
      )}
      <label className="block text-sm font-medium md:col-span-3">
        Description
        <textarea className={textareaClass} value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} />
      </label>
      <div className="flex items-end gap-2 pt-1 md:col-span-3">
        <Button className="h-12 flex-1 sm:h-10 sm:flex-none" disabled={isSaving} type="submit">
          <Save size={16} />
          {isSaving ? "Saving" : "Save"}
        </Button>
        <Button
          className={`${secondaryButtonClass} h-12 flex-1 sm:h-10 sm:flex-none`}
          disabled={isSaving}
          onClick={onCancel}
          type="button"
        >
          <X size={16} />
          Cancel
        </Button>
      </div>
    </form>
  );
}
