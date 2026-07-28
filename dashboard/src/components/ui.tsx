import { clsx } from "clsx";
import type { ButtonHTMLAttributes, PropsWithChildren } from "react";

export function Button({ children, className, ...props }: PropsWithChildren<ButtonHTMLAttributes<HTMLButtonElement>>) {
  return (
    <button
      className={clsx(
        "inline-flex h-9 items-center justify-center gap-2 rounded-md bg-ink px-3 text-sm font-medium text-white transition hover:bg-black disabled:opacity-50",
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
  return <div className="border border-stone-200 bg-white px-4 py-10 text-center text-sm text-stone-500">{label}</div>;
}
