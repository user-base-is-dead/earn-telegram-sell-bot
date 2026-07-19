"use server";
import { revalidatePath } from "next/cache";
import { supabaseServer } from "@/lib/supabase-server";

export async function approveOrder(orderId: number) {
  const db = supabaseServer();
  const { data, error } = await db
    .from("orders")
    .update({ status: "approved", updated_at: new Date().toISOString() })
    .eq("id", orderId)
    .eq("status", "pending_review")
    .select("id");
  if (error) throw new Error(error.message);
  if (!data || data.length === 0) throw new Error("Order was already handled by someone else");
  revalidatePath("/orders");
}

export async function rejectOrder(orderId: number, reason: string) {
  const db = supabaseServer();
  const { data, error } = await db
    .from("orders")
    .update({ status: "rejected", reason, updated_at: new Date().toISOString() })
    .eq("id", orderId)
    .eq("status", "pending_review")
    .select("id");
  if (error) throw new Error(error.message);
  if (!data || data.length === 0) throw new Error("Order was already handled by someone else");
  revalidatePath("/orders");
}
