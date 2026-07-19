import { getSession } from "@/lib/session";
import { supabaseServer } from "@/lib/supabase-server";

export async function GET() {
  const session = await getSession();
  if (!session) {
    return new Response("Unauthorized", { status: 401 });
  }

  const encoder = new TextEncoder();
  let close: () => void = () => {};

  const stream = new ReadableStream({
    start(controller) {
      const send = (event: string) => controller.enqueue(encoder.encode(`data: ${event}\n\n`));

      const db = supabaseServer();
      const channel = db
        .channel("admin-panel-changes")
        .on("postgres_changes", { event: "*", schema: "public", table: "orders" }, () => send("orders"))
        .on("postgres_changes", { event: "*", schema: "public", table: "products" }, () => send("products"))
        .on("postgres_changes", { event: "*", schema: "public", table: "deposits" }, () => send("deposits"))
        .subscribe();

      const ping = setInterval(() => controller.enqueue(encoder.encode(": ping\n\n")), 20_000);
      close = () => {
        clearInterval(ping);
        db.removeChannel(channel);
      };
    },
    cancel() {
      close();
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    },
  });
}
