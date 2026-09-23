import assert from "node:assert/strict";
import test from "node:test";
import { closeBlockingModals } from "../src/lib/payment-window.ts";

function dialog(open) {
  return {
    open,
    close() {
      this.open = false;
    },
  };
}

function fakeDocument(dialogs) {
  return {
    querySelectorAll(selector) {
      assert.equal(selector, "dialog[open]");
      return dialogs.filter((item) => item.open);
    },
  };
}

test("an open modal dialog is closed before the payment window opens", () => {
  // A <dialog> opened with showModal() renders in the top layer, above the payment window's
  // z-index, so leaving one open shows the customer a spinner and nothing else.
  const confirmation = dialog(true);
  assert.equal(closeBlockingModals(fakeDocument([confirmation])), 1);
  assert.equal(confirmation.open, false);
});

test("every open dialog is closed and already-closed ones are left alone", () => {
  const dialogs = [dialog(true), dialog(false), dialog(true)];
  assert.equal(closeBlockingModals(fakeDocument(dialogs)), 2);
  assert.deepEqual(
    dialogs.map((item) => item.open),
    [false, false, false],
  );
  assert.equal(closeBlockingModals(fakeDocument(dialogs)), 0);
});

test("a page with no dialogs, or an element that cannot close, is not an error", () => {
  assert.equal(closeBlockingModals(fakeDocument([])), 0);
  assert.equal(closeBlockingModals({ querySelectorAll: () => [{ open: true }] }), 0);
});
