import { supabaseServer } from "@/lib/supabase-server";
import { OrderRow } from "@/components/order-row";
import { LiveRefresh } from "@/components/live-refresh";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export const dynamic = "force-dynamic";

function OrderTable({
  orders,
  actionable,
  emptyMessage,
}: {
  orders: any[];
  actionable: boolean;
  emptyMessage: string;
}) {
  if (orders.length === 0) {
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
            <TableHead>Order</TableHead>
            <TableHead>Product</TableHead>
            <TableHead>Buyer</TableHead>
            <TableHead>Method</TableHead>
            <TableHead>Amount</TableHead>
            <TableHead>Type</TableHead>
            <TableHead>Status</TableHead>
            {actionable && <TableHead>Actions</TableHead>}
          </TableRow>
        </TableHeader>
        <TableBody>
          {orders.map((order) => (
            <OrderRow key={order.id} order={order} actionable={actionable} />
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

export default async function OrdersPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string }>;
}) {
  const { q } = await searchParams;
  const db = supabaseServer();
  const { data: orders } = await db
    .from("orders")
    .select("*")
    .order("id", { ascending: false })
    .limit(100);

  const needle = q?.toLowerCase();
  const matches = (o: { product_name: string; username: string; ref: string }) =>
    !needle ||
    o.product_name.toLowerCase().includes(needle) ||
    o.username.toLowerCase().includes(needle) ||
    o.ref.toLowerCase().includes(needle);

  const filtered = (orders ?? []).filter(matches);
  const pending = filtered.filter((o) => o.status === "pending_review");
  const autoApproved = filtered.filter((o) => o.status !== "pending_review" && o.method === "Wallet");
  const reviewed = filtered.filter((o) => o.status !== "pending_review" && o.method !== "Wallet");

  return (
    <div className="flex flex-col gap-4">
      <LiveRefresh watch={["orders"]} />

      <Tabs defaultValue="pending">
        <TabsList className="h-10 border border-[var(--border)] bg-[var(--surface)] p-1">
          <TabsTrigger
            value="pending"
            className="gap-1.5 data-active:bg-[var(--brand-violet-to)] data-active:text-white data-active:shadow-[var(--glow-violet)]"
          >
            Needs review
            {pending.length > 0 && (
              <Badge className="bg-[var(--warning)]/15 text-[var(--warning)]">{pending.length}</Badge>
            )}
          </TabsTrigger>
          <TabsTrigger
            value="auto"
            className="gap-1.5 data-active:bg-[var(--brand-violet-to)] data-active:text-white data-active:shadow-[var(--glow-violet)]"
          >
            ⚡ Auto-approved
            {autoApproved.length > 0 && (
              <Badge className="bg-[var(--brand-violet-to)]/15 text-[var(--brand-violet-to)]">
                {autoApproved.length}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger
            value="reviewed"
            className="data-active:bg-[var(--brand-violet-to)] data-active:text-white data-active:shadow-[var(--glow-violet)]"
          >
            ✓ Reviewed by you
          </TabsTrigger>
        </TabsList>

        <TabsContent value="pending">
          <p className="text-sm text-[var(--ink-muted)]">
            Manual UPI / Binance Pay / crypto orders awaiting your approve or reject.
          </p>
          <OrderTable orders={pending} actionable emptyMessage="Nothing waiting." />
        </TabsContent>

        <TabsContent value="auto">
          <p className="text-sm text-[var(--ink-muted)]">
            Wallet purchases — delivered instantly, no review needed.
          </p>
          <OrderTable orders={autoApproved} actionable={false} emptyMessage="None yet." />
        </TabsContent>

        <TabsContent value="reviewed">
          <p className="text-sm text-[var(--ink-muted)]">Manual orders you approved or rejected.</p>
          <OrderTable orders={reviewed} actionable={false} emptyMessage="None yet." />
        </TabsContent>
      </Tabs>
    </div>
  );
}
