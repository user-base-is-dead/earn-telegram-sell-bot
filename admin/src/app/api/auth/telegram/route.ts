import { createHash, createHmac, timingSafeEqual } from "crypto";
import { NextRequest, NextResponse } from "next/server";
import { createSession } from "@/lib/session";

function verifyTelegramAuth(data: Record<string, string>): boolean {
  const { hash, ...rest } = data;
  if (!hash) return false;

  const checkString = Object.keys(rest)
    .sort()
    .map((key) => `${key}=${rest[key]}`)
    .join("\n");

  const secretKey = createHash("sha256").update(process.env.TELEGRAM_BOT_TOKEN!).digest();
  const computedHash = createHmac("sha256", secretKey).update(checkString).digest("hex");

  const a = Buffer.from(computedHash, "hex");
  const b = Buffer.from(hash, "hex");
  return a.length === b.length && timingSafeEqual(a, b);
}

export async function GET(req: NextRequest) {
  const params = Object.fromEntries(req.nextUrl.searchParams.entries());

  if (!verifyTelegramAuth(params)) {
    return NextResponse.redirect(new URL("/login?error=invalid_signature", req.url));
  }

  // auth_date freshness: reject widget payloads older than 5 minutes.
  const authDate = Number(params.auth_date) * 1000;
  if (Date.now() - authDate > 5 * 60 * 1000) {
    return NextResponse.redirect(new URL("/login?error=stale", req.url));
  }

  const telegramId = Number(params.id);
  const adminIds = (process.env.ADMIN_TELEGRAM_IDS ?? "")
    .split(",")
    .map((s) => Number(s.trim()))
    .filter(Boolean);

  if (!adminIds.includes(telegramId)) {
    return NextResponse.redirect(new URL("/login?error=not_admin", req.url));
  }

  await createSession(telegramId);
  return NextResponse.redirect(new URL("/dashboard", req.url));
}
