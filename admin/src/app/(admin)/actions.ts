"use server";
import { redirect } from "next/navigation";
import { clearSession } from "@/lib/session";

export async function signOutAction() {
  await clearSession();
  redirect("/login");
}
