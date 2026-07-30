import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CalendarDays, CheckSquare, FolderKanban, GripVertical, UserRound } from "lucide-react";
import { useMemo, useState } from "react";
import { apiGet, apiPost, errorMessage, shouldRetry } from "../api/client";
import { LoadingPanel, Notice } from "../components/ui";
import type { Project, Task } from "../types/domain";

const priorities = [
  { id: "urgent", label: "Urgent", tone: "border-coral/40 bg-coral/5 text-coral" },
  { id: "high", label: "High", tone: "border-amber/50 bg-amber/10 text-amber" },
  { id: "medium", label: "Medium", tone: "border-mint/40 bg-mint/10 text-mint" },
  { id: "low", label: "Low", tone: "border-stone-300 bg-stone-50 text-stone-600" }
] as const;

type Priority = (typeof priorities)[number]["id"];
type DragState = { taskId: string; fromPriority: string } | null;

export function TasksBoard() {
  const queryClient = useQueryClient();
  const [dragged, setDragged] = useState<DragState>(null);
  const [dropTarget, setDropTarget] = useState<string | null>(null);
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
  const updatePriority = useMutation({
    mutationFn: ({ taskId, priority }: { taskId: string; priority: Priority }) =>
      apiPost<Task>(`/api/dashboard/tasks/${taskId}/priority`, { priority }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["Tasks"] });
      void queryClient.invalidateQueries({ queryKey: ["summary"] });
    }
  });

  const projectGroups = useMemo(() => {
    const rows = tasks.data ?? [];
    const projectById = new Map((projects.data ?? []).map((project) => [project.id, project]));
    const groups = new Map<string, { id: string; name: string; owner?: string | null; tasks: Task[] }>();
    for (const task of rows) {
      const key = task.project_id ?? "none";
      const project = task.project_id ? projectById.get(task.project_id) : undefined;
      if (!groups.has(key)) {
        groups.set(key, {
          id: key,
          name: task.project_name ?? project?.name ?? "No project",
          owner: project?.person_name,
          tasks: []
        });
      }
      groups.get(key)?.tasks.push(task);
    }
    return Array.from(groups.values()).sort((a, b) => {
      if (a.id === "none") return 1;
      if (b.id === "none") return -1;
      return a.name.localeCompare(b.name);
    });
  }, [projects.data, tasks.data]);

  const onDrop = (priority: Priority) => {
    if (!dragged || dragged.fromPriority === priority || updatePriority.isPending) return;
    updatePriority.mutate({ taskId: dragged.taskId, priority });
    setDragged(null);
    setDropTarget(null);
  };

  if (tasks.isLoading || projects.isLoading) return <LoadingPanel label="Loading task board" />;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold">Tasks</h2>
          <p className="text-sm text-stone-500">Grouped by project. Drag a task into another priority lane to notify the related person.</p>
        </div>
        <div className="inline-flex h-9 items-center gap-2 border border-stone-200 bg-white px-3 text-sm text-stone-600">
          <CheckSquare size={16} />
          {(tasks.data ?? []).length} open records
        </div>
      </div>

      {(tasks.isError || projects.isError || updatePriority.isError) && (
        <Notice title="Task board needs attention">
          {tasks.isError ? errorMessage(tasks.error) : projects.isError ? errorMessage(projects.error) : errorMessage(updatePriority.error)}
        </Notice>
      )}

      {!projectGroups.length && <LoadingPanel label="No tasks yet" />}

      {projectGroups.map((project) => (
        <section className="border-y border-stone-200 bg-white/80 py-4" key={project.id}>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2 px-4">
            <div className="flex min-w-0 items-center gap-2">
              <FolderKanban className="shrink-0 text-mint" size={18} />
              <h3 className="truncate text-base font-semibold">{project.name}</h3>
            </div>
            {project.owner && (
              <span className="inline-flex items-center gap-1 text-sm text-stone-500">
                <UserRound size={15} />
                {project.owner}
              </span>
            )}
          </div>

          <div className="grid gap-3 px-4 xl:grid-cols-4">
            {priorities.map((priority) => {
              const rows = project.tasks.filter((task) => task.priority === priority.id);
              const isTarget = dropTarget === `${project.id}:${priority.id}`;
              return (
                <div
                  className={`min-h-44 border bg-white transition ${isTarget ? "border-ink" : "border-stone-200"}`}
                  key={priority.id}
                  onDragLeave={() => setDropTarget(null)}
                  onDragOver={(event) => {
                    event.preventDefault();
                    setDropTarget(`${project.id}:${priority.id}`);
                  }}
                  onDrop={() => onDrop(priority.id)}
                >
                  <div className={`flex h-10 items-center justify-between border-b px-3 text-sm font-medium ${priority.tone}`}>
                    <span>{priority.label}</span>
                    <span>{rows.length}</span>
                  </div>
                  <div className="space-y-2 p-2">
                    {rows.map((task) => (
                      <TaskTile key={task.id} task={task} onDragStart={() => setDragged({ taskId: task.id, fromPriority: task.priority })} />
                    ))}
                    {!rows.length && <div className="grid h-20 place-items-center text-xs text-stone-400">Drop here</div>}
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

function TaskTile({ task, onDragStart }: { task: Task; onDragStart: () => void }) {
  return (
    <article
      className="min-h-28 cursor-grab border border-stone-200 bg-[#fffdf8] p-3 shadow-sm active:cursor-grabbing"
      draggable
      onDragStart={onDragStart}
    >
      <div className="flex items-start gap-2">
        <GripVertical className="mt-0.5 shrink-0 text-stone-400" size={16} />
        <div className="min-w-0 flex-1">
          <h4 className="line-clamp-2 text-sm font-semibold leading-5">{task.title}</h4>
          {task.assigned_person_name && (
            <div className="mt-2 flex items-center gap-1 text-xs text-stone-500">
              <UserRound size={13} />
              <span className="truncate">{task.assigned_person_name}</span>
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
