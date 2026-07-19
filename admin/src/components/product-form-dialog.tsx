"use client";
import { useState, useTransition } from "react";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { createProduct, updateProduct, addProductKeys } from "@/app/(admin)/products/actions";

export function ProductFormDialog({
  product,
  hasKeys = false,
  unusedKeys = 0,
  onClose,
}: {
  product?: any;
  hasKeys?: boolean;
  unusedKeys?: number;
  onClose: () => void;
}) {
  const isEdit = !!product;
  const [form, setForm] = useState({
    name: product?.name ?? "",
    description: product?.description ?? "",
    price: product?.price ?? 0,
    price_inr: product?.price_inr ?? 0,
    content: product?.content ?? "",
    stock: product?.stock ?? 0,
  });
  const [keysText, setKeysText] = useState("");
  const [saving, startSaving] = useTransition();
  const [addingKeys, startAddingKeys] = useTransition();

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] w-full max-w-2xl overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{isEdit ? `Edit ${product.name}` : "New product"}</DialogTitle>
        </DialogHeader>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5 sm:col-span-2">
            <Label htmlFor="name">Name</Label>
            <Input id="name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </div>
          <div className="flex flex-col gap-1.5 sm:col-span-2">
            <Label htmlFor="description">Description</Label>
            <textarea
              id="description"
              className="w-full rounded-md border border-[var(--border)] bg-[var(--bg)] p-2 text-sm"
              rows={3}
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="price_inr">Price (INR)</Label>
            <Input
              id="price_inr"
              type="number"
              value={form.price_inr}
              onChange={(e) => setForm({ ...form, price_inr: Number(e.target.value) })}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="price">Price (USDT)</Label>
            <Input
              id="price"
              type="number"
              value={form.price}
              onChange={(e) => setForm({ ...form, price: Number(e.target.value) })}
            />
          </div>
          <div className="flex flex-col gap-1.5 sm:col-span-2">
            <Label htmlFor="stock">{hasKeys ? "Stock" : "Stock (-1 = unlimited)"}</Label>
            {hasKeys ? (
              <p className="text-sm text-[var(--ink-muted)]">
                {unusedKeys} unused keys — managed via &quot;Add delivery keys&quot; below, not
                editable here.
              </p>
            ) : (
              <Input
                id="stock"
                type="number"
                value={form.stock}
                onChange={(e) => setForm({ ...form, stock: Number(e.target.value) })}
              />
            )}
          </div>
          <div className="flex flex-col gap-1.5 sm:col-span-2">
            <Label htmlFor="content">Delivery content (secret shown to buyer)</Label>
            <textarea
              id="content"
              className="w-full rounded-md border border-[var(--border)] bg-[var(--bg)] p-2 text-sm"
              rows={3}
              value={form.content}
              onChange={(e) => setForm({ ...form, content: e.target.value })}
            />
          </div>
          <Button
            className="sm:col-span-2"
            disabled={saving}
            onClick={() =>
              startSaving(async () => {
                try {
                  // hasKeys products don't submit `stock` — it's derived from the key pool,
                  // not this form, so there's nothing meaningful to save for it here.
                  // eslint-disable-next-line @typescript-eslint/no-unused-vars
                  const { stock, ...rest } = form;
                  const payload = hasKeys ? rest : form;
                  if (isEdit) {
                    await updateProduct(product.id, payload);
                  } else {
                    await createProduct(form);
                  }
                  onClose();
                } catch (e) {
                  toast.error(e instanceof Error ? e.message : "Failed to save product");
                }
              })
            }
          >
            Save
          </Button>
          {isEdit && (
            <div className="mt-2 flex flex-col gap-1.5 border-t border-[var(--border)] pt-3 sm:col-span-2">
              <Label htmlFor="keys">Add delivery keys (one per line)</Label>
              <textarea
                id="keys"
                className="w-full rounded-md border border-[var(--border)] bg-[var(--bg)] p-2 text-sm"
                rows={4}
                value={keysText}
                onChange={(e) => setKeysText(e.target.value)}
              />
              <Button
                size="sm"
                variant="outline"
                className="mt-2"
                disabled={addingKeys}
                onClick={() =>
                  startAddingKeys(async () => {
                    const codes = keysText.split("\n").map((s) => s.trim()).filter(Boolean);
                    // dedupe exact-duplicate lines within this paste; doesn't catch dupes across separate paste sessions
                    const uniqueCodes = [...new Set(codes)];
                    if (!uniqueCodes.length) return;
                    try {
                      await addProductKeys(product.id, uniqueCodes);
                      setKeysText("");
                    } catch (e) {
                      toast.error(e instanceof Error ? e.message : "Failed to add keys");
                    }
                  })
                }
              >
                Add keys
              </Button>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
