import { Skeleton } from "@/components/ui/skeleton";

export default function ProductsLoading() {
  return (
    <div>
      <Skeleton className="h-8 w-32" />
      <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-36 rounded-xl" />
        ))}
      </div>
    </div>
  );
}
