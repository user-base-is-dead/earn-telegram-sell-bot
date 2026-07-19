import Link from "next/link";
import { Users as UsersIcon, Wallet, Sparkles, CircleDollarSign } from "lucide-react";
import { supabaseServer } from "@/lib/supabase-server";
import { KpiCard } from "@/components/kpi-card";
import { KpiRow } from "@/components/kpi-row";
import { DepositRow } from "@/components/deposit-row";
import { LiveRefresh } from "@/components/live-refresh";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export const dynamic = "force-dynamic";

type UserRow = {
  user_id: number;
  first_name: string;
  username: string;
  started_at: string;
  wallet_balance_micro: number;
};

const TAB_TRIGGER =
  "data-active:bg-[var(--brand-violet-to)] data-active:text-white data-active:shadow-[var(--glow-violet)]";

function UserTable({ users, emptyMessage }: { users: UserRow[]; emptyMessage: string }) {
  if (users.length === 0) {
    return <p className="mt-4 text-sm text-[var(--ink-muted)]">{emptyMessage}</p>;
  }
  return (
    <div className="mt-4 overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--surface)]">
      <div
        className="h-[3px] w-full"
        style={{ background: "linear-gradient(90deg, var(--brand-violet-to), var(--brand-teal-to))" }}
      />
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>User</TableHead>
            <TableHead>User ID</TableHead>
            <TableHead>Joined</TableHead>
            <TableHead className="text-right">Wallet balance</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {users.map((u) => {
            const isNew = Date.now() - new Date(u.started_at).getTime() < 7 * 24 * 60 * 60 * 1000;
            return (
              <TableRow key={u.user_id} className="cursor-pointer">
                <TableCell className="p-0">
                  <Link
                    href={`/users/${u.user_id}`}
                    className="flex items-center gap-2 px-2 py-2 text-[var(--ink)]"
                  >
                    {u.first_name || "—"} <span className="text-[var(--ink-muted)]">@{u.username || "—"}</span>
                    {isNew && (
                      <Badge className="bg-[var(--brand-teal-to)]/15 text-[var(--brand-teal-to)]">New</Badge>
                    )}
                  </Link>
                </TableCell>
                <TableCell className="text-[var(--ink-muted)] tabular-nums">{u.user_id}</TableCell>
                <TableCell className="text-[var(--ink-muted)]">{u.started_at.slice(0, 10)}</TableCell>
                <TableCell className="text-right tabular-nums text-[var(--ink)]">
                  ${(u.wallet_balance_micro / 1_000_000).toFixed(3)}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

function StatCard({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <p className="text-xs text-[var(--ink-muted)]">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-[var(--ink)]">{value}</p>
      <p className="text-xs text-[var(--ink-muted)]">{sub}</p>
    </div>
  );
}

function DepositTable({
  deposits,
  actionable,
  emptyMessage,
  amountByDepositId,
  manualIds,
}: {
  deposits: any[];
  actionable: boolean;
  emptyMessage: string;
  amountByDepositId?: Map<number, number>;
  manualIds?: Set<number>;
}) {
  if (deposits.length === 0) {
    return <p className="mt-4 text-sm text-[var(--ink-muted)]">{emptyMessage}</p>;
  }
  return (
    <div className="mt-4 overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--surface)]">
      <div
        className="h-[3px] w-full"
        style={{ background: "linear-gradient(90deg, var(--brand-violet-to), var(--brand-gold-to))" }}
      />
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Buyer</TableHead>
            <TableHead>Rail</TableHead>
            <TableHead className="text-right">Amount</TableHead>
            <TableHead>Created</TableHead>
            <TableHead>Status</TableHead>
            {actionable && <TableHead>Actions</TableHead>}
          </TableRow>
        </TableHeader>
        <TableBody>
          {deposits.map((d) => (
            <DepositRow
              key={d.id}
              deposit={d}
              actionable={actionable}
              displayAmountMicro={amountByDepositId?.get(d.id)}
              isManual={manualIds?.has(d.id)}
            />
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

async function DepositsSection() {
  const db = supabaseServer();
  const { data: deposits } = await db
    .from("deposits")
    .select("*")
    .order("id", { ascending: true }) // oldest first, so genuinely stuck ones surface at the top
    .limit(200);

  const userIds = [...new Set((deposits ?? []).map((d) => d.user_id))];
  const { data: depositUsers } = userIds.length
    ? await db.from("users").select("user_id, first_name, username").in("user_id", userIds)
    : { data: [] as { user_id: number; first_name: string; username: string }[] };
  const userMap = new Map((depositUsers ?? []).map((u) => [u.user_id, u]));

  const enriched = (deposits ?? []).map((d) => ({
    ...d,
    buyer_name: userMap.get(d.user_id)?.username || userMap.get(d.user_id)?.first_name || d.user_id,
  }));

  const pending = enriched.filter((d) => d.status === "pending");
  const credited = enriched.filter((d) => d.status === "credited"); // auto-matched + manually resolved
  const rejected = enriched.filter((d) => d.status === "rejected");
  const manualCredited = credited.filter((d) => d.credited_tx_ref?.startsWith("manual:"));
  const manualIds = new Set(manualCredited.map((d) => d.id));

  // Manual credits can differ from the tagged amount (the whole point of a
  // manual resolve is confirming what actually arrived) — look up the real
  // credited amount from wallet_ledger. Auto-credits always equal the tagged
  // amount exactly (that's how the matcher works), so no lookup needed there.
  const manualRefs = manualCredited.map((d) => `deposit:${d.id}`);
  const { data: manualLedgerRows } = manualRefs.length
    ? await db
        .from("wallet_ledger")
        .select("ref, delta_micro")
        .eq("reason", "manual_admin_credit")
        .in("ref", manualRefs)
    : { data: [] as { ref: string; delta_micro: number }[] };
  const amountByDepositId = new Map(
    (manualLedgerRows ?? []).map((r) => [Number(r.ref.split(":")[1]), r.delta_micro]),
  );

  const pendingTotalMicro = pending.reduce((sum, d) => sum + d.tagged_amount_micro, 0);
  const staleCount = pending.filter((d) => new Date(d.expires_at) < new Date()).length;

  // ponytail: "today" is approximated from the deposit's created_at (when the
  // top-up was requested), not the exact credit timestamp — auto-credits land
  // within ~1 min of creation in practice, so this is accurate for the
  // overwhelming majority of rows. A stale deposit resolved days after
  // creation would misattribute; upgrade path is joining wallet_ledger's own
  // created_at if that ever matters.
  const todayStart = new Date();
  todayStart.setHours(0, 0, 0, 0);
  const creditedToday = credited.filter((d) => new Date(d.created_at) >= todayStart);
  const creditedTodayTotalMicro = creditedToday.reduce(
    (sum, d) => sum + (amountByDepositId.get(d.id) ?? d.tagged_amount_micro),
    0,
  );

  const usdt = (micro: number) => `${(micro / 1_000_000).toFixed(3)} USDT`;

  return (
    <div className="mt-4 flex flex-col gap-4">
      <LiveRefresh watch={["deposits"]} />

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <StatCard
          label="Pending"
          value={usdt(pendingTotalMicro)}
          sub={`${pending.length} deposit(s)${staleCount ? ` · ${staleCount} stale` : ""}`}
        />
        <StatCard
          label="Credited today"
          value={usdt(creditedTodayTotalMicro)}
          sub={`${creditedToday.length} deposit(s)`}
        />
        <StatCard label="Rejected" value={String(rejected.length)} sub="all-time" />
      </div>

      <Tabs defaultValue="pending">
        <TabsList className="h-10 border border-[var(--border)] bg-[var(--surface)] p-1">
          <TabsTrigger value="pending" className={`gap-1.5 ${TAB_TRIGGER}`}>
            Pending
            {pending.length > 0 && (
              <Badge className="bg-[var(--warning)]/15 text-[var(--warning)]">{pending.length}</Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="credited" className={TAB_TRIGGER}>
            Credited
          </TabsTrigger>
          <TabsTrigger value="rejected" className={TAB_TRIGGER}>
            Rejected
          </TabsTrigger>
        </TabsList>

        <TabsContent value="pending">
          <p className="text-sm text-[var(--ink-muted)]">
            Deposits waiting to be auto-matched by the BSC/Binance Pay watcher. Stale ones (past
            their expiry) will never auto-match — resolve or reject them once you&apos;ve
            confirmed what (if anything) the buyer actually sent.
          </p>
          <DepositTable deposits={pending} actionable emptyMessage="Nothing pending." />
        </TabsContent>

        <TabsContent value="credited">
          <p className="text-sm text-[var(--ink-muted)]">
            Every credited deposit — auto-matched by the watcher, or resolved by hand (badged
            &quot;Manual&quot;).
          </p>
          <DepositTable
            deposits={credited}
            actionable={false}
            emptyMessage="None yet."
            amountByDepositId={amountByDepositId}
            manualIds={manualIds}
          />
        </TabsContent>

        <TabsContent value="rejected">
          <p className="text-sm text-[var(--ink-muted)]">
            Deposits marked as never received — the buyer was DM&apos;d with the reason.
          </p>
          <DepositTable deposits={rejected} actionable={false} emptyMessage="None yet." />
        </TabsContent>
      </Tabs>
    </div>
  );
}

export default async function UsersPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string }>;
}) {
  const { q } = await searchParams;
  const db = supabaseServer();
  const { data: users } = await db
    .from("users")
    .select("user_id, first_name, username, wallet_balance_micro, started_at")
    .order("started_at", { ascending: false })
    .limit(200);

  const all = (users ?? []) as UserRow[];
  const needle = q?.toLowerCase();
  const filtered = all.filter(
    (u) =>
      !needle ||
      u.first_name.toLowerCase().includes(needle) ||
      u.username.toLowerCase().includes(needle) ||
      String(u.user_id).includes(needle)
  );

  const weekAgo = Date.now() - 7 * 24 * 60 * 60 * 1000;
  const newUsers = filtered.filter((u) => new Date(u.started_at).getTime() >= weekAgo);
  const holders = filtered.filter((u) => u.wallet_balance_micro > 0);
  const zeroBalance = filtered.filter((u) => u.wallet_balance_micro === 0);
  const totalBalance = all.reduce((sum, u) => sum + u.wallet_balance_micro, 0) / 1_000_000;

  return (
    <div className="flex flex-col gap-6">
      <KpiRow>
        <KpiCard label="Total users" value={String(all.length)} icon={<UsersIcon className="size-5 text-white" />} accent="violet" />
        <KpiCard label="Total wallet balance" value={`$${totalBalance.toFixed(3)}`} icon={<Wallet className="size-5 text-white" />} accent="teal" />
        <KpiCard label="New this week" value={String(newUsers.length)} icon={<Sparkles className="size-5 text-white" />} accent="magenta" />
        <KpiCard label="Wallet holders" value={String(holders.length)} sublabel={`${zeroBalance.length} at zero balance`} icon={<CircleDollarSign className="size-5 text-white" />} accent="gold" />
      </KpiRow>

      <Tabs defaultValue="all">
        <TabsList className="h-10 border border-[var(--border)] bg-[var(--surface)] p-1">
          <TabsTrigger value="all" className={TAB_TRIGGER}>
            All ({filtered.length})
          </TabsTrigger>
          <TabsTrigger value="new" className={TAB_TRIGGER}>
            New ({newUsers.length})
          </TabsTrigger>
          <TabsTrigger value="holders" className={TAB_TRIGGER}>
            Wallet holders ({holders.length})
          </TabsTrigger>
          <TabsTrigger value="zero" className={TAB_TRIGGER}>
            Zero balance ({zeroBalance.length})
          </TabsTrigger>
          <TabsTrigger value="deposits" className={TAB_TRIGGER}>
            Deposits
          </TabsTrigger>
        </TabsList>

        <TabsContent value="all">
          <UserTable users={filtered} emptyMessage={q ? `No users match "${q}".` : "No users yet."} />
        </TabsContent>
        <TabsContent value="new">
          <UserTable users={newUsers} emptyMessage="No new users in the last 7 days." />
        </TabsContent>
        <TabsContent value="holders">
          <UserTable users={holders} emptyMessage="No users with a positive balance." />
        </TabsContent>
        <TabsContent value="zero">
          <UserTable users={zeroBalance} emptyMessage="Every user has a positive balance." />
        </TabsContent>
        <TabsContent value="deposits">
          <DepositsSection />
        </TabsContent>
      </Tabs>
    </div>
  );
}
