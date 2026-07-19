"use client";
import { motion } from "motion/react";
import { listItem, GLOW_DELAY, DURATION } from "@/lib/motion";
import { KpiSparkline } from "@/components/kpi-sparkline";

type Accent = "violet" | "teal" | "coral" | "magenta" | "gold";

const ACCENT_GRADIENT: Record<Accent, string> = {
  violet: "linear-gradient(135deg, var(--brand-violet-from), var(--brand-violet-to))",
  teal: "linear-gradient(135deg, var(--brand-teal-from), var(--brand-teal-to))",
  coral: "linear-gradient(135deg, var(--brand-coral-from), var(--brand-coral-to))",
  magenta: "linear-gradient(135deg, var(--brand-magenta-from), var(--brand-magenta-to))",
  gold: "linear-gradient(135deg, var(--brand-gold-from), var(--brand-gold-to))",
};

const ACCENT_GLOW: Record<Accent, string> = {
  violet: "var(--glow-violet)",
  teal: "var(--glow-teal)",
  coral: "var(--glow-coral)",
  magenta: "var(--glow-magenta)",
  gold: "var(--glow-gold)",
};

const ACCENT_SOLID: Record<Accent, string> = {
  violet: "var(--brand-violet-to)",
  teal: "var(--brand-teal-to)",
  coral: "var(--brand-coral-to)",
  magenta: "var(--brand-magenta-to)",
  gold: "var(--brand-gold-to)",
};

export function KpiCard({
  label,
  value,
  sublabel,
  icon,
  accent,
  sparkline,
}: {
  label: string;
  value: string;
  sublabel?: string;
  icon: React.ReactNode;
  accent: Accent;
  sparkline?: number[];
}) {
  return (
    <motion.div
      variants={listItem}
      className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-5 ring-1 ring-white/[0.02] transition-colors hover:border-[var(--ink-muted)]/40"
    >
      <div className="relative mb-3 size-9">
        <div
          className="absolute inset-0 rounded-lg"
          style={{ background: ACCENT_GRADIENT[accent] }}
        />
        <motion.div
          className="absolute inset-0 rounded-lg"
          style={{ boxShadow: ACCENT_GLOW[accent] }}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: GLOW_DELAY, duration: DURATION.micro }}
        />
        <div className="relative flex size-9 items-center justify-center">
          {icon}
        </div>
      </div>
      <p className="text-sm text-[var(--ink-muted)]">{label}</p>
      <p className="mt-2 text-3xl font-semibold tabular-nums text-[var(--ink)]">{value}</p>
      {sublabel && <p className="mt-1 text-xs text-[var(--ink-muted)]">{sublabel}</p>}
      {sparkline && sparkline.some((v) => v > 0) && (
        <div className="mt-2">
          <KpiSparkline data={sparkline} color={ACCENT_SOLID[accent]} />
        </div>
      )}
    </motion.div>
  );
}
