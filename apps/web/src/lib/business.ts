"use client";
import { useCallback, useEffect, useState } from "react";
import { api, errorMessage, type Session } from "./api";
export type Role = "owner" | "editor" | "viewer";
export function roleOf(session: Session | null): Role {
  return session?.role || session?.user.role || "viewer";
}
export function canEdit(session: Session | null) {
  return ["owner", "editor"].includes(roleOf(session));
}
export type BrandData = {
  id: string;
  name: string;
  colors: string[];
  font_ids: string[];
  logo_asset_id?: string | null;
};
export type VariantData = {
  id: string;
  name: string;
  sku?: string;
  barcode?: string;
  net_weight?: string;
  net_quantity?: number | null;
  net_unit?: "g" | "kg" | "ml" | "l" | "ea" | null;
  ingredients?: string;
  allergens?: string;
  storage?: string;
  manufacturer?: string;
};
export type ProductData = {
  id: string;
  name: string;
  brand_id?: string;
  description?: string;
  variants: VariantData[];
};
export type WorkspaceData = { id: string; name: string; description?: string };
export function useApiData<T>(path: string) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((v) => v + 1), []);
  useEffect(() => {
    let active = true;
    setError("");
    setLoading(true);
    api<T>(path)
      .then((value) => {
        if (active) setData(value);
      })
      .catch((e) => {
        if (active) setError(errorMessage(e));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [path, revision]);
  return { data, setData, error, setError, loading, refresh };
}
export const money = (value: number) => `₩${value.toLocaleString("ko-KR")}`;
export const dateTime = (value?: string | null) =>
  value ? new Date(value).toLocaleString("ko-KR") : "—";
