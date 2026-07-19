"use client";
import { useMemo, useState, useTransition } from "react";
import { toast } from "sonner";
import { AlertTriangle, KeyRound, PackageX } from "lucide-react";
import { KpiCard } from "@/components/kpi-card";
import { KpiRow } from "@/components/kpi-row";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ProductFormDialog } from "@/components/product-form-dialog";
import { clearProductKeys, setProductActive } from "@/app/(admin)/products/actions";

const LOW_STOCK_THRESHOLD = 5;

type StockRow = {
  id: number;
  name: string;
  stock: number;
  active: number;
  hasKeys: boolean;
  unusedKeys: number;
  [key: string]: unknown;
};

type BulkAction = "deactivate" | "reactivate" | "clearcodes";

const BULK_LABELS: Record<BulkAction, string> = {
  deactivate: "Deactivate",
  reactivate: "Reactivate",
  clearcodes: "Clear unused codes for",
};

function effectiveStockOf(r: StockRow): number {
  return r.hasKeys ? r.unusedKeys : r.stock;
}

function rankOf(r: StockRow): number {
  const stock = effectiveStockOf(r);
  if (r.active === 0) return 2;
  if (stock === 0) return 0;
  if (stock !== -1 && stock <= LOW_STOCK_THRESHOLD) return 1;
  return 1.5;
}

export function StockTable({ products }: { products: StockRow[] }) {
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [editing, setEditing] = useState<StockRow | null>(null);
  const [confirmAction, setConfirmAction] = useState<BulkAction | null>(null);
  const [pending, startTransition] = useTransition();

  const rows = useMemo(
    () => [...products].sort((a, b) => rankOf(a) - rankOf(b) || a.name.localeCompare(b.name)),
    [products]
  );

  const outOfStockCount = rows.filter((r) => r.active === 1 && effectiveStockOf(r) === 0).length;
  const lowStockCount = rows.filter((r) => {
    const s = effectiveStockOf(r);
    return r.active === 1 && s !== -1 && s > 0 && s <= LOW_STOCK_THRESHOLD;
  }).length;
  const totalUnusedCodes = rows.filter((r) => r.hasKeys).reduce((sum, r) => sum + r.unusedKeys, 0);

  function toggle(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function applyBulk() {
    if (!confirmAction) return;
    const ids = [...selected];
    startTransition(async () => {
      let applied = 0;
      try {
        for (const id of ids) {
          const row = rows.find((r) => r.id === id);
          if (!row) continue;
          if (confirmAction === "deactivate" && row.active === 1) {
            await setProductActive(id, false);
            applied++;
          } else if (confirmAction === "reactivate" && row.active === 0) {
            await setProductActive(id, true);
            applied++;
          } else if (confirmAction === "clearcodes" && row.hasKeys && row.unusedKeys > 0) {
            await clearProductKeys(id);
            applied++;
          }
        }
        toast(`Applied to ${applied} of ${ids.length} selected product(s).`);
        setSelected(new Set());
        setConfirmAction(null);
      } catch (e) {
        const msg = e instanceof Error ? e.message : "Bulk action failed";
        toast.error(applied > 0 ? `Applied to ${applied} of ${ids.length} before failing: ${msg}` : msg);
      }
    });
  }

  return (
    <div className="flex flex-col gap-4">
      <KpiRow>
        <KpiCard
          label="Out of stock"
          value={String(outOfStockCount)}
          icon={<PackageX className="size-5 text-white" />}
          accent="coral"
        />
        <KpiCard
          label="Low stock"
          value={String(lowStockCount)}
          icon={<AlertTriangle className="size-5 text-white" />}
          accent="gold"
        />
        <KpiCard
          label="Total unused codes"
          value={String(totalUnusedCodes)}
          icon={<KeyRound className="size-5 text-white" />}
          accent="teal"
        />
      </KpiRow>

      {selected.size > 0 && (
        <div className="flex items-center justify-between rounded-lg border border-[var(--brand-violet-to)]/40 bg-[var(--brand-violet-to)]/10 px-4 py-2">
          <span className="text-sm">{selected.size} selected</span>
          <div className="flex gap-2">
            <Button size="sm" variant="destructive" onClick={() => setConfirmAction("deactivate")}>
              Deactivate
            </Button>
            <Button size="sm" variant="outline" onClick={() => setConfirmAction("reactivate")}>
              Reactivate
            </Button>
            <Button size="sm" variant="outline" onClick={() => setConfirmAction("clearcodes")}>
              Clear codes
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
              Clear selection
            </Button>
          </div>
        </div>
      )}

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead />
            <TableHead>Product</TableHead>
            <TableHead>Type</TableHead>
            <TableHead>Stock</TableHead>
            <TableHead>Status</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r) => {
            const stock = effectiveStockOf(r);
            const statusLabel =
              r.active === 0 ? "Inactive" : stock === 0 ? "Sold out" : stock !== -1 && stock <= LOW_STOCK_THRESHOLD ? "Low stock" : "In stock";
            const statusClass =
              r.active === 0
                ? "bg-[var(--ink-muted)]/15 text-[var(--ink-muted)]"
                : stock === 0
                  ? "bg-[var(--brand-coral-to)]/15 text-[var(--brand-coral-to)]"
                  : stock !== -1 && stock <= LOW_STOCK_THRESHOLD
                    ? "bg-[var(--brand-gold-to)]/15 text-[var(--brand-gold-to)]"
                    : "bg-[var(--brand-teal-to)]/15 text-[var(--brand-teal-to)]";
            return (
              <TableRow key={r.id}>
                <TableCell>
                  <input
                    type="checkbox"
                    className="size-4 accent-[var(--brand-violet-to)]"
                    checked={selected.has(r.id)}
                    onChange={() => toggle(r.id)}
                  />
                </TableCell>
                <TableCell className="font-medium text-[var(--ink)]">{r.name}</TableCell>
                <TableCell className="text-[var(--ink-muted)]">{r.hasKeys ? "Keyed" : "Manual"}</TableCell>
                <TableCell>{stock === -1 ? "∞" : stock}</TableCell>
                <TableCell>
                  <Badge className={statusClass}>{statusLabel}</Badge>
                </TableCell>
                <TableCell>
                  <button
                    className="text-xs text-[var(--brand-violet-to)] hover:underline"
                    onClick={() => setEditing(r)}
                  >
                    {r.hasKeys ? "Add codes →" : "Edit stock →"}
                  </button>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>

      {editing && (
        <ProductFormDialog
          product={editing}
          hasKeys={editing.hasKeys}
          unusedKeys={editing.unusedKeys}
          onClose={() => setEditing(null)}
        />
      )}

      <Dialog open={!!confirmAction} onOpenChange={(open) => !pending && !open && setConfirmAction(null)}>
        <DialogContent className="w-full max-w-sm">
          <DialogHeader>
            <DialogTitle>
              {confirmAction && `${BULK_LABELS[confirmAction]} ${selected.size} product(s)?`}
            </DialogTitle>
          </DialogHeader>
          {confirmAction === "clearcodes" && (
            <p className="text-sm text-[var(--ink-muted)]">This can&apos;t be undone.</p>
          )}
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="outline" disabled={pending} onClick={() => setConfirmAction(null)}>
              Cancel
            </Button>
            <Button
              size="sm"
              variant={confirmAction === "reactivate" ? "default" : "destructive"}
              disabled={pending}
              onClick={applyBulk}
            >
              Confirm
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
