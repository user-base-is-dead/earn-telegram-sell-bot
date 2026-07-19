"use client";
import { useLiveUpdates } from "@/hooks/use-live-updates";

export function LiveRefresh({ watch }: { watch: string[] }) {
  useLiveUpdates(watch);
  return null;
}
