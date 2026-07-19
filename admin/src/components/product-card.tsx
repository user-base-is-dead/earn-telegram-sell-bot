"use client";
import { useState, useTransition } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { deleteProduct, setProductActive } from "@/app/(admin)/products/actions";
import { ProductFormDialog } from "@/components/product-form-dialog";

export function ProductCard({
  product,
  unusedKeys,
  hasKeys,
}: {
  product: any;
  unusedKeys: number;
  hasKeys: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [pending, startTransition] = useTransition();
  const [deletePending, startDeleteTransition] = useTransition();
  const [optimisticActive, setOptimisticActive] = useState<boolean | null>(null);
  const active = optimisticActive ?? product.active === 1;

  function confirmDelete() {
    startDeleteTransition(async () => {
      try {
        const { deactivated } = await deleteProduct(product.id);
        toast(deactivated ? "Has past orders — deactivated instead of deleted." : "Product deleted.");
        setDeleting(false);
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Failed to delete product");
      }
    });
  }

  // For key-backed ("automatic") products, stock is the real unused-key count,
  // not the manually-typed field — that field only means something for
  // reseller/"manual" products with no key pool.
  const effectiveStock = hasKeys ? unusedKeys : product.stock;
  const lowStock = effectiveStock !== -1 && effectiveStock <= 5;
  const statusLabel = !active ? "Inactive" : lowStock ? "Low stock" : "In stock";
  const statusClass = !active
    ? "bg-[var(--ink-muted)]/15 text-[var(--ink-muted)]"
    : lowStock
      ? "bg-[var(--brand-coral-to)]/15 text-[var(--brand-coral-to)]"
      : "bg-[var(--brand-teal-to)]/15 text-[var(--brand-teal-to)]";

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-5 transition-transform hover:-translate-y-0.5 hover:shadow-md">
      <div className="flex items-start justify-between">
        <div>
          <p className="font-medium text-[var(--ink)]">{product.name}</p>
          <p className="text-sm text-[var(--ink-muted)]">
            ₹{product.price_inr} · ${product.price} · stock{" "}
            {effectiveStock === -1 ? "∞" : effectiveStock}
          </p>
        </div>
        <Switch
          checked={active}
          disabled={pending}
          onCheckedChange={(checked) => {
            setOptimisticActive(checked);
            startTransition(async () => {
              try {
                await setProductActive(product.id, checked);
              } catch (e) {
                setOptimisticActive(null);
                toast.error(e instanceof Error ? e.message : "Failed to update product");
              }
            });
          }}
        />
      </div>
      <div className="mt-3 flex items-center justify-between">
        <Badge className={statusClass}>{statusLabel}</Badge>
        <p className="text-xs text-[var(--ink-muted)]">{unusedKeys} unused keys</p>
      </div>
      <div className="mt-3 flex gap-2">
        <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
          Edit
        </Button>
        <Button size="sm" variant="destructive" onClick={() => setDeleting(true)}>
          Delete
        </Button>
      </div>
      {editing && (
        <ProductFormDialog
          product={product}
          hasKeys={hasKeys}
          unusedKeys={unusedKeys}
          onClose={() => setEditing(false)}
        />
      )}
      <Dialog open={deleting} onOpenChange={(open) => !deletePending && setDeleting(open)}>
        <DialogContent className="w-full max-w-sm">
          <DialogHeader>
            <DialogTitle>Delete {product.name}?</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-[var(--ink-muted)]">
            If it has past orders it will be deactivated (hidden) instead, so order history stays
            intact.
          </p>
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="outline" disabled={deletePending} onClick={() => setDeleting(false)}>
              Cancel
            </Button>
            <Button size="sm" variant="destructive" disabled={deletePending} onClick={confirmDelete}>
              Delete
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
