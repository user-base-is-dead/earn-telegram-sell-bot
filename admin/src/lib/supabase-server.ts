import "server-only";
import { createClient } from "@supabase/supabase-js";

// Service-role key: full read/write, bypasses RLS. Never imported into a Client
// Component — the `server-only` import above makes that a build error if it happens.
export function supabaseServer() {
  return createClient(
    process.env.SUPABASE_URL!,
    process.env.SUPABASE_SERVICE_ROLE_KEY!,
    { auth: { persistSession: false } },
  );
}
