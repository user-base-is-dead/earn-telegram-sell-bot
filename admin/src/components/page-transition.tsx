"use client";
import { usePathname } from "next/navigation";
import { motion, AnimatePresence } from "motion/react";
import { DURATION, EASE_OUT_QUART } from "@/lib/motion";

export function PageTransition({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={pathname}
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -8 }}
        transition={{ duration: DURATION.page, ease: EASE_OUT_QUART }}
      >
        {children}
      </motion.div>
    </AnimatePresence>
  );
}
