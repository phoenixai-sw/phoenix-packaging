/** Never echo read-only registration/audit data into strict variant write schemas. */
export function variantInput(
  value: Record<string, unknown>,
): Record<string, unknown> {
  const allowed = [
    "id",
    "name",
    "sku",
    "barcode",
    "net_weight",
    "net_quantity",
    "net_unit",
    "ingredients",
    "allergens",
    "storage",
    "manufacturer",
  ];
  return Object.fromEntries(
    allowed
      .filter((key) => value[key] !== undefined)
      .map((key) => [key, value[key]]),
  );
}
