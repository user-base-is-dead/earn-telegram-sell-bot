"use server";
import { revalidatePath } from "next/cache";
import { supabaseServer } from "@/lib/supabase-server";

export type ProductInput = {
  name: string;
  description: string;
  price: number;
  content: string;
  stock: number;
};

export async function createProduct(input: ProductInput) {
  const db = supabaseServer();
  const { error } = await db.from("products").insert({
    ...input,
    active: 1,
    created_at: new Date().toISOString(),
  });
  if (error) throw new Error(error.message);
  revalidatePath("/products");
}

export async function updateProduct(id: number, input: Partial<ProductInput>) {
  const db = supabaseServer();
  const { error } = await db.from("products").update(input).eq("id", id);
  if (error) throw new Error(error.message);
  revalidatePath("/products");
}

export async function setProductActive(id: number, active: boolean) {
  const db = supabaseServer();
  const { error } = await db.from("products").update({ active: active ? 1 : 0 }).eq("id", id);
  if (error) throw new Error(error.message);
  revalidatePath("/products");
}

export async function addProductKeys(id: number, codes: string[]) {
  const db = supabaseServer();
  const { error } = await db.from("product_keys").insert(codes.map((code) => ({ product_id: id, code })));
  if (error) throw new Error(error.message);
  revalidatePath("/products");
}

export async function clearProductKeys(id: number): Promise<{ deleted: number }> {
  const db = supabaseServer();
  const { error, count } = await db
    .from("product_keys")
    .delete({ count: "exact" })
    .eq("product_id", id)
    .eq("used", 0);
  if (error) throw new Error(error.message);
  revalidatePath("/products");
  return { deleted: count ?? 0 };
}

export async function deleteProduct(id: number): Promise<{ deactivated: boolean }> {
  const db = supabaseServer();
  const { error } = await db.from("products").delete().eq("id", id);
  if (error) {
    // Postgres FK violation (23503): this product has past orders referencing
    // it, so a hard delete would break order history — deactivate instead,
    // mirroring the bot's own /delete fallback (app/handlers/products_admin.py).
    if (error.code === "23503") {
      const { error: deactivateErr } = await db.from("products").update({ active: 0 }).eq("id", id);
      if (deactivateErr) throw new Error(deactivateErr.message);
      revalidatePath("/products");
      return { deactivated: true };
    }
    throw new Error(error.message);
  }
  revalidatePath("/products");
  return { deactivated: false };
}
