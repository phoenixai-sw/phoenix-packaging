type ClosableDialog = { close?: () => void };

/** A `<dialog>` opened with `showModal()` lives in the browser's top layer, which sits above every
 *  z-index — including the Toss payment window (`z-index: 9999999`). Leaving one open hides the
 *  payment window completely, so the customer sees nothing but the caller's spinner. Callers should
 *  drop their own dialog state as well; this is the safety net for the ones that forget. */
export function closeBlockingModals(doc: {
  querySelectorAll: (selector: string) => ArrayLike<ClosableDialog>;
}): number {
  let closed = 0;
  for (const dialog of Array.from(doc.querySelectorAll("dialog[open]"))) {
    if (typeof dialog.close !== "function") continue;
    dialog.close();
    closed += 1;
  }
  return closed;
}
