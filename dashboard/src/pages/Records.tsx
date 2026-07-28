import { useQuery } from "@tanstack/react-query";
import { createColumnHelper, flexRender, getCoreRowModel, useReactTable } from "@tanstack/react-table";
import { apiGet, errorMessage, shouldRetry } from "../api/client";
import { LoadingPanel, Notice } from "../components/ui";
import type { Meeting, Person, Reminder, Task } from "../types/domain";

type DataType = Person | Task | Meeting | Reminder;

const endpoints = {
  People: "/api/dashboard/people",
  Tasks: "/api/dashboard/tasks",
  Meetings: "/api/dashboard/meetings",
  Reminders: "/api/dashboard/reminders"
} as const;

const columnsByPage: Record<keyof typeof endpoints, string[]> = {
  People: ["full_name", "company", "job_title", "email", "whatsapp_number"],
  Tasks: ["title", "status", "priority", "due_date", "assigned_person_id"],
  Meetings: ["title", "status", "start_time", "timezone", "preparation_status"],
  Reminders: ["title", "status", "trigger_time", "timezone", "delivery_channel"]
};

export function Records({ title }: { title: keyof typeof endpoints }) {
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

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-xl font-semibold">{title}</h2>
        <span className="text-sm text-stone-500">{query.isLoading ? "Loading" : `${rows.length} records`}</span>
      </div>
      {query.isError && (
        <div className="mb-4">
          <Notice title={`${title} could not load`}>{errorMessage(query.error)}</Notice>
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
