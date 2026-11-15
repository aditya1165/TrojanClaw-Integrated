import { useEffect, useState } from "react";
import type { ToastDetail } from "../lib/toast";

interface Item extends ToastDetail { id: number }

/** Shows toasts for a few seconds, newest at the bottom. Mounted once in App. */
export default function Toaster() {
  const [items, setItems] = useState<Item[]>([]);

  useEffect(() => {
    let next = 0;
    const show = (event: Event) => {
      const item = { ...(event as CustomEvent<ToastDetail>).detail, id: next++ };
      setItems((current) => [...current.slice(-2), item]);
      window.setTimeout(() => setItems((current) => current.filter((value) => value.id !== item.id)), item.tone === "error" ? 6000 : 3500);
    };
    window.addEventListener("tc-toast", show);
    return () => window.removeEventListener("tc-toast", show);
  }, []);

  return (
    <div aria-live="polite" className="pointer-events-none fixed inset-x-0 bottom-6 z-[60] flex flex-col items-center gap-2 px-4">
      {items.map((item) => (
        <div key={item.id} role={item.tone === "error" ? "alert" : "status"}
          className={`pointer-events-auto flex max-w-md items-center gap-3 rounded-xl px-4 py-3 text-sm shadow-lg ${item.tone === "error" ? "border border-line bg-error-soft text-error" : "bg-fg text-bg"}`}>
          <span>{item.message}</span>
          <button type="button" aria-label="Dismiss" className="opacity-70 hover:opacity-100"
            onClick={() => setItems((current) => current.filter((value) => value.id !== item.id))}>×</button>
        </div>
      ))}
    </div>
  );
}
