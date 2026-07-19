import { supabaseServer } from "@/lib/supabase-server";
import { ProductCard } from "@/components/product-card";
import { NewProductButton } from "@/components/new-product-button";
import { StockTable } from "@/components/stock-table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export const dynamic = "force-dynamic";

export default async function ProductsPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string }>;
}) {
  const { q } = await searchParams;
  const db = supabaseServer();
  const { data: products } = await db.from("products").select("*").order("id");
  const { data: keyCounts } = await db.from("product_keys").select("product_id, used");

  const unusedByProduct = new Map<number, number>();
  const hasKeysByProduct = new Set<number>();
  for (const row of keyCounts ?? []) {
    hasKeysByProduct.add(row.product_id);
    if (row.used === 0) {
      unusedByProduct.set(row.product_id, (unusedByProduct.get(row.product_id) ?? 0) + 1);
    }
  }

  const needle = q?.toLowerCase();
  const filtered = (products ?? []).filter(
    (p) => !needle || p.name.toLowerCase().includes(needle)
  );

  const stockRows = filtered.map((p) => ({
    ...p,
    hasKeys: hasKeysByProduct.has(p.id),
    unusedKeys: unusedByProduct.get(p.id) ?? 0,
  }));

  return (
    <div>
      <div className="flex justify-end">
        <NewProductButton />
      </div>
      <Tabs defaultValue="all" className="mt-6">
        <TabsList>
          <TabsTrigger value="all">All products</TabsTrigger>
          <TabsTrigger value="stock">Stock</TabsTrigger>
        </TabsList>
        <TabsContent value="all" className="mt-6">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {filtered.map((p) => (
              <ProductCard
                key={p.id}
                product={p}
                unusedKeys={unusedByProduct.get(p.id) ?? 0}
                hasKeys={hasKeysByProduct.has(p.id)}
              />
            ))}
            {filtered.length === 0 && (
              <p className="text-sm text-[var(--ink-muted)]">
                {q ? `No products match "${q}".` : "No products yet."}
              </p>
            )}
          </div>
        </TabsContent>
        <TabsContent value="stock" className="mt-6">
          <StockTable products={stockRows} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
