"use client";
import { useState } from "react";
import { Plus, PencilLine, LoaderCircle, Trash2 } from "lucide-react";
import { useSession } from "@/components/workspace";
import {
  ManagementPage,
  Feedback,
  Loading,
  Dialog,
  Empty,
} from "@/components/management";
import {
  useApiData,
  canEdit,
  type ProductData,
  type VariantData,
  type BrandData,
} from "@/lib/business";
import { api, errorMessage } from "@/lib/api";
const emptyVariant = () => ({
  name: "기본 상품",
  sku: "",
  barcode: "",
  net_weight: "",
  ingredients: "",
  allergens: "",
  storage: "",
  manufacturer: "",
});
export default function Products() {
  const session = useSession();
  const { data, loading, error, refresh } = useApiData<{
    items: ProductData[];
  }>("/products");
  const { data: brands } = useApiData<{ items: BrandData[] }>("/brands");
  const [editing, setEditing] = useState<ProductData | null | undefined>();
  const [name, setName] = useState("");
  const [brand, setBrand] = useState("");
  const [description, setDescription] = useState("");
  const [variants, setVariants] = useState<Array<Partial<VariantData>>>([
    emptyVariant(),
  ]);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState("");
  function open(product: ProductData | null) {
    setEditing(product);
    setName(product?.name || "");
    setBrand(product?.brand_id || "");
    setDescription(product?.description || "");
    setVariants(
      product?.variants?.length ? product.variants : [emptyVariant()],
    );
    setFormError("");
  }
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setFormError("");
    try {
      await api(editing ? `/products/${editing.id}` : "/products", {
        method: editing ? "PATCH" : "POST",
        body: JSON.stringify({
          name,
          brand_id: brand || null,
          description,
          variants,
        }),
      });
      setEditing(undefined);
      refresh();
    } catch (e) {
      setFormError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <ManagementPage
      eyebrow="EVERY PRODUCT, IN ONE PLACE"
      title="상품과 변형"
      description="제품명과 고객이 확정한 표시사항을 관리하세요. 영양·효능 정보는 자동 추정하지 않습니다."
      actions={
        canEdit(session) && (
          <button className="button button-orange" onClick={() => open(null)}>
            <Plus size={17} /> 상품 등록
          </button>
        )
      }
    >
      <Feedback error={error} />
      {loading ? (
        <Loading />
      ) : !data?.items.length ? (
        <Empty>브랜드의 첫 상품을 등록해 보세요.</Empty>
      ) : (
        <div className="management-table-wrap">
          <table className="management-table">
            <thead>
              <tr>
                <th>상품명</th>
                <th>브랜드</th>
                <th>변형</th>
                <th>바코드</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.items.map((product) => (
                <tr key={product.id}>
                  <td>
                    <strong>{product.name}</strong>
                    <small>{product.description}</small>
                  </td>
                  <td>
                    {brands?.items.find((b) => b.id === product.brand_id)
                      ?.name || "—"}
                  </td>
                  <td>{product.variants?.map((v) => v.name).join(" · ")}</td>
                  <td>
                    {product.variants
                      ?.map((v) => v.barcode)
                      .filter(Boolean)
                      .join(" · ") || "확인 필요"}
                  </td>
                  <td>
                    {canEdit(session) && (
                      <button
                        className="icon-button"
                        onClick={() => open(product)}
                        aria-label={`${product.name} 편집`}
                      >
                        <PencilLine size={17} />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {editing !== undefined && (
        <Dialog
          title={editing ? "상품 편집" : "새 상품"}
          onClose={() => setEditing(undefined)}
        >
          <form onSubmit={save}>
            <div className="form-two-columns">
              <label className="field">
                상품명
                <input
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  maxLength={160}
                />
              </label>
              <label className="field">
                브랜드
                <select
                  value={brand}
                  onChange={(e) => setBrand(e.target.value)}
                >
                  <option value="">선택 안 함</option>
                  {brands?.items.map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <label className="field">
              상품 설명
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={2}
              />
            </label>
            {variants.map((variant, index) => (
              <fieldset className="variant-form" key={variant.id || index}>
                <legend>상품 변형 {index + 1}</legend>
                <div className="form-two-columns">
                  {(
                    [
                      ["name", "변형 이름"],
                      ["sku", "SKU"],
                      ["barcode", "소유한 EAN-13 번호"],
                      ["net_weight", "중량·용량"],
                      ["ingredients", "원재료"],
                      ["allergens", "알레르기"],
                      ["storage", "보관 방법"],
                      ["manufacturer", "제조사"],
                    ] as const
                  ).map(([key, label]) => (
                    <label className="field" key={key}>
                      {label}
                      <input
                        required={key === "name"}
                        value={variant[key] || ""}
                        onChange={(e) =>
                          setVariants((items) =>
                            items.map((v, i) =>
                              i === index ? { ...v, [key]: e.target.value } : v,
                            ),
                          )
                        }
                      />
                    </label>
                  ))}
                  <label className="field">
                    제작 기준 내용량
                    <input
                      type="number"
                      min="0.001"
                      step="0.001"
                      value={variant.net_quantity ?? ""}
                      onChange={(e) =>
                        setVariants((items) =>
                          items.map((v, i) =>
                            i === index
                              ? {
                                  ...v,
                                  net_quantity: e.target.value
                                    ? Number(e.target.value)
                                    : null,
                                }
                              : v,
                          ),
                        )
                      }
                    />
                  </label>
                  <label className="field">
                    내용량 단위
                    <select
                      value={variant.net_unit || ""}
                      onChange={(e) =>
                        setVariants((items) =>
                          items.map((v, i) =>
                            i === index
                              ? {
                                  ...v,
                                  net_unit:
                                    (e.target
                                      .value as VariantData["net_unit"]) ||
                                    null,
                                }
                              : v,
                          ),
                        )
                      }
                    >
                      <option value="">미확정</option>
                      {["g", "kg", "ml", "l", "ea"].map((unit) => (
                        <option key={unit} value={unit}>
                          {unit}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                {!variant.id && variants.length > 1 && (
                  <button
                    type="button"
                    className="text-link"
                    onClick={() =>
                      setVariants((items) =>
                        items.filter((_, i) => i !== index),
                      )
                    }
                  >
                    <Trash2 size={13} /> 입력 중인 변형 제거
                  </button>
                )}
              </fieldset>
            ))}
            <button
              type="button"
              className="button button-light button-sm"
              onClick={() =>
                setVariants((v) => [...v, { ...emptyVariant(), name: "" }])
              }
            >
              <Plus size={15} /> 상품 변형 추가
            </button>
            <p className="field-hint">
              빈 항목은 미확정으로 남습니다. 기존 디자인의 연결 문구는
              편집기에서 변경 내용을 확인한 뒤 적용하세요.
            </p>
            <Feedback error={formError} />
            <button className="button button-dark full-width" disabled={busy}>
              {busy ? <LoaderCircle className="spin" size={17} /> : "상품 저장"}
            </button>
          </form>
        </Dialog>
      )}
    </ManagementPage>
  );
}
