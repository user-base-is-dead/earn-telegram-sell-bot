"use client";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { ProductFormDialog } from "@/components/product-form-dialog";

export function NewProductButton() {
  const [creating, setCreating] = useState(false);

  return (
    <>
      <Button size="sm" onClick={() => setCreating(true)}>
        New product
      </Button>
      {creating && <ProductFormDialog onClose={() => setCreating(false)} />}
    </>
  );
}
