import { useCallback, useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

/**
 * Pointer-event based list sorting.
 *
 * HTML5 drag-and-drop (draggable / onDragStart) never fires on touch devices, so
 * reordering is driven by pointer events instead: they cover mouse, touch and pen
 * with one code path. The handle carries `touch-action: none` so a drag gesture
 * that starts there is ours and does not scroll the page.
 *
 * Row geometry is measured once per drag, relative to the container (offsetTop),
 * so auto-scrolling mid-drag cannot invalidate it.
 */

type RowBox = { top: number; height: number };

/** Props spread onto the element that starts a drag. */
export type DragHandleProps = {
  onPointerDown: (event: ReactPointerEvent<HTMLElement>) => void;
};

type DragState = {
  fromIndex: number;
  toIndex: number;
  dy: number;
  slot: number;
};

type Session = {
  pointerId: number;
  handle: HTMLElement;
  fromIndex: number;
  toIndex: number;
  grabOffset: number;
  boxes: RowBox[];
  slot: number;
  height: number;
  clientY: number;
  raf: number | null;
  detach: () => void;
};

/** Distance from the viewport edge where auto-scroll starts, and its top speed. */
const EDGE = 88;
const MAX_STEP = 18;

export function useDragSort({
  itemCount,
  enabled,
  onCommit
}: {
  itemCount: number;
  enabled: boolean;
  onCommit: (fromIndex: number, toIndex: number) => void;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const rowRefs = useRef<Array<HTMLElement | null>>([]);
  const session = useRef<Session | null>(null);
  const [drag, setDrag] = useState<DragState | null>(null);

  // Kept in a ref so the pointer handlers never go stale mid-drag.
  const commitRef = useRef(onCommit);
  commitRef.current = onCommit;

  const registerRow = useCallback(
    (index: number) => (el: HTMLElement | null) => {
      rowRefs.current[index] = el;
    },
    []
  );

  const measure = useCallback(() => {
    const active = session.current;
    const container = containerRef.current;
    if (!active || !container) return;

    const containerTop = container.getBoundingClientRect().top;
    const draggedTop = active.clientY - containerTop - active.grabOffset;
    const centre = draggedTop + active.height / 2;

    // Insertion index = how many other rows have their midpoint above our centre.
    let toIndex = 0;
    for (let i = 0; i < active.boxes.length; i += 1) {
      if (i === active.fromIndex) continue;
      const box = active.boxes[i];
      if (centre > box.top + box.height / 2) toIndex += 1;
    }

    active.toIndex = toIndex;
    setDrag({
      fromIndex: active.fromIndex,
      toIndex,
      dy: draggedTop - active.boxes[active.fromIndex].top,
      slot: active.slot
    });
  }, []);

  const tick = useCallback(() => {
    const active = session.current;
    if (!active) return;

    const viewport = window.innerHeight;
    let step = 0;
    if (active.clientY < EDGE) {
      step = -Math.ceil(((EDGE - active.clientY) / EDGE) * MAX_STEP);
    } else if (active.clientY > viewport - EDGE) {
      step = Math.ceil(((active.clientY - (viewport - EDGE)) / EDGE) * MAX_STEP);
    }
    if (step !== 0) window.scrollBy(0, step);

    measure();
    active.raf = requestAnimationFrame(tick);
  }, [measure]);

  const finish = useCallback((commit: boolean) => {
    const active = session.current;
    if (!active) return;

    if (active.raf !== null) cancelAnimationFrame(active.raf);
    active.detach();
    try {
      active.handle.releasePointerCapture(active.pointerId);
    } catch {
      // Already released (pointercancel, element unmounted) — nothing to do.
    }
    document.body.classList.remove("app-dragging");

    session.current = null;
    setDrag(null);
    if (commit && active.toIndex !== active.fromIndex) {
      commitRef.current(active.fromIndex, active.toIndex);
    }
  }, []);

  useEffect(
    () => () => {
      const active = session.current;
      if (active?.raf != null) cancelAnimationFrame(active.raf);
      active?.detach();
      document.body.classList.remove("app-dragging");
    },
    []
  );

  const onPointerDown = useCallback(
    (index: number) => (event: ReactPointerEvent<HTMLElement>) => {
      if (!enabled || session.current) return;
      if (event.pointerType === "mouse" && event.button !== 0) return;

      const row = rowRefs.current[index];
      const container = containerRef.current;
      if (!row || !container) return;

      event.preventDefault();

      const boxes: RowBox[] = [];
      for (let i = 0; i < itemCount; i += 1) {
        const el = rowRefs.current[i];
        boxes.push({ top: el?.offsetTop ?? 0, height: el?.offsetHeight ?? 0 });
      }
      const height = boxes[index]?.height || row.offsetHeight;
      // Derive the row gap from the layout rather than hard-coding the Tailwind value.
      const gap =
        boxes.length > 1 ? Math.max(0, boxes[1].top - (boxes[0].top + boxes[0].height)) : 0;

      const handle = event.currentTarget;
      // Capture keeps the gesture glued to the handle; window listeners below are what
      // actually drive the drag, so a browser that refuses capture still works.
      try {
        handle.setPointerCapture(event.pointerId);
      } catch {
        // Not capturable — window listeners cover us.
      }

      const onMove = (moveEvent: PointerEvent) => {
        const active = session.current;
        if (!active || moveEvent.pointerId !== active.pointerId) return;
        moveEvent.preventDefault();
        active.clientY = moveEvent.clientY;
        measure();
      };
      const onUp = (upEvent: PointerEvent) => {
        const active = session.current;
        if (!active || upEvent.pointerId !== active.pointerId) return;
        finish(true);
      };
      const onCancel = (cancelEvent: PointerEvent) => {
        const active = session.current;
        if (!active || cancelEvent.pointerId !== active.pointerId) return;
        finish(false);
      };

      window.addEventListener("pointermove", onMove, { passive: false });
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onCancel);

      session.current = {
        pointerId: event.pointerId,
        handle,
        fromIndex: index,
        toIndex: index,
        grabOffset: event.clientY - row.getBoundingClientRect().top,
        boxes,
        slot: height + gap,
        height,
        clientY: event.clientY,
        raf: null,
        detach: () => {
          window.removeEventListener("pointermove", onMove);
          window.removeEventListener("pointerup", onUp);
          window.removeEventListener("pointercancel", onCancel);
        }
      };

      document.body.classList.add("app-dragging");
      navigator.vibrate?.(8);
      measure();
      session.current.raf = requestAnimationFrame(tick);
    },
    [enabled, finish, itemCount, measure, tick]
  );

  const handleProps = useCallback(
    (index: number): DragHandleProps => ({ onPointerDown: onPointerDown(index) }),
    [onPointerDown]
  );

  /** Live transform for a row: the dragged one follows the finger, the rest part to make room. */
  const transformFor = (index: number) => {
    if (!drag) return undefined;
    if (index === drag.fromIndex) return `translate3d(0, ${drag.dy}px, 0)`;
    if (drag.fromIndex < drag.toIndex && index > drag.fromIndex && index <= drag.toIndex) {
      return `translate3d(0, ${-drag.slot}px, 0)`;
    }
    if (drag.fromIndex > drag.toIndex && index >= drag.toIndex && index < drag.fromIndex) {
      return `translate3d(0, ${drag.slot}px, 0)`;
    }
    return "translate3d(0, 0, 0)";
  };

  /** Projected 0-based position, so numbering and priority labels update as you drag. */
  const positionOf = (index: number) => {
    if (!drag) return index;
    if (index === drag.fromIndex) return drag.toIndex;
    if (drag.fromIndex < drag.toIndex && index > drag.fromIndex && index <= drag.toIndex) {
      return index - 1;
    }
    if (drag.fromIndex > drag.toIndex && index >= drag.toIndex && index < drag.fromIndex) {
      return index + 1;
    }
    return index;
  };

  return {
    containerRef,
    registerRow,
    handleProps,
    transformFor,
    positionOf,
    draggingIndex: drag?.fromIndex ?? null,
    isDragging: drag !== null
  };
}
