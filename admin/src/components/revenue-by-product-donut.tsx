"use client";
import { PieChart, Pie, Cell, Tooltip, Legend, ResponsiveContainer } from "recharts";

const COLORS = [
  "var(--brand-gold-to)",
  "var(--brand-violet-to)",
  "var(--brand-teal-to)",
  "var(--brand-coral-to)",
  "var(--brand-magenta-to)",
];

export function RevenueByProductDonut({ data }: { data: { name: string; value: number }[] }) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-5">
      <p className="mb-4 text-sm font-medium text-[var(--ink)]">Revenue by product</p>
      {data.length === 0 ? (
        <p className="text-sm text-[var(--ink-muted)]">No approved orders in the last 7 days.</p>
      ) : (
        <ResponsiveContainer width="100%" height={240}>
          <PieChart>
            <Pie
              data={data}
              dataKey="value"
              nameKey="name"
              innerRadius={55}
              outerRadius={85}
              paddingAngle={2}
              animationDuration={350}
            >
              {data.map((entry, i) => (
                <Cell key={entry.name} fill={COLORS[i % COLORS.length]} />
              ))}
            </Pie>
            <Tooltip
              contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8 }}
            />
            <Legend wrapperStyle={{ fontSize: 12 }} />
          </PieChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
