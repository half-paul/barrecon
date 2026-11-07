import { api, n } from "./api";
import { Empty, Loading, Notice, useAsync } from "./ui";

type SoldItem = { code: string; name: string; department: string; category: string; total_qty: number; net_amount: number };

/** Every Swype item one import sold, with quantities. */
export function SoldItems({ url }: { url: string }) {
  const items = useAsync(() => api.get<SoldItem[]>(url), [url]);
  if (items.error) return <Notice kind="error">{items.error}</Notice>;
  if (!items.data) return <Loading />;
  if (!items.data.length) return <Empty>No items: a later import of these dates replaced this one.</Empty>;
  return (
    <table className="grid">
      <thead><tr><th>Item</th><th className="max-sm:hidden">Swype #</th><th className="max-sm:hidden">Category</th><th className="r">Qty</th><th className="r">Net sales</th></tr></thead>
      <tbody>{items.data.map((x) => (
        <tr key={x.code}><td>{x.name}</td><td className="text-muted num max-sm:hidden">{x.code}</td><td className="text-muted max-sm:hidden">{x.category}</td>
          <td className="r num">{n(x.total_qty, 0)}</td><td className="r num">${n(x.net_amount, 2)}</td></tr>))}</tbody>
    </table>
  );
}
