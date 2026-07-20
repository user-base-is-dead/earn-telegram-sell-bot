import { supabaseServer } from "./supabase-server";

/** Returns the 7 calendar-day strings (YYYY-MM-DD) ending today, oldest first. */
function last7Days(): string[] {
  const days: string[] = [];
  for (let i = 6; i >= 0; i--) {
    days.push(new Date(Date.now() - i * 24 * 60 * 60 * 1000).toISOString().slice(0, 10));
  }
  return days;
}

/**
 * Buckets rows by their created_at day and sums valueOf(row) per day, returning
 * one number per day of last7Days() (0 for days with no matching rows) so every
 * sparkline series has the same length regardless of data gaps.
 */
function dailySeries(
  rows: { created_at: string }[],
  valueOf: (row: { created_at: string; amount_usdt?: number | null }) => number
): number[] {
  const byDay = new Map<string, number>();
  for (const row of rows) {
    const day = row.created_at.slice(0, 10);
    byDay.set(day, (byDay.get(day) ?? 0) + valueOf(row));
  }
  return last7Days().map((day) => byDay.get(day) ?? 0);
}

export async function getDashboardStats() {
  const db = supabaseServer();

  const since7d = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString();
  const todayStart = new Date();
  todayStart.setHours(0, 0, 0, 0);

  const [
    { data: recentOrders },
    { data: activeProducts },
    { count: pendingCount },
    { data: keyRows },
    { data: pendingDeposits },
    { data: creditedTodayDeposits },
  ] = await Promise.all([
    db
      .from("orders")
      .select("created_at, amount_usdt, status, product_name")
      .gte("created_at", since7d),
    db.from("products").select("id, name, stock").eq("active", 1),
    db.from("orders").select("id", { count: "exact", head: true }).eq("status", "pending_review"),
    db.from("product_keys").select("product_id, used"),
    db.from("deposits").select("tagged_amount_micro").eq("status", "pending"),
    // ponytail: "today" approximated from created_at (request time), not the
    // exact credit timestamp — see the same tradeoff on the users deposits
    // tab, which this mirrors.
    db
      .from("deposits")
      .select("tagged_amount_micro")
      .eq("status", "credited")
      .gte("created_at", todayStart.toISOString()),
  ]);

  const depositsPendingMicro = (pendingDeposits ?? []).reduce((sum, d) => sum + d.tagged_amount_micro, 0);
  const depositsPendingCount = (pendingDeposits ?? []).length;
  const depositsCreditedTodayMicro = (creditedTodayDeposits ?? []).reduce(
    (sum, d) => sum + d.tagged_amount_micro,
    0,
  );
  const depositsCreditedTodayCount = (creditedTodayDeposits ?? []).length;

  // Key-backed ("automatic") products' real stock is their unused-key count, not the
  // manually-typed `stock` column — same distinction the admin panel's product cards make.
  const unusedKeysByProduct = new Map<number, number>();
  const hasKeysByProduct = new Set<number>();
  for (const row of keyRows ?? []) {
    hasKeysByProduct.add(row.product_id);
    if (row.used === 0) {
      unusedKeysByProduct.set(row.product_id, (unusedKeysByProduct.get(row.product_id) ?? 0) + 1);
    }
  }
  const lowStock = (activeProducts ?? [])
    .map((p) => ({
      name: p.name,
      stock: hasKeysByProduct.has(p.id) ? (unusedKeysByProduct.get(p.id) ?? 0) : p.stock,
    }))
    .filter((p) => p.stock >= 0 && p.stock <= 5);

  const approved = (recentOrders ?? []).filter((o) => o.status === "approved");
  const revenueUsdt = approved.reduce((sum, o) => sum + Number(o.amount_usdt ?? 0), 0);

  const revenueUsdtTrend = dailySeries(approved, (o) => Number(o.amount_usdt ?? 0));
  const orderCountTrend = dailySeries(approved, () => 1);

  const trend = last7Days().map((date, i) => ({ date, usdt: revenueUsdtTrend[i] }));

  // last7Days()/dailySeries() both end on today, so the trend's last entry is today's figure —
  // no separate "today" query needed for sales.
  const todayIndex = revenueUsdtTrend.length - 1;

  return {
    revenueUsdt,
    orderCount7d: approved.length,
    pendingCount: pendingCount ?? 0,
    lowStock,
    trend,
    revenueUsdtTrend,
    orderCountTrend,
    topProducts: topProductsByRevenue(approved),
    salesTodayUsdt: revenueUsdtTrend[todayIndex],
    ordersToday: orderCountTrend[todayIndex],
    depositsPendingMicro,
    depositsPendingCount,
    depositsCreditedTodayMicro,
    depositsCreditedTodayCount,
  };
}

/**
 * Groups approved orders by product name and returns the top 4 by USDT revenue,
 * folding everything past the top 4 into an "Other" bucket (omitted if zero).
 */
export function topProductsByRevenue(
  approved: { product_name: string; amount_usdt: number }[]
): { name: string; value: number }[] {
  const byProduct = new Map<string, number>();
  for (const o of approved) {
    byProduct.set(o.product_name, (byProduct.get(o.product_name) ?? 0) + Number(o.amount_usdt ?? 0));
  }
  const sorted = [...byProduct.entries()].sort((a, b) => b[1] - a[1]);
  const top = sorted.slice(0, 4).map(([name, value]) => ({ name, value }));
  const otherValue = sorted.slice(4).reduce((sum, [, v]) => sum + v, 0);
  return otherValue > 0 ? [...top, { name: "Other", value: otherValue }] : top;
}

export async function getRecentOrders(limit: number) {
  const db = supabaseServer();
  const { data } = await db
    .from("orders")
    .select("id, ref, product_name, username, user_id, method, status, amount_usdt, created_at")
    .order("id", { ascending: false })
    .limit(limit);
  return data ?? [];
}

export async function getPendingOrderCount(): Promise<number> {
  const db = supabaseServer();
  const { count } = await db
    .from("orders")
    .select("id", { count: "exact", head: true })
    .eq("status", "pending_review");
  return count ?? 0;
}
