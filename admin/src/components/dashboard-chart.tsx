"use client";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";

export function DashboardChart({ data }: { data: { date: string; usdt: number }[] }) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-5">
      <p className="mb-4 text-sm font-medium text-[var(--ink)]">Revenue trend (USDT)</p>
      <ResponsiveContainer width="100%" height={240}>
        <AreaChart data={data}>
          <defs>
            <linearGradient id="revenueTrendFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--brand-violet-to)" stopOpacity={0.5} />
              <stop offset="100%" stopColor="var(--brand-violet-to)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis dataKey="date" stroke="var(--ink-muted)" fontSize={12} />
          <YAxis stroke="var(--ink-muted)" fontSize={12} />
          <Tooltip
            contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8 }}
          />
          <Area
            type="monotone"
            dataKey="usdt"
            stroke="var(--brand-violet-to)"
            strokeWidth={2}
            fill="url(#revenueTrendFill)"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
