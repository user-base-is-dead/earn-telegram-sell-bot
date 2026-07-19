"use client";
import { useEffect, useRef } from "react";

export default function LoginPage() {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const script = document.createElement("script");
    script.src = "https://telegram.org/js/telegram-widget.js?22";
    script.async = true;
    script.setAttribute("data-telegram-login", process.env.NEXT_PUBLIC_BOT_USERNAME!);
    script.setAttribute("data-size", "large");
    script.setAttribute("data-auth-url", `${process.env.NEXT_PUBLIC_SITE_URL}/api/auth/telegram`);
    script.setAttribute("data-request-access", "write");
    containerRef.current?.appendChild(script);

    return () => {
      if (containerRef.current) {
        containerRef.current.innerHTML = "";
      }
    };
  }, []);

  return (
    <main
      className="flex min-h-dvh items-center justify-center"
      style={{
        background:
          "radial-gradient(circle at 30% 20%, color-mix(in oklch, var(--brand-violet-to) 12%, var(--bg)), var(--bg) 60%)",
      }}
    >
      <div
        className="flex flex-col items-center gap-6 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-10"
        style={{ boxShadow: "var(--glow-violet)" }}
      >
        <h1 className="font-heading text-2xl font-semibold text-[var(--ink)]">Admin sign-in</h1>
        <p className="max-w-xs text-center text-sm text-[var(--ink-muted)]">
          Sign in with the Telegram account that owns this store.
        </p>
        <div ref={containerRef} />
      </div>
    </main>
  );
}
