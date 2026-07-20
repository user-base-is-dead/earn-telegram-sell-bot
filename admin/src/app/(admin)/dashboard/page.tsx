import Link from "next/link";
import { DollarSign, Wallet, Receipt, Clock, ArrowDownToLine, Hourglass, Sparkles } from "lucide-react";
import { getDashboardStats, getRecentOrders } from "@/lib/queries";
import { KpiCard } from "@/components/kpi-card";
import { DashboardChart } from "@/components/dashboard-chart";
import { RevenueByProductDonut } from "@/components/revenue-by-product-donut";
import { KpiRow } from "@/components/kpi-row";
import { LiveRefresh } from "@/components/live-refresh";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export const dynamic = "force-dynamic";

const STATUS_COLOR: Record<string, string> = {
  approved: "bg-[var(--success)]/15 text-[var(--success)]",
  rejected: "bg-[var(--danger)]/15 text-[var(--danger)]",
  pending_review: "bg-[var(--warning)]/15 text-[var(--warning)]",
  created: "bg-[var(--ink-muted)]/15 text-[var(--ink-muted)]",
};

export default async function DashboardPage() {
  const [stats, recentOrders] = await Promise.all([getDashboardStats(), getRecentOrders(8)]);

  return (
    <div className="flex flex-col gap-6">
      <LiveRefresh watch={["orders", "products", "deposits"]} />

      {stats.lowStock.length > 0 && (
        <div className="rounded-lg border border-[var(--brand-coral-to)]/30 bg-[var(--brand-coral-to)]/10 px-4 py-3 text-sm text-[var(--ink)]">
          Low stock: {stats.lowStock.map((p) => `${p.name} (${p.stock})`).join(", ")}
        </div>
      )}

      <div>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-medium text-[var(--ink-muted)]">Today</h2>
          <Link href="/users" className="text-xs text-[var(--brand-violet-to)] hover:underline">
            Manage deposits →
          </Link>
        </div>
        <KpiRow>
          <KpiCard
            label="Sales today"
            value={`$${stats.salesTodayUsdt.toFixed(2)}`}
            sublabel={`${stats.ordersToday} order(s)`}
            icon={<DollarSign className="size-5 text-white" />}
            accent="gold"
          />
          <KpiCard
            label="Deposits credited today"
            value={`$${(stats.depositsCreditedTodayMicro / 1_000_000).toFixed(2)}`}
            sublabel={`${stats.depositsCreditedTodayCount} deposit(s)`}
            icon={<ArrowDownToLine className="size-5 text-white" />}
            accent="teal"
          />
          <KpiCard
            label="Deposits pending"
            value={`$${(stats.depositsPendingMicro / 1_000_000).toFixed(2)}`}
            sublabel={`${stats.depositsPendingCount} deposit(s)`}
            icon={<Hourglass className="size-5 text-white" />}
            accent="coral"
          />
          <KpiCard
            label="Pending approval"
            value={String(stats.pendingCount)}
            sublabel="Orders needing review"
            icon={<Clock className="size-5 text-white" />}
            accent="magenta"
          />
        </KpiRow>
      </div>

      <div>
        <h2 className="mb-3 text-sm font-medium text-[var(--ink-muted)]">Last 7 days</h2>
        <KpiRow>
          <KpiCard
            label="Revenue (7d)"
            value={`$${stats.revenueUsdt.toFixed(2)}`}
            icon={<Wallet className="size-5 text-white" />}
            accent="violet"
            sparkline={stats.revenueUsdtTrend}
          />
          <KpiCard
            label="Orders (7d)"
            value={String(stats.orderCount7d)}
            icon={<Receipt className="size-5 text-white" />}
            accent="teal"
            sparkline={stats.orderCountTrend}
          />
          <KpiCard
            label="Top product (7d)"
            value={stats.topProducts[0]?.name ?? "—"}
            sublabel={stats.topProducts[0] ? `$${stats.topProducts[0].value.toFixed(2)} USDT` : undefined}
            icon={<Sparkles className="size-5 text-white" />}
            accent="magenta"
          />
        </KpiRow>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1.6fr_1fr]">
        <DashboardChart data={stats.trend} />
        <RevenueByProductDonut data={stats.topProducts} />
      </div>

      <div className="overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--surface)]">
        <div
          className="h-[3px] w-full"
          style={{ background: "linear-gradient(90deg, var(--brand-gold-to), var(--brand-violet-to))" }}
        />
        <div className="flex items-center justify-between px-5 pt-4">
          <p className="font-heading text-sm font-medium text-[var(--ink)]">Recent activity</p>
          <Link href="/orders" className="text-xs text-[var(--brand-violet-to)] hover:underline">
            View all orders
          </Link>
        </div>
        {recentOrders.length === 0 ? (
          <p className="px-5 py-6 text-sm text-[var(--ink-muted)]">No orders yet.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Order</TableHead>
                <TableHead>Product</TableHead>
                <TableHead>Buyer</TableHead>
                <TableHead>Method</TableHead>
                <TableHead>Amount</TableHead>
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {recentOrders.map((order) => (
                <TableRow key={order.id}>
                  <TableCell className="font-medium text-[var(--ink)]">#{order.ref}</TableCell>
                  <TableCell className="max-w-48 truncate">{order.product_name}</TableCell>
                  <TableCell>
                    <Link
                      href={`/users/${order.user_id}`}
                      className="text-[var(--brand-violet-to)] hover:underline"
                    >
                      {order.username || order.user_id}
                    </Link>
                  </TableCell>
                  <TableCell className="text-[var(--ink-muted)]">{order.method}</TableCell>
                  <TableCell className="text-[var(--ink-muted)]">
                    {order.amount_usdt ? `$${order.amount_usdt}` : "Free"}
                  </TableCell>
                  <TableCell>
                    <Badge className={STATUS_COLOR[order.status]}>{order.status}</Badge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
    </div>
  );
}
