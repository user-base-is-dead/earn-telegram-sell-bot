import { Suspense } from "react";
import { Sidebar } from "@/components/sidebar";
import { Topbar } from "@/components/topbar";
import { PageTransition } from "@/components/page-transition";
import { getPendingOrderCount } from "@/lib/queries";

export const dynamic = "force-dynamic";

export default async function AdminLayout({ children }: { children: React.ReactNode }) {
  const pendingCount = await getPendingOrderCount();

  return (
    <div className="flex h-dvh overflow-hidden">
      <Sidebar />
      <div className="flex min-h-0 flex-1 flex-col">
        <Suspense fallback={<div className="h-[65px] shrink-0 border-b border-[var(--border)] bg-[var(--surface)]" />}>
          <Topbar pendingCount={pendingCount} />
        </Suspense>
        <main
          className="flex-1 overflow-y-auto p-8"
          style={{
            background:
              "radial-gradient(circle at 15% 0%, color-mix(in oklch, var(--brand-violet-to) 7%, transparent), transparent 55%)",
          }}
        >
          <PageTransition>{children}</PageTransition>
        </main>
      </div>
    </div>
  );
}
