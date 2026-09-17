"use client";
import { useState } from "react";
import { Plus, PencilLine, ImagePlus, LoaderCircle } from "lucide-react";
import { useSession } from "@/components/workspace";
import {
  ManagementPage,
  Feedback,
  Loading,
  Dialog,
  Empty,
} from "@/components/management";
import { useApiData, canEdit, type BrandData } from "@/lib/business";
import { uploadAsset } from "@/lib/assets";
import { api, errorMessage } from "@/lib/api";
import type { ApiSchema } from "@/lib/api-contract";
import { FontManagement } from "@/components/font-management";
export default function Brands() {
  const session = useSession();
  const { data, loading, error, refresh } = useApiData<{ items: BrandData[] }>(
    "/brands",
  );
  const [editing, setEditing] = useState<BrandData | null | undefined>();
  const [name, setName] = useState("");
  const [colors, setColors] = useState("#274631, #E5B18D, #F5F0E5");
  const [logo, setLogo] = useState<string | undefined>();
  const [fontIds, setFontIds] = useState<string[]>([]);
  const { data: fonts, refresh: refreshFonts } = useApiData<ApiSchema<"FontList">>("/fonts");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [formError, setFormError] = useState("");
  function open(brand: BrandData | null) {
    setEditing(brand);
    setName(brand?.name || "");
    setColors(brand?.colors?.join(", ") || "#274631, #E5B18D, #F5F0E5");
    setLogo(brand?.logo_asset_id || undefined);
    setFontIds(brand?.font_asset_ids ?? []);
    setFormError("");
  }
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setFormError("");
    try {
      const palette = colors
        .split(",")
        .map((c) => c.trim())
        .filter(Boolean);
      if (palette.some((c) => !/^#[a-fA-F0-9]{6}$/.test(c)))
        throw new Error("색상은 #274631 형식으로 쉼표로 나눠 입력해 주세요.");
      await api(editing ? `/brands/${editing.id}` : "/brands", {
        method: editing ? "PATCH" : "POST",
        body: JSON.stringify({
          name,
          colors: palette,
          font_ids: ["NotoSansKR"],
          font_asset_ids: fontIds,
          logo_asset_id: logo || null,
        }),
      });
      setEditing(undefined);
      setMessage("브랜드 정보를 저장했습니다.");
      refresh();
    } catch (e) {
      setFormError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function upload(file: File) {
    setBusy(true);
    try {
      const asset = await uploadAsset(file);
      setLogo(asset.id);
    } catch (e) {
      setFormError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <ManagementPage
      eyebrow="A CONSISTENT BRAND"
      title="브랜드 보관함"
      description="색상과 로고를 모아, 제품마다 일관된 인상을 만드세요."
      actions={
        canEdit(session) && (
          <button className="button button-orange" onClick={() => open(null)}>
            <Plus size={17} /> 브랜드 등록
          </button>
        )
      }
    >
      <Feedback error={error} notice={message} />
      {loading ? (
        <Loading />
      ) : !data?.items.length ? (
        <Empty>
          아직 등록한 브랜드가 없습니다. 브랜드명과 색상을 먼저 정해 보세요.
        </Empty>
      ) : (
        <div className="brand-management-grid">
          {data.items.map((brand) => (
            <article className="management-card" key={brand.id}>
              <div className="brand-card-logo">
                {brand.logo_asset_id ? (
                  <img
                    src={`/api/v1/assets/${brand.logo_asset_id}/content`}
                    alt={`${brand.name} 로고`}
                  />
                ) : (
                  <span>{brand.name.slice(0, 1)}</span>
                )}
              </div>
              <h2>{brand.name}</h2>
              <div className="brand-colors">
                {brand.colors?.map((color) => (
                  <span
                    key={color}
                    style={{ background: color }}
                    title={color}
                  />
                ))}
              </div>
              <p>{brand.font_ids?.join(", ") || "NotoSansKR"}{brand.font_asset_ids.length > 0 ? ` · 등록 글꼴 ${brand.font_asset_ids.length}개` : ""}</p>
              {canEdit(session) && (
                <button
                  className="button button-light button-sm"
                  onClick={() => open(brand)}
                >
                  <PencilLine size={14} /> 브랜드 편집
                </button>
              )}
            </article>
          ))}
        </div>
      )}
      <FontManagement catalog={fonts} readOnly={!canEdit(session)} onChanged={refreshFonts} />
      {editing !== undefined && (
        <Dialog
          title={editing ? "브랜드 편집" : "새 브랜드"}
          onClose={() => setEditing(undefined)}
        >
          <form onSubmit={save}>
            <label className="field">
              브랜드명
              <input
                required
                maxLength={120}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label className="field">
              브랜드 색상
              <textarea
                rows={2}
                value={colors}
                onChange={(e) => setColors(e.target.value)}
              />
            </label>
            <label className="field">
              로고 이미지 (PNG · JPG · WebP · SVG)
              <input
                type="file"
                accept="image/png,image/jpeg,image/webp,image/svg+xml"
                disabled={busy}
                onChange={(e) => {
                  if (e.target.files?.[0]) void upload(e.target.files[0]);
                }}
              />
            </label>
            <p className="field-hint">SVG는 1MiB 이하 정적 윤곽선을 PNG로 변환합니다. SVG 글자는 윤곽선으로 바꾸고 외부 이미지·스크립트는 제거해 주세요.</p>
            {logo && (
              <img
                className="logo-form-preview"
                src={`/api/v1/assets/${logo}/content`}
                alt="업로드한 로고"
              />
            )}
            <fieldset disabled={busy} style={{ border: 0, padding: 0 }}><legend>새 문구에 허용할 등록 글꼴</legend>
              {fonts?.items.map(font => <label className="checkbox-label" key={font.id}><input type="checkbox" checked={fontIds.includes(font.id)} onChange={e => setFontIds(ids => e.target.checked ? [...ids, font.id] : ids.filter(id => id !== font.id))} />{font.family} · {font.weight}</label>)}
              <p className="field-hint">기본 Noto Sans KR은 항상 사용 가능합니다. 허용 해제는 새 선택에만 적용하며 기존 디자인·저장본의 글꼴은 유지합니다.</p>
            </fieldset>
            <Feedback error={formError} />
            <button className="button button-dark full-width" disabled={busy}>
              {busy ? (
                <LoaderCircle className="spin" size={17} />
              ) : (
                "브랜드 저장"
              )}
            </button>
          </form>
        </Dialog>
      )}
    </ManagementPage>
  );
}
