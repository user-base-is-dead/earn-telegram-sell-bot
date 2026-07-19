"use server";
import { revalidatePath } from "next/cache";
import { supabaseServer } from "@/lib/supabase-server";
import { getSession } from "@/lib/session";

export async function resolveDeposit(
  depositId: number,
  userId: number,
  creditedAmountMicro: number,
  note: string,
) {
  const session = await getSession();
  if (!session) throw new Error("Not authenticated");

  const db = supabaseServer();
  const trimmedNote = note.trim();
  const ref = trimmedNote
    ? `manual:${session.telegramId}:${trimmedNote}`
    : `manual:${session.telegramId}`;

  // Guarded UPDATE is the concurrency lock — same pattern as orders/actions.ts's
  // approveOrder/rejectOrder. Only the caller that actually flips pending -> credited
  // proceeds to credit the wallet, so a double-click or two admins racing on the same
  // deposit can't both credit it.
  const { data, error } = await db
    .from("deposits")
    .update({ status: "credited", credited_tx_ref: ref })
    .eq("id", depositId)
    .eq("status", "pending")
    .select("id");
  if (error) throw new Error(error.message);
  if (!data || data.length === 0) throw new Error("This deposit was already resolved");

  const { data: userRow, error: userErr } = await db
    .from("users")
    .select("wallet_balance_micro")
    .eq("user_id", userId)
    .single();
  if (userErr) throw new Error(userErr.message);

  // ponytail: read-then-write balance update, not an atomic increment (Supabase's
  // query builder has no `col = col + x` expression support without an RPC, which the
  // design doc explicitly deferred for this low-frequency admin action). Tiny race
  // window if two admins resolve different deposits for the same user at the same
  // instant. Upgrade path: a Postgres RPC function if manual resolves become frequent.
  const newBalance = (userRow?.wallet_balance_micro ?? 0) + creditedAmountMicro;

  const { error: balErr } = await db
    .from("users")
    .update({ wallet_balance_micro: newBalance })
    .eq("user_id", userId);
  if (balErr) throw new Error(balErr.message);

  const { error: ledgerErr } = await db.from("wallet_ledger").insert({
    user_id: userId,
    delta_micro: creditedAmountMicro,
    reason: "manual_admin_credit",
    ref: `deposit:${depositId}`,
    balance_after_micro: newBalance,
    created_at: new Date().toISOString(),
  });
  if (ledgerErr) throw new Error(ledgerErr.message);

  revalidatePath("/users");
}

export async function rejectDeposit(depositId: number, note: string) {
  const session = await getSession();
  if (!session) throw new Error("Not authenticated");

  const db = supabaseServer();
  const trimmedNote = note.trim();
  const ref = trimmedNote
    ? `rejected:${session.telegramId}:${trimmedNote}`
    : `rejected:${session.telegramId}`;

  // Same guarded-UPDATE concurrency lock as resolveDeposit — only the caller
  // that actually flips pending -> rejected proceeds, so a double-click or two
  // admins racing on the same deposit can't both act on it. No wallet/ledger
  // writes here: nothing was ever credited, so there's nothing to undo.
  const { data, error } = await db
    .from("deposits")
    .update({ status: "rejected", credited_tx_ref: ref })
    .eq("id", depositId)
    .eq("status", "pending")
    .select("id");
  if (error) throw new Error(error.message);
  if (!data || data.length === 0) throw new Error("This deposit was already resolved");

  revalidatePath("/users");
}
