import { useEffect, useState } from "react";

/** Header rows of long lists stick to the top of the page while it scrolls (index.css: table.grid thead th, .lg-head).
 *
 * Two things make that work:
 * - `--sticky-top` on <html>: the height of the app's own sticky bar, so a header row sits under it. A bar hidden
 *   with display:none measures 0.
 * - `.table-box` scrolls sideways only while its table is wider than the box (class `scrolls`). A scrolling box is a
 *   scroll container, and a header inside one sticks to the box, not the page; so a table that fits gets no
 *   scrolling and its header sticks, and a table too wide for a phone keeps its sideways scroll.
 */
/** Returns a ref callback for the sticky bar; measures it while it is on the page. */
export function useStickyTop() {
  const [el, setEl] = useState<HTMLElement | null>(null);
  useEffect(() => {
    if (!el) return;
    const set = () => document.documentElement.style.setProperty("--sticky-top", `${el.offsetHeight}px`);
    const ro = new ResizeObserver(set);
    ro.observe(el);
    window.addEventListener("resize", set);
    set();
    return () => { ro.disconnect(); window.removeEventListener("resize", set); document.documentElement.style.removeProperty("--sticky-top"); };
  }, [el]);
  return setEl;
}

export function useWideTables() {
  useEffect(() => {
    const boxes = new Set<HTMLElement>();
    const check = (box: HTMLElement) => {
      const table = box.firstElementChild as HTMLElement | null;
      box.classList.toggle("scrolls", !!table && table.scrollWidth > box.clientWidth + 1);
    };
    const ro = new ResizeObserver((entries) => entries.forEach((e) => check((e.target as HTMLElement).closest(".table-box") ?? (e.target as HTMLElement))));
    const scan = () => document.querySelectorAll<HTMLElement>(".table-box").forEach((box) => {
      if (boxes.has(box)) return;
      boxes.add(box);
      ro.observe(box);
      if (box.firstElementChild) ro.observe(box.firstElementChild);
      check(box);
    });
    const mo = new MutationObserver(() => {
      boxes.forEach((b) => { if (!b.isConnected) { ro.unobserve(b); boxes.delete(b); } });
      scan();
    });
    mo.observe(document.body, { childList: true, subtree: true });
    scan();
    return () => { mo.disconnect(); ro.disconnect(); };
  }, []);
}
