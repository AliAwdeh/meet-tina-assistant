import { clsx } from "clsx";
import { X } from "lucide-react";
import { useEffect } from "react";
import type { ButtonHTMLAttributes, PropsWithChildren } from "react";

export const secondaryButtonClass =
  "border-mint/70 bg-mint/15 text-ink hover:border-mint hover:bg-mint/25 focus:ring-mint";

export const editButtonClass =
  "inline-flex h-9 items-center gap-1 rounded-md border border-amber/70 bg-amber/15 px-3 text-sm font-semibold text-ink shadow-sm transition hover:border-amber hover:bg-amber/25 focus:outline-none focus:ring-2 focus:ring-amber focus:ring-offset-2";

export const smallEditButtonClass =
  "inline-flex h-8 shrink-0 items-center gap-1 rounded-md border border-amber/70 bg-amber/15 px-2 text-xs font-semibold text-ink shadow-sm transition hover:border-amber hover:bg-amber/25 focus:outline-none focus:ring-2 focus:ring-amber focus:ring-offset-2";

export function Button({ children, className, ...props }: PropsWithChildren<ButtonHTMLAttributes<HTMLButtonElement>>) {
  return (
    <button
      className={clsx(
        "inline-flex h-10 items-center justify-center gap-2 rounded-md border-2 border-ink bg-mint px-4 text-sm font-semibold text-ink shadow-sm transition hover:bg-mint/80 focus:outline-none focus:ring-2 focus:ring-ink focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
        className
      )}
      {...props}
    >
      {children}
    </button>
  );
}

export function Panel({ children, className }: PropsWithChildren<{ className?: string }>) {
  return <section className={clsx("border-y border-stone-200 bg-white/72 px-5 py-4", className)}>{children}</section>;
}

export function Metric({ label, value, tone = "default" }: { label: string; value: string | number; tone?: "default" | "warn" | "bad" }) {
  const color = tone === "bad" ? "text-coral" : tone === "warn" ? "text-amber" : "text-ink";
  return (
    <div className="min-h-24 border border-stone-200 bg-white p-4">
      <div className={clsx("text-3xl font-semibold", color)}>{value}</div>
      <div className="mt-2 text-sm text-stone-600">{label}</div>
    </div>
  );
}

export function Notice({ title, children }: PropsWithChildren<{ title: string }>) {
  return (
    <div className="border border-amber/40 bg-amber/10 px-4 py-3 text-sm text-ink">
      <div className="font-semibold">{title}</div>
      <div className="mt-1 text-stone-600">{children}</div>
    </div>
  );
}

export function LoadingPanel({ label = "Loading" }: { label?: string }) {
  return <div className="rounded-lg border border-stone-200 bg-white px-4 py-10 text-center text-sm text-stone-500">{label}</div>;
}

/** Bottom sheet on phones, centred modal from `sm` up. */
export function Sheet({
  title,
  description,
  onClose,
  children
}: PropsWithChildren<{ title: string; description?: string; onClose: () => void }>) {
  useEffect(() => {
    document.body.classList.add("app-locked");
    return () => document.body.classList.remove("app-locked");
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center sm:items-center sm:p-5">
      <button aria-label="Close" className="absolute inset-0 bg-ink/40 backdrop-blur-sm" onClick={onClose} type="button" />
      <div
        className="relative flex max-h-[92dvh] w-full flex-col rounded-t-2xl bg-white shadow-2xl sm:max-h-[86dvh] sm:max-w-2xl sm:rounded-2xl"
        role="dialog"
        aria-modal="true"
      >
        <div aria-hidden className="mx-auto mt-2.5 h-1 w-10 shrink-0 rounded-full bg-stone-300 sm:hidden" />
        <div className="flex shrink-0 items-start justify-between gap-3 border-b border-stone-100 px-4 pb-3 pt-3 sm:px-5 sm:pt-4">
          <div className="min-w-0">
            <h3 className="text-base font-semibold">{title}</h3>
            {description && <p className="mt-0.5 text-sm text-stone-500">{description}</p>}
          </div>
          <button
            aria-label="Close"
            className="grid h-9 w-9 shrink-0 place-items-center rounded-lg text-stone-500 transition hover:bg-stone-100 hover:text-ink"
            onClick={onClose}
            type="button"
          >
            <X size={18} />
          </button>
        </div>
        <div
          className="app-scroll min-h-0 flex-1 overflow-y-auto px-4 pt-4 sm:px-5"
          style={{ paddingBottom: "calc(1.25rem + var(--safe-bottom))" }}
        >
          {children}
        </div>
      </div>
    </div>
  );
}
