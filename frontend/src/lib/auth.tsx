import { type ReactNode, createContext, useContext } from "react";
import type { Level, Me } from "./api";

export const MeContext = createContext<Me | null>(null);
export const useMe = () => useContext(MeContext);

/** Mirrors app.auth.can: edit includes view; admins can do everything. */
export function can(me: Me | null, area: string | null, need: Level = "view") {
  if (!me) return false;
  if (area === null || me.is_admin) return true;
  if (area === "users") return false;
  const lv = me.permissions[area];
  return lv === "edit" || (lv === "view" && need === "view");
}

const AreaContext = createContext<string | null>(null);
/** The part of a page that belongs to `area`: buttons inside it follow that area’s edit permission. */
export function AreaScope({ area, children }: { area: string | undefined; children: ReactNode }) {
  const outer = useContext(AreaContext);
  return <AreaContext.Provider value={area ?? outer}>{children}</AreaContext.Provider>;
}
/** True when this login may look at the area around it (or `area`) but not change it. */
export function useReadOnly(area?: string) {
  const me = useMe();
  const around = useContext(AreaContext);
  const a = area ?? around;
  return a !== null && !can(me, a, "edit");
}

/** The area each page belongs to; null: every logged-in user. "users" is admin only (users and businesses). */
export const PAGE_AREA: Record<string, string | null> = {
  "/": null, "/account": null, "/counts": "counts", "/deliveries": "deliveries", "/sales": "sales",
  "/variance": "variance", "/items": "items", "/items/groups": "items", "/swype-items": "swype_items", "/recipes": "recipes",
  "/imports": "imports", "/settings": "settings", "/users": "users", "/businesses": "users", "/audit": "audit",
};
