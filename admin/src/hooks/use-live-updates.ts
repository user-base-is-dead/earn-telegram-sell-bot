"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** Re-fetches the current Server Component route whenever a watched table changes. */
export function useLiveUpdates(watchTables: string[]) {
  const router = useRouter();

  useEffect(() => {
    const source = new EventSource("/api/realtime");
    source.onmessage = (e) => {
      if (watchTables.includes(e.data)) {
        router.refresh();
      }
    };
    return () => source.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router, watchTables.join(",")]);
}
