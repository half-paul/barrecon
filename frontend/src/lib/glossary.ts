// Plain-language meanings of the stock words used across the portal. One place, so every "?" says the same thing.

export const GLOSSARY = {
  stock_unit: "Counted in: the unit stock is measured in. ml for spirits and wine, each for beer bottles and cans, kg or g for food.",
  pack: "Pack: the thing you count on the shelf and pay for one of: a 750 ml bottle, one can of beer, a 2.5 kg bag. Stock and cost are kept in packs.",
  pack_size: "Pack size: how much one pack holds, in the counted unit. 750 for a 750 ml bottle; 1 for a beer can counted each.",
  case: "Case (or flat): a box of packs the supplier sells together, e.g. 12 bottles or 24 cans. The same beer can come in a 12 case and a 24 case under different store SKUs.",
  case_size: "Per case: how many packs are in this item's usual case. Used to turn \"2 cases\" into packs when entering by hand. A receipt line keeps its own count in Packs each, because a 12 and a 24 case have different SKUs.",
  cost_per_pack: "Cost / pack: what one pack cost before tax. From a receipt it is the line amount ÷ packs, so a $48 case of 24 is $2.00 a pack.",
  sku: "SKU: the product number. On a receipt it is the store's number as printed; each case size has its own.",
  receipt_qty: "Qty: how many the receipt says were bought, as printed: often cases, sometimes single bottles.",
  packs_each: "Packs each: how many packs one receipt Qty holds. 1 if the receipt lists single bottles; 12 or 24 if it lists cases. Remembered for this store's SKU, so each case size keeps its own number.",
  packs: "Packs: what goes into stock = Qty × Packs each.",
  loose_packs: "Loose packs: single packs on top of full cases.",
  amount: "Amount: the line total on the receipt, after line discounts, before tax.",
  not_stock: "Not stock: bags, deposits, snacks. Kept so the receipt total still adds up, but nothing goes into stock.",
  par: "Par: the stock level you want on hand, in packs.",
} as const;

export type Term = keyof typeof GLOSSARY;
