"use client";
import { useState, useTransition } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { updateUserNote } from "@/app/(admin)/users/notes-actions";

export function UserNotes({ userId, initialNotes }: { userId: number; initialNotes: string }) {
  const [notes, setNotes] = useState(initialNotes);
  const [pending, startTransition] = useTransition();

  function save() {
    startTransition(async () => {
      try {
        await updateUserNote(userId, notes);
        toast.success("Note saved");
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Failed to save note");
      }
    });
  }

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <p className="mb-2 text-sm font-medium text-[var(--ink)]">Notes</p>
      <textarea
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        placeholder="e.g. disputed a charge once, watch for repeat"
        rows={3}
        className="w-full resize-y rounded-lg border border-input bg-transparent px-2.5 py-1.5 text-sm text-[var(--ink)] outline-none placeholder:text-[var(--ink-muted)] focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
      />
      <Button size="sm" className="mt-2" disabled={pending} onClick={save}>
        Save note
      </Button>
    </div>
  );
}
