"use client";
import { useState, useTransition, useEffect } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { motion } from "motion/react";
import { Bell, LogOut, Search } from "lucide-react";
import { signOutAction } from "@/app/(admin)/actions";
import { DURATION } from "@/lib/motion";

const TITLES: [string, string][] = [
  ["/dashboard", "Dashboard"],
  ["/orders", "Orders"],
  ["/products", "Products"],
  ["/users", "Users & Wallet"],
];

export function Topbar({ pendingCount }: { pendingCount: number }) {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [query, setQuery] = useState(searchParams.get("q") ?? "");
  useEffect(() => {
    setQuery(searchParams.get("q") ?? "");
  }, [searchParams]);
  const [, startTransition] = useTransition();

  const title = TITLES.find(([prefix]) => pathname.startsWith(prefix))?.[1] ?? "Admin";

  function onSearch(value: string) {
    setQuery(value);
    startTransition(() => {
      const params = new URLSearchParams(searchParams.toString());
      if (value) params.set("q", value);
      else params.delete("q");
      const qs = params.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname);
    });
  }

  return (
    <div className="flex shrink-0 items-center justify-between border-b border-[var(--border)] bg-[var(--surface)] px-8 py-4">
      <h2 className="font-heading text-lg font-semibold text-[var(--ink)]">{title}</h2>
      <div className="flex items-center gap-4">
        <div className="relative">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-[var(--ink-muted)]" />
          <input
            value={query}
            onChange={(e) => onSearch(e.target.value)}
            placeholder="Search this page..."
            aria-label="Search this page"
            className="w-56 rounded-lg border border-[var(--border)] bg-[var(--bg)] py-2 pr-3 pl-9 text-sm text-[var(--ink)] outline-none focus:border-[var(--brand-violet-to)]"
          />
        </div>
        <div className="relative">
          <Bell className="size-5 text-[var(--ink-muted)]" />
          {pendingCount > 0 && (
            <motion.span
              initial={{ scale: 0 }}
              animate={{ scale: 1 }}
              transition={{ duration: DURATION.micro }}
              className="absolute -top-1 -right-1 flex size-4 items-center justify-center rounded-full text-[10px] font-semibold text-white"
              style={{ background: "var(--brand-coral-to)" }}
            >
              {pendingCount > 9 ? "9+" : pendingCount}
            </motion.span>
          )}
        </div>
        <button
          onClick={() => startTransition(() => void signOutAction())}
          className="flex size-8 items-center justify-center rounded-full text-[var(--ink-muted)] hover:text-[var(--ink)]"
          aria-label="Sign out"
        >
          <LogOut className="size-4" />
        </button>
      </div>
    </div>
  );
}
