"use client";
import { motion } from "motion/react";
import { listContainer } from "@/lib/motion";

export function KpiRow({ children }: { children: React.ReactNode }) {
  return (
    <motion.div
      variants={listContainer}
      initial="hidden"
      animate="show"
      className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4"
    >
      {children}
    </motion.div>
  );
}
