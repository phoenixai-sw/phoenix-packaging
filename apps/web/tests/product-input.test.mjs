import test from "node:test";
import assert from "node:assert/strict";
import { variantInput } from "../src/lib/product-input.ts";
test("product edits keep writable product data and exclude server registration/ACL metadata", () => {
  assert.deepEqual(
    variantInput({
      id: "v1",
      name: "상품",
      barcode: "8801234567893",
      net_quantity: 0,
      net_unit: null,
      barcode_registration: { confirmed_by: "owner" },
      updated_at: "now",
      tenant_id: "secret",
    }),
    {
      id: "v1",
      name: "상품",
      barcode: "8801234567893",
      net_quantity: 0,
      net_unit: null,
    },
  );
});
