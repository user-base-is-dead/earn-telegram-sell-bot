"use client";
import { useState, useTransition } from "react";
import Link from "next/link";
import { motion } from "motion/react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { TableCell, TableRow } from "@/components/ui/table";
import { approveOrder, rejectOrder } from "@/app/(admin)/orders/actions";
import { PRESS_SCALE, DURATION } from "@/lib/motion";

const STATUS_COLOR: Record<string, string> = {
  approved: "bg-[var(--success)]/15 text-[var(--success)]",
  rejected: "bg-[var(--danger)]/15 text-[var(--danger)]",
  pending_review: "bg-[var(--warning)]/15 text-[var(--warning)]",
  created: "bg-[var(--ink-muted)]/15 text-[var(--ink-muted)]",
};

export function OrderRow({ order, actionable }: { order: any; actionable: boolean }) {
  const [pending, startTransition] = useTransition();
  const [optimisticStatus, setOptimisticStatus] = useState<string | null>(null);
  const status = optimisticStatus ?? order.status;
  const resolved = optimisticStatus !== null;

  function handle(action: "approve" | "reject") {
    setOptimisticStatus(action === "approve" ? "approved" : "rejected");
    startTransition(async () => {
      try {
        if (action === "approve") await approveOrder(order.id);
        else await rejectOrder(order.id, "Rejected from admin panel");
      } catch (e) {
        setOptimisticStatus(null);
        toast.error(e instanceof Error ? e.message : `Failed to ${action} order`);
      }
    });
  }

  return (
    <TableRow className={resolved ? "opacity-60" : ""}>
      <TableCell className="font-medium text-[var(--ink)]">#{order.ref}</TableCell>
      <TableCell className="max-w-48 truncate">{order.product_name}</TableCell>
      <TableCell className="text-[var(--ink-muted)]">
        <Link href={`/users/${order.user_id}`} className="text-[var(--brand-violet-to)] hover:underline">
          {order.username || order.user_id}
        </Link>
      </TableCell>
      <TableCell className="text-[var(--ink-muted)]">{order.method}</TableCell>
      <TableCell className="text-[var(--ink-muted)]">
        {order.amount_usdt ? `$${order.amount_usdt}` : "Free"}
      </TableCell>
      <TableCell>
        {!actionable && order.status !== "created" && (
          order.method === "Wallet" ? (
            <Badge className="bg-[var(--brand-violet-to)]/15 text-[var(--brand-violet-to)]">⚡ Auto</Badge>
          ) : (
            <Badge className="bg-[var(--ink-muted)]/15 text-[var(--ink-muted)]">✓ Reviewed</Badge>
          )
        )}
      </TableCell>
      <TableCell>
        <Badge className={STATUS_COLOR[status]}>{status}</Badge>
      </TableCell>
      <TableCell>
        {actionable && !resolved && (
          <div className="flex items-center gap-2">
            <motion.div whileTap={{ scale: PRESS_SCALE }} transition={{ duration: DURATION.micro }}>
              <Button size="sm" disabled={pending} onClick={() => handle("approve")}>
                Approve
              </Button>
            </motion.div>
            <motion.div whileTap={{ scale: PRESS_SCALE }} transition={{ duration: DURATION.micro }}>
              <Button size="sm" variant="destructive" disabled={pending} onClick={() => handle("reject")}>
                Reject
              </Button>
            </motion.div>
          </div>
        )}
      </TableCell>
    </TableRow>
  );
}
