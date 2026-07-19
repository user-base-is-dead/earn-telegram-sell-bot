"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion } from "motion/react";
import { LayoutDashboard, Package, Receipt, Store, Users } from "lucide-react";
import { SPRING_INTERACTIVE } from "@/lib/motion";

const NAV = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/orders", label: "Orders", icon: Receipt },
  { href: "/products", label: "Products", icon: Package },
  { href: "/users", label: "Users & Wallet", icon: Users },
];

export function Sidebar() {
  const pathname = usePathname();
  return (
    <nav className="flex h-dvh w-60 shrink-0 flex-col gap-1 overflow-y-auto border-r border-[var(--border)] bg-[var(--surface)] p-4">
      <div className="mb-4 flex items-center gap-2.5 px-1">
        <div
          className="flex size-8 shrink-0 items-center justify-center rounded-lg"
          style={{
            background: "linear-gradient(135deg, var(--brand-gold-from), var(--brand-gold-to))",
            boxShadow: "var(--glow-gold)",
          }}
        >
          <Store className="size-4 text-[var(--bg)]" />
        </div>
        <span className="font-heading truncate text-sm font-semibold text-[var(--ink)]">
          @{process.env.NEXT_PUBLIC_BOT_USERNAME || "Store"}
        </span>
      </div>
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            className="relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-[var(--ink-muted)] transition-colors hover:text-[var(--ink)]"
          >
            {active && (
              <motion.div
                layoutId="active-nav"
                className="absolute inset-0 rounded-lg"
                style={{
                  background: "linear-gradient(135deg, var(--brand-violet-from), var(--brand-violet-to))",
                  boxShadow: "var(--glow-violet)",
                }}
                transition={SPRING_INTERACTIVE}
              />
            )}
            <Icon className={`relative z-10 size-4 ${active ? "text-white" : ""}`} />
            <span className={`relative z-10 ${active ? "text-white" : ""}`}>{label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
