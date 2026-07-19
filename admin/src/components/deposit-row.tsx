"use client";
import { useId, useState, useTransition } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { TableCell, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { rejectDeposit, resolveDeposit } from "@/app/(admin)/users/deposit-actions";

export function DepositRow({
  deposit,
  actionable,
  displayAmountMicro,
  isManual,
}: {
  deposit: any;
  actionable: boolean;
  /** Real credited amount (from wallet_ledger) for manual credits, which can
   * differ from the tagged amount — falls back to tagged_amount_micro. */
  displayAmountMicro?: number;
  isManual?: boolean;
}) {
  const uid = useId();
  const defaultAmount = (deposit.tagged_amount_micro / 1_000_000).toFixed(6);
  const amountToShow = displayAmountMicro ?? deposit.tagged_amount_micro;
  const [resolving, setResolving] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [amount, setAmount] = useState(defaultAmount);
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [pending, startTransition] = useTransition();
  const [localStatus, setLocalStatus] = useState<"resolved" | "rejected" | null>(null);

  const stale = deposit.status === "pending" && new Date(deposit.expires_at) < new Date();

  function openResolve() {
    // Reset to the tagged amount each time the dialog opens, so a cancelled
    // edit from a prior attempt on this same row can't linger and get
    // submitted by mistake — this is a money-crediting form.
    setAmount(defaultAmount);
    setNote("");
    setResolving(true);
  }

  function openReject() {
    setReason("");
    setRejecting(true);
  }

  function submitResolve() {
    const micro = Math.round(Number(amount) * 1_000_000);
    if (!micro || micro <= 0 || !Number.isFinite(micro)) {
      toast.error("Enter a valid amount");
      return;
    }
    startTransition(async () => {
      try {
        await resolveDeposit(deposit.id, deposit.user_id, micro, note);
        setLocalStatus("resolved");
        setResolving(false);
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Failed to resolve deposit");
      }
    });
  }

  function submitReject() {
    startTransition(async () => {
      try {
        await rejectDeposit(deposit.id, reason);
        setLocalStatus("rejected");
        setRejecting(false);
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Failed to reject deposit");
      }
    });
  }

  const badgeLabel = localStatus ?? (stale ? "stale" : deposit.status);
  const badgeClass =
    localStatus === "resolved" || deposit.status === "credited"
      ? "bg-[var(--success)]/15 text-[var(--success)]"
      : localStatus === "rejected" || deposit.status === "rejected" || stale
        ? "bg-[var(--danger)]/15 text-[var(--danger)]"
        : "bg-[var(--warning)]/15 text-[var(--warning)]";

  return (
    <TableRow className={localStatus ? "opacity-60" : ""}>
      <TableCell className="text-[var(--ink-muted)]">{deposit.buyer_name}</TableCell>
      <TableCell className="uppercase text-[var(--ink-muted)]">{deposit.rail}</TableCell>
      <TableCell className="text-right tabular-nums">{(amountToShow / 1_000_000).toFixed(3)}</TableCell>
      <TableCell className="text-[var(--ink-muted)]">{deposit.created_at}</TableCell>
      <TableCell>
        <div className="flex items-center gap-1.5">
          <Badge className={badgeClass}>{badgeLabel}</Badge>
          {isManual && (
            <Badge className="bg-[var(--ink-muted)]/15 text-[var(--ink-muted)]">Manual</Badge>
          )}
        </div>
      </TableCell>
      <TableCell>
        {actionable && !localStatus && (
          <div className="flex gap-1.5">
            <Button size="sm" disabled={pending} onClick={openResolve}>
              Resolve
            </Button>
            <Button size="sm" variant="destructive" disabled={pending} onClick={openReject}>
              Reject
            </Button>
          </div>
        )}
      </TableCell>
      {resolving && (
        <Dialog open onOpenChange={(open) => !open && setResolving(false)}>
          <DialogContent className="w-full max-w-md">
            <DialogHeader>
              <DialogTitle>Resolve deposit #{deposit.id}</DialogTitle>
            </DialogHeader>
            <div className="flex flex-col gap-3">
              <p className="text-sm text-[var(--ink-muted)]">
                Buyer was tagged for {(deposit.tagged_amount_micro / 1_000_000).toFixed(3)} USDT.
                Enter what they actually sent.
              </p>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor={`amount-${uid}`}>Amount to credit (USDT)</Label>
                <Input
                  id={`amount-${uid}`}
                  type="number"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor={`note-${uid}`}>Note (optional)</Label>
                <Input
                  id={`note-${uid}`}
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="e.g. sent 9.98 instead of 10.03"
                />
              </div>
              <Button disabled={pending} onClick={submitResolve}>
                Credit wallet
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      )}
      {rejecting && (
        <Dialog open onOpenChange={(open) => !open && setRejecting(false)}>
          <DialogContent className="w-full max-w-md">
            <DialogHeader>
              <DialogTitle>Reject deposit #{deposit.id}</DialogTitle>
            </DialogHeader>
            <div className="flex flex-col gap-3">
              <p className="text-sm text-[var(--ink-muted)]">
                Marks this as never received — no wallet credit. The buyer gets a DM with the
                reason below.
              </p>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor={`reason-${uid}`}>Reason (sent to the buyer)</Label>
                <Input
                  id={`reason-${uid}`}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="e.g. No matching transaction found"
                />
              </div>
              <Button variant="destructive" disabled={pending} onClick={submitReject}>
                Reject deposit
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      )}
    </TableRow>
  );
}
