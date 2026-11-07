import { createContext } from "react";

/** What the header offers in place of Start count while an editable draft count is open. */
export type CountBar = { post: () => void; cancel: () => void; busy: boolean };

/** Set by the open count page, cleared when it closes. */
export const CountBarContext = createContext<(bar: CountBar | null) => void>(() => {});
