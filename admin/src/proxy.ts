import { NextRequest, NextResponse } from "next/server";
import { getSession } from "@/lib/session";

// Next.js 16 renamed the `middleware` file convention to `proxy` (same
// behavior/config shape, new file name + export name).
export async function proxy(req: NextRequest) {
  const session = await getSession();
  if (!session) {
    return NextResponse.redirect(new URL("/login", req.url));
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/dashboard/:path*", "/orders/:path*", "/products/:path*", "/users/:path*"],
};
