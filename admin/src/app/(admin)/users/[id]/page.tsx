import Link from "next/link";
import { ArrowLeft, Wallet, Receipt, ArrowDownToLine } from "lucide-react";
import { supabaseServer } from "@/lib/supabase-server";
import { KpiCard } from "@/components/kpi-card";
import { KpiRow } from "@/components/kpi-row";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { UserNotes } from "@/components/user-notes";

export const dynamic = "force-dynamic";

const DEPOSIT_BADGE: Record<string, string> = {
  credited: "bg-[var(--success)]/15 text-[var(--success)]",
  pending: "bg-[var(--warning)]/15 text-[var(--warning)]",
  rejected: "bg-[var(--danger)]/15 text-[var(--danger)]",
};

export default async function UserDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const userId = Number(id);
  const db = supabaseServer();

  const [{ data: user }, { data: ledger }, { data: deposits }] = await Promise.all([
    db.from("users").select("*").eq("user_id", userId).single(),
    db.from("wallet_ledger").select("*").eq("user_id", userId).order("id", { ascending: false }),
    db.from("deposits").select("*").eq("user_id", userId).order("id", { ascending: false }),
  ]);

  const ledgerRows = ledger ?? [];
  const depositRows = deposits ?? [];
  const ledgerSum = ledgerRows.reduce((sum, r) => sum + Number(r.delta_micro), 0);
  const balanceMismatch = user && ledgerSum !== user.wallet_balance_micro;
  const totalCredited = ledgerRows.filter((r) => r.delta_micro > 0).reduce((sum, r) => sum + r.delta_micro, 0);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <Link
          href="/users"
          className="inline-flex items-center gap-1.5 text-sm text-[var(--ink-muted)] hover:text-[var(--ink)]"
        >
          <ArrowLeft className="size-4" /> Back to users
        </Link>
        <h1 className="mt-2 text-2xl font-semibold text-[var(--ink)]">
          {user?.first_name || "—"} <span className="text-[var(--ink-muted)]">@{user?.username || "—"}</span>
        </h1>
        <p className="text-sm text-[var(--ink-muted)]">
          ID {userId} · joined {user?.started_at?.slice(0, 10)}
        </p>
      </div>

      <UserNotes userId={userId} initialNotes={user?.notes ?? ""} />

      {balanceMismatch && (
        <div className="rounded-lg border border-[var(--danger)]/40 bg-[var(--danger)]/10 px-4 py-3 text-sm text-[var(--danger)]">
          Ledger sum (${(ledgerSum / 1_000_000).toFixed(3)}) does not match cached balance
          (${((user?.wallet_balance_micro ?? 0) / 1_000_000).toFixed(3)}) — investigate before
          trusting this balance.
        </div>
      )}

      <KpiRow>
        <KpiCard
          label="Current balance"
          value={`$${((user?.wallet_balance_micro ?? 0) / 1_000_000).toFixed(3)}`}
          icon={<Wallet className="size-5 text-white" />}
          accent="teal"
        />
        <KpiCard
          label="Total credited"
          value={`$${(totalCredited / 1_000_000).toFixed(3)}`}
          icon={<ArrowDownToLine className="size-5 text-white" />}
          accent="violet"
        />
        <KpiCard
          label="Ledger entries"
          value={String(ledgerRows.length)}
          icon={<Receipt className="size-5 text-white" />}
          accent="magenta"
        />
        <KpiCard
          label="Deposits"
          value={String(depositRows.length)}
          sublabel={`${depositRows.filter((d) => d.status === "pending").length} pending`}
          icon={<Wallet className="size-5 text-white" />}
          accent="gold"
        />
      </KpiRow>

      <div>
        <h2 className="text-lg font-medium text-[var(--ink)]">Wallet ledger</h2>
        <div className="mt-3 overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--surface)]">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Date</TableHead>
                <TableHead>Reason</TableHead>
                <TableHead className="text-right">Delta</TableHead>
                <TableHead className="text-right">Balance after</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {ledgerRows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={4} className="text-center text-[var(--ink-muted)]">
                    No ledger entries.
                  </TableCell>
                </TableRow>
              ) : (
                ledgerRows.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell className="text-[var(--ink-muted)]">{r.created_at}</TableCell>
                    <TableCell className="text-[var(--ink)]">{r.reason}</TableCell>
                    <TableCell
                      className={`text-right tabular-nums ${r.delta_micro >= 0 ? "text-[var(--success)]" : "text-[var(--danger)]"}`}
                    >
                      {r.delta_micro >= 0 ? "+" : ""}
                      {(r.delta_micro / 1_000_000).toFixed(3)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-[var(--ink)]">
                      {(r.balance_after_micro / 1_000_000).toFixed(3)}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      </div>

      <div>
        <h2 className="text-lg font-medium text-[var(--ink)]">Deposits</h2>
        <div className="mt-3 overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--surface)]">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Rail</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Tagged amount</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {depositRows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={3} className="text-center text-[var(--ink-muted)]">
                    No deposits.
                  </TableCell>
                </TableRow>
              ) : (
                depositRows.map((d) => (
                  <TableRow key={d.id}>
                    <TableCell className="uppercase text-[var(--ink-muted)]">{d.rail}</TableCell>
                    <TableCell>
                      <Badge className={DEPOSIT_BADGE[d.status] ?? "bg-[var(--ink-muted)]/15 text-[var(--ink-muted)]"}>
                        {d.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-[var(--ink)]">
                      {(d.tagged_amount_micro / 1_000_000).toFixed(3)}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      </div>
    </div>
  );
}
