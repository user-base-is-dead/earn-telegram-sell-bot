"use server";
import { revalidatePath } from "next/cache";
import { supabaseServer } from "@/lib/supabase-server";
import { getSession } from "@/lib/session";

export async function updateUserNote(userId: number, notes: string) {
  const session = await getSession();
  if (!session) throw new Error("Not authenticated");

  const db = supabaseServer();
  const { error } = await db.from("users").update({ notes }).eq("user_id", userId);
  if (error) throw new Error(error.message);

  revalidatePath(`/users/${userId}`);
}
