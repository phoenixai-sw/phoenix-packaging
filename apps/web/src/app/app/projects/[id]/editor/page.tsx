"use client";
import { use, useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import {
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  Check,
  ChevronDown,
  Download,
  Eye,
  EyeOff,
  FileText,
  ImagePlus,
  Info,
  Layers3,
  LoaderCircle,
  Minus,
  Plus,
  Redo2,
  RotateCcw,
  Save,
  ShieldCheck,
  Trash2,
  Type,
  Undo2,
  X,
  ZoomIn,
  Square,
  Sparkles,
  Box,
  Barcode,
  Link2,
  History,
} from "lucide-react";
import { api, ApiError, errorMessage } from "@/lib/api";
import { useSession } from "@/components/workspace";
import { canEdit, useApiData } from "@/lib/business";
import { assetImageDimensions, uploadAsset } from "@/lib/assets";
import { Dialog } from "@/components/management";
import { AIStudio } from "@/components/ai-studio";
import { StructureTools } from "@/components/structure-tools";
import { BindingTools } from "@/components/binding-tools";
import { ExportTools } from "@/components/export-tools";
import { TextHistory } from "@/components/text-history";
import type { PackagingPreviewProps } from "@preview3d/PackagingPreview";
import type { FaceStructure } from "@editor/structure";
import { readRecovery, writeRecovery, type Recovery } from "@/lib/recovery";
import {
  addText,
  applyImageBackground,
  containImage,
  faceSafeRegion,
  initialImagePlacement,
  moveLayer,
  sendLayerToBack,
  removeObject,
  updateObject,
  safeWarnings,
  roundMM,
  type Project,
  type Scene,
  type SceneObject,
} from "@editor/model";
const Canvas = dynamic(() => import("@editor/canvas"), {
  ssr: false,
  loading: () => (
    <div className="loading-state">
      <LoaderCircle className="spin" /> 편집기를 준비하고 있어요.
    </div>
  ),
});
const PackagingPreview = dynamic(() => import("@preview3d/PackagingPreview"), {
  ssr: false,
});
type SaveStatus = "saved" | "dirty" | "saving" | "error" | "conflict";
export default function EditorPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const session = useSession();
  const readOnly = !canEdit(session);
  const uploadConfig = useApiData<{ upload_max_bytes: number }>("/config");
  const [panel, setPanel] = useState<
    "ai" | "structure" | "bindings" | "exports" | "3d" | "history" | null
  >(null);
  const [project, setProject] = useState<Project | null>(null);
  const [scene, setScene] = useState<Scene | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [mobilePane, setMobilePane] = useState<
    "canvas" | "tools" | "properties"
  >("canvas");
  const [faceId, setFaceId] = useState("front");
  const [status, setStatus] = useState<SaveStatus>("saved");
  const [saveError, setSaveError] = useState("");
  const [zoom, setZoom] = useState(1);
  const [guides, setGuides] = useState(true);
  const [historyCount, setHistoryCount] = useState({ past: 0, future: 0 });
  const [editing, setEditing] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [fittingImage, setFittingImage] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportMessage, setExportMessage] = useState("");
  const [exportId, setExportId] = useState<string | null>(null);
  const [recovery, setRecovery] = useState<Recovery>();
  const [retry, setRetry] = useState(0);
  const [saveTick, setSaveTick] = useState(0);
  const current = useRef<Scene | null>(null);
  const revision = useRef(0);
  const savedJSON = useRef("");
  const past = useRef<Scene[]>([]);
  const future = useRef<Scene[]>([]);
  const saving = useRef<Promise<Project | null> | null>(null);
  const conflict = useRef(false);
  const mounted = useRef(true);
  const activeProjectId = useRef(id);
  activeProjectId.current = id;
  const uploadInput = useRef<HTMLInputElement>(null);
  const recoveryKey = `${session?.user.id}:${id}`;
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    let active = true;
    setError("");
    api<Project>(`/projects/${id}`)
      .then(async (p) => {
        if (!active) return;
        setProject(p);
        setScene(p.scene);
        current.current = p.scene;
        revision.current = p.base_revision;
        savedJSON.current = JSON.stringify(p.scene);
        setFaceId(p.scene.active_face_id || p.scene.faces[0].id);
        conflict.current = false;
        setStatus("saved");
        past.current = [];
        future.current = [];
        setHistoryCount({ past: 0, future: 0 });
        const draft = await readRecovery(recoveryKey);
        if (
          active &&
          draft &&
          JSON.stringify(draft.scene) !== savedJSON.current
        )
          setRecovery(draft);
      })
      .catch((e) => {
        if (active) setError(errorMessage(e));
      });
    return () => {
      active = false;
    };
  }, [id, retry, recoveryKey]);
  function commit(next: Scene, confirmationOnly = false) {
    if (readOnly) return;
    if (
      !current.current ||
      JSON.stringify(next) === JSON.stringify(current.current)
    )
      return;
    if (!confirmationOnly)
      next = { ...next, reviewed_face_ids: [], confirmed_fields: [] };
    past.current = [...past.current.slice(-79), current.current];
    future.current = [];
    current.current = next;
    setScene(next);
    setHistoryCount({ past: past.current.length, future: 0 });
    if (!conflict.current) setStatus("dirty");
    void writeRecovery(recoveryKey, {
      scene: next,
      base_revision: revision.current,
      updated_at: Date.now(),
    });
  }
  function undo() {
    if (readOnly) return;
    const previous = past.current.pop();
    if (!previous || !current.current) return;
    future.current.push(current.current);
    current.current = previous;
    setScene(previous);
    setStatus(conflict.current ? "conflict" : "dirty");
    setHistoryCount({
      past: past.current.length,
      future: future.current.length,
    });
    void writeRecovery(recoveryKey, {
      scene: previous,
      base_revision: revision.current,
      updated_at: Date.now(),
    });
  }
  function redo() {
    if (readOnly) return;
    const next = future.current.pop();
    if (!next || !current.current) return;
    past.current.push(current.current);
    current.current = next;
    setScene(next);
    setStatus(conflict.current ? "conflict" : "dirty");
    setHistoryCount({
      past: past.current.length,
      future: future.current.length,
    });
    void writeRecovery(recoveryKey, {
      scene: next,
      base_revision: revision.current,
      updated_at: Date.now(),
    });
  }
  const saveNow = useCallback(async (): Promise<Project | null> => {
    while (saving.current) {
      await saving.current;
      if (conflict.current) return null;
    }
    if (!current.current || conflict.current) return null;
    const snapshot = current.current;
    const json = JSON.stringify(snapshot);
    if (json === savedJSON.current) return null;
    setStatus("saving");
    setSaveError("");
    const task = api<Project>(`/projects/${id}/draft`, {
      method: "PATCH",
      body: JSON.stringify({
        base_revision: revision.current,
        scene: snapshot,
      }),
    })
      .then((p) => {
        revision.current = p.base_revision;
        savedJSON.current = json;
        if (mounted.current) {
          setProject(p);
          const unchanged = JSON.stringify(current.current) === json;
          setStatus(unchanged ? "saved" : "dirty");
          if (unchanged) void writeRecovery(recoveryKey, null);
          setSaveTick((n) => n + 1);
        }
        return p;
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 409) {
          conflict.current = true;
          setStatus("conflict");
          setSaveError(
            "다른 창에서 이 프로젝트를 수정했습니다. 내 편집 내용은 이 브라우저에 보관되어 있습니다.",
          );
        } else {
          setStatus("error");
          setSaveError(errorMessage(e));
        }
        return null;
      })
      .finally(() => {
        saving.current = null;
      });
    saving.current = task;
    return task;
  }, [id, recoveryKey]);
  async function saveCurrent(): Promise<number> {
    if (editing) throw new Error("텍스트 편집을 마친 뒤 다시 시도해 주세요.");
    await saveNow();
    if (
      conflict.current ||
      JSON.stringify(current.current) !== savedJSON.current
    )
      throw new Error(
        "현재 변경 내용을 먼저 저장해 주세요. 저장 오류나 충돌을 확인하세요.",
      );
    return revision.current;
  }
  function onServerProject(p: Project) {
    current.current = p.scene;
    revision.current = p.base_revision;
    savedJSON.current = JSON.stringify(p.scene);
    setProject(p);
    setScene(p.scene);
    setStatus("saved");
    setSaveError("");
    setSelected(null);
    past.current = [];
    future.current = [];
    setHistoryCount({ past: 0, future: 0 });
    void writeRecovery(recoveryKey, null);
  }
  async function useGeneratedAsset(asset: { id: string; width_px?: number; height_px?: number }) {
    if (!current.current || readOnly) return;
    const targetFaceId = faceId;
    const dimensions = asset.width_px && asset.height_px
      ? { width_px: asset.width_px, height_px: asset.height_px }
      : await assetImageDimensions(asset.id);
    if (!mounted.current || activeProjectId.current !== id || !current.current || !current.current.faces.some((f) => f.id === targetFaceId)) return;
    // Preserve intervening edits; only the target face's previous AI background is replaced.
    commit(applyImageBackground(current.current, targetFaceId, { id: asset.id, ...dimensions }));
  }
  useEffect(() => {
    if (
      !scene ||
      editing ||
      conflict.current ||
      JSON.stringify(scene) === savedJSON.current
    )
      return;
    const timer = setTimeout(() => {
      void saveNow();
    }, 1100);
    return () => clearTimeout(timer);
  }, [scene, editing, saveNow, saveTick]);
  useEffect(() => {
    const reconnect = () => {
      if (!conflict.current) void saveNow();
    };
    window.addEventListener("online", reconnect);
    const guard = (e: BeforeUnloadEvent) => {
      if (
        current.current &&
        JSON.stringify(current.current) !== savedJSON.current
      ) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", guard);
    return () => {
      window.removeEventListener("online", reconnect);
      window.removeEventListener("beforeunload", guard);
    };
  }, [saveNow]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.isComposing || editing) return;
      const target = e.target as HTMLElement;
      if (
        ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) ||
        target.isContentEditable
      )
        return;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        e.shiftKey ? redo() : undo();
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void saveNow();
      }
      if (
        (e.key === "Delete" || e.key === "Backspace") &&
        selected &&
        current.current
      ) {
        e.preventDefault();
        commit(removeObject(current.current, selected));
        setSelected(null);
      }
      if (e.key === "Escape") setSelected(null);
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  });
  const setEditingStable = useCallback(
    (value: boolean) => setEditing(value),
    [],
  );
  function change(id: string, patch: Partial<SceneObject>) {
    if ("text" in patch || "barcode_value" in patch)
      patch = { ...patch, binding_key: undefined };
    if (current.current) commit(updateObject(current.current, id, patch));
  }
  function addTextLayer() {
    if (!current.current) return;
    try {
      const result = addText(current.current, faceId, geometryFace?.regions?.safe);
      commit(result.scene);
      setSelected(result.id);
      setMobilePane("properties");
    } catch (e) {
      setSaveError(errorMessage(e));
    }
  }
  async function fitImageLayer(objectId: string) {
    if (!current.current || readOnly || fittingImage) return;
    const targetFaceId = faceId;
    const original = current.current.faces.find((face) => face.id === targetFaceId)?.objects.find((object) => object.id === objectId);
    if (original?.type !== "image" || !original.asset_id) return;
    setFittingImage(objectId);
    setSaveError("");
    try {
      const dimensions = await assetImageDimensions(original.asset_id);
      const target = current.current?.faces.find((face) => face.id === targetFaceId);
      const latest = target?.objects.find((object) => object.id === objectId);
      if (!mounted.current || activeProjectId.current !== id || !target || !latest || latest.asset_id !== original.asset_id) return;
      change(objectId, containImage({ x_mm: 0, y_mm: 0, width_mm: target.width_mm, height_mm: target.height_mm }, dimensions.width_px, dimensions.height_px));
    } catch (e) {
      if (mounted.current) setSaveError(errorMessage(e));
    } finally {
      if (mounted.current) setFittingImage(null);
    }
  }
  function addShape() {
    if (!current.current) return;
    const face = current.current.faces.find((f) => f.id === faceId)!;
    const object: SceneObject = {
      id: crypto.randomUUID(),
      type: "shape",
      face_id: faceId,
      x_mm: 20,
      y_mm: 20,
      width_mm: 50,
      height_mm: 40,
      rotation_deg: 0,
      z_index: Math.max(0, ...face.objects.map((o) => o.z_index)) + 1,
      color: "#e5ab75",
      visible: true,
      print_enabled: true,
    };
    commit({
      ...current.current,
      faces: current.current.faces.map((f) =>
        f.id === faceId ? { ...f, objects: [...f.objects, object] } : f,
      ),
    });
    setSelected(object.id);
  }
  async function demoBackground(palette: "forest" | "citrus" | "berry") {
    if (!current.current || readOnly) return;
    const targetFaceId = faceId;
    setUploading(true);
    setSaveError("");
    try {
      const asset = await api<{ id: string }>(
        `/projects/${id}/demo-background`,
        { method: "POST", body: JSON.stringify({ palette }) },
      );
      const target = current.current.faces.find((f) => f.id === targetFaceId)!;
      const object: SceneObject = {
        id: `demo-background-${targetFaceId}`,
        type: "image",
        face_id: targetFaceId,
        x_mm: 0,
        y_mm: 0,
        width_mm: target.width_mm,
        height_mm: target.height_mm,
        rotation_deg: 0,
        z_index: -100,
        asset_id: asset.id,
        visible: true,
        print_enabled: true,
        locked: true,
      };
      commit({
        ...current.current,
        faces: current.current.faces.map((f) =>
          f.id === targetFaceId
            ? {
                ...f,
                objects: [
                  object,
                  ...f.objects.filter((o) => o.id !== object.id),
                ],
              }
            : f,
        ),
      });
      setSelected(null);
    } catch (e) {
      setSaveError(errorMessage(e));
    } finally {
      setUploading(false);
    }
  }
  async function upload(file: File) {
    if (!current.current || readOnly) return;
    setUploading(true);
    setSaveError("");
    try {
      const asset = await uploadAsset(file, id);
      const dimensions = asset.width_px && asset.height_px
        ? { width_px: asset.width_px, height_px: asset.height_px }
        : await assetImageDimensions(asset.id);
      if (!mounted.current || activeProjectId.current !== id || !current.current) return;
      const face = current.current.faces.find((f) => f.id === faceId)!;
      const object: SceneObject = {
        id: crypto.randomUUID(),
        type: "image",
        face_id: faceId,
        ...initialImagePlacement(faceSafeRegion(current.current, face, geometryFace?.regions?.safe), dimensions.width_px, dimensions.height_px),
        z_index: Math.min(10000, Math.max(0, ...face.objects.map((o) => o.z_index)) + 1),
        asset_id: asset.id,
        visible: true,
        print_enabled: true,
      };
      commit({
        ...current.current,
        faces: current.current.faces.map((f) =>
          f.id === faceId ? { ...f, objects: [...f.objects, object] } : f,
        ),
      });
      setSelected(object.id);
    } catch (e) {
      setSaveError(errorMessage(e));
    } finally {
      setUploading(false);
      if (uploadInput.current) uploadInput.current.value = "";
    }
  }
  async function exportPDF() {
    if (!project || !current.current) return;
    setExporting(true);
    setExportMessage("변경 내용을 저장하고 있어요.");
    setExportId(null);
    try {
      await saveNow();
      if (
        conflict.current ||
        JSON.stringify(current.current) !== savedJSON.current
      )
        throw new Error(
          "저장되지 않은 변경이 있습니다. 저장 문제를 해결한 후 출력해 주세요.",
        );
      const result = await api<{ id: string; job_id?: string; status: string }>(
        "/exports",
        {
          method: "POST",
          body: JSON.stringify({
            project_id: id,
            base_revision: revision.current,
          }),
        },
      );
      const jobId = result.job_id || result.id;
      if (result.status === "failed")
        await api(`/jobs/${jobId}/retry`, { method: "POST" });
      setExportMessage(
        "검토용 PDF를 준비하고 있어요. 창을 닫아도 서버에서 작업을 계속합니다.",
      );
      for (let attempts = 0; attempts < 60; attempts++) {
        const job = await api<{
          id: string;
          status: string;
          export_id?: string;
          error?: string | { message?: string };
          message?: string;
        }>(`/jobs/${jobId}`);
        if (job.status === "succeeded") {
          setExportId(job.export_id || result.id);
          setExportMessage("앞면과 뒷면을 담은 검토용 PDF가 준비되었습니다.");
          return;
        }
        if (["failed", "canceled"].includes(job.status))
          throw new Error(
            job.message ||
              (typeof job.error === "string"
                ? job.error
                : job.error?.message) ||
              "PDF를 준비하지 못했습니다. 다시 시도해 주세요.",
          );
        await new Promise((resolve) => setTimeout(resolve, 2000));
        if (!mounted.current) return;
      }
      setExportMessage(
        `출력 시간이 길어지고 있어요. 작업 번호: ${jobId}. 잠시 후 다시 확인해 주세요.`,
      );
    } catch (e) {
      setExportMessage(errorMessage(e));
    } finally {
      if (mounted.current) setExporting(false);
    }
  }
  function downloadDraft() {
    if (!current.current) return;
    const blob = new Blob(
      [
        JSON.stringify(
          {
            project_id: id,
            base_revision: revision.current,
            scene: current.current,
          },
          null,
          2,
        ),
      ],
      { type: "application/json" },
    );
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `phoenix-${id}-recovery.json`;
    a.click();
    URL.revokeObjectURL(url);
  }
  function restoreDraft() {
    if (!recovery || !current.current) return;
    const changedServer = recovery.base_revision !== revision.current;
    commit(recovery.scene);
    setRecovery(undefined);
    if (changedServer) {
      conflict.current = true;
      setStatus("conflict");
      setSaveError(
        "복구본을 만든 이후 서버 버전이 변경됐습니다. 복구본을 내려받은 뒤 서버 버전과 비교해 주세요.",
      );
    }
  }
  if (error)
    return (
      <div className="editor-load-error">
        <Link href="/app" className="text-link">
          <ArrowLeft /> 내 프로젝트
        </Link>
        <h2>프로젝트를 열 수 없어요.</h2>
        <p role="alert">{error}</p>
        <button
          className="button button-dark"
          onClick={() => setRetry((v) => v + 1)}
        >
          다시 시도
        </button>
      </div>
    );
  if (!project || !scene)
    return (
      <div className="loading-state full-height">
        <LoaderCircle className="spin" /> 프로젝트를 불러오고 있어요.
      </div>
    );
  const face = scene.faces.find((f) => f.id === faceId) || scene.faces[0];
  const geometry = project.geometry as unknown as
    | PackagingPreviewProps["geometry"]
    | undefined;
  const geometryFace = (project.geometry?.faces as FaceStructure[] | undefined)?.find((f) => f.id === face.id);
  const object = face.objects.find((o) => o.id === selected);
  const warnings = safeWarnings(face, geometryFace?.regions?.safe);
  const saveLabel = {
    saved: "모든 변경 저장됨",
    dirty: "저장 대기 중",
    saving: "저장 중…",
    error: "연결 확인 필요",
    conflict: "저장 충돌",
  }[status];
  return (
    <main className="editor-page">
      <header className="editor-topbar">
        <Link
          href="/app"
          className="editor-back icon-button"
          aria-label="내 프로젝트"
          onClick={(e) => {
            if (
              JSON.stringify(current.current) !== savedJSON.current &&
              !confirm(
                "아직 서버에 저장되지 않은 변경이 있습니다. 프로젝트 목록으로 이동할까요?",
              )
            )
              e.preventDefault();
          }}
        >
          <ArrowLeft size={20} />
        </Link>
        <div className="editor-project-title">
          <h1>{project.name}</h1>
          <span>
            {{
              "three-side-seal": "3면 실링 봉투",
              "stand-up-pouch": "스탠드 파우치",
              "folding-box": "접이식 상자",
            }[project.template_id] || "패키지"}{" "}
            <i>·</i> {project.width_mm} × {project.height_mm} mm
          </span>
        </div>
        <div className={`save-status save-${status}`} role="status">
          {status === "saving" ? (
            <LoaderCircle className="spin" size={14} />
          ) : status === "saved" ? (
            <Check size={14} />
          ) : (
            <span className="status-dot" />
          )}
          {saveLabel}
        </div>
        <div className="editor-top-actions">
          <Link
            className="icon-button"
            href={`/app/projects/${id}/exports`}
            target="_blank"
            title="파일 이력 (새 창)"
            aria-label="파일 이력"
          >
            <FileText size={18} />
          </Link>
          <button
            className="icon-button"
            title="지금 저장 (Ctrl+S)"
            aria-label="지금 저장"
            onClick={() => void saveNow()}
            disabled={
              readOnly ||
              status === "saving" ||
              status === "conflict" ||
              editing
            }
          >
            <Save size={18} />
          </button>
          <button
            className="button button-dark button-sm"
            disabled={editing || status === "conflict"}
            onClick={() => setPanel("exports")}
          >
            {exporting ? (
              <LoaderCircle className="spin" size={16} />
            ) : (
              <Download size={16} />
            )}{" "}
            검수와 출력
          </button>
        </div>
      </header>
      <div className="editor-disclaimer">
        <Info size={14} />
        <span>
          {readOnly
            ? "열람 권한"
            : String(scene.template_version_id || "").includes("demo")
              ? "데모 구조 · 제조사 미승인"
              : "제조 조건 확인 필요"}
        </span>
        <span className="disclaimer-divider">|</span>
        <span>
          제작용 출력은 승인된 도면·프로필과 출력 검수를 통과해야 합니다.
        </span>
      </div>
      {recovery && (
        <div className="recovery-bar">
          <RotateCcw size={17} />
          <span>이 브라우저에서 저장되지 않은 편집 내용을 찾았습니다.</span>
          <button onClick={restoreDraft}>복구본 열기</button>
          <button
            onClick={() => {
              setRecovery(undefined);
              void writeRecovery(recoveryKey, null);
            }}
          >
            서버 버전 유지
          </button>
        </div>
      )}
      {saveError && (
        <div
          className={`editor-message ${status === "conflict" ? "conflict-message" : ""}`}
          role="alert"
        >
          <Info size={16} />
          <span>{saveError}</span>
          {status === "conflict" ? (
            <>
              <button onClick={downloadDraft}>내 편집 파일로 보관</button>
              <button
                onClick={() => {
                  if (
                    confirm(
                      "현재 편집 내용은 복구 파일로 먼저 내려받는 것을 권장합니다. 서버에 저장된 버전을 불러올까요?",
                    )
                  ) {
                    setSaveError("");
                    setRecovery(undefined);
                    setRetry((n) => n + 1);
                  }
                }}
              >
                서버 버전 불러오기
              </button>
            </>
          ) : (
            <button
              onClick={() => {
                setSaveError("");
                void saveNow();
              }}
            >
              다시 시도
            </button>
          )}
        </div>
      )}
      <nav className="mobile-editor-tabs" aria-label="편집 도구 선택">
        <button
          aria-pressed={mobilePane === "canvas"}
          onClick={() => setMobilePane("canvas")}
        >
          <Eye size={17} /> 디자인
        </button>
        <button
          aria-pressed={mobilePane === "tools"}
          onClick={() => setMobilePane("tools")}
        >
          <Layers3 size={17} /> 요소·레이어
        </button>
        <button
          aria-pressed={mobilePane === "properties"}
          onClick={() => setMobilePane("properties")}
        >
          <Type size={17} /> {object ? "선택 속성" : "디자인 설정"}
        </button>
      </nav>
      <div className="editor-layout" data-mobile-pane={mobilePane}>
        <aside className="editor-left">
          <div className="panel-section face-section">
            <h2>
              인쇄면 <span>{scene.faces.length}</span>
            </h2>
            <div className="face-list">
              {scene.faces.map((f) => (
                <button
                  key={f.id}
                  className={`face-button ${face.id === f.id ? "selected" : ""}`}
                  onClick={() => {
                    setFaceId(f.id);
                    setSelected(null);
                    setMobilePane("canvas");
                  }}
                >
                  <span
                    className="face-preview"
                    style={{ background: f.background }}
                  >
                    <span>
                      {f.objects
                        .find((o) => o.type === "text")
                        ?.text?.slice(0, 5) || f.name}
                    </span>
                  </span>
                  <span>
                    {f.name || { front: "앞면", back: "뒷면" }[f.id] || f.id}
                  </span>
                  {face.id === f.id && <Check size={14} />}
                </button>
              ))}
            </div>
          </div>
          <div className="panel-section tools-section">
            <h2>디자인 요소</h2>
            <div className="add-tools">
              <button onClick={addTextLayer} disabled={readOnly}>
                <Type size={22} />
                <span>텍스트</span>
              </button>
              <button
                onClick={() => uploadInput.current?.click()}
                disabled={uploading || readOnly}
              >
                {uploading ? (
                  <LoaderCircle className="spin" size={22} />
                ) : (
                  <ImagePlus size={22} />
                )}
                <span>이미지</span>
              </button>
              <button onClick={addShape} disabled={readOnly}>
                <Square size={22} />
                <span>도형</span>
              </button>
            </div>
            <input
              ref={uploadInput}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              className="visually-hidden"
              onChange={(e) => {
                if (e.target.files?.[0]) void upload(e.target.files[0]);
              }}
            />
            <p className="panel-hint">
              PNG · JPG · WebP{" "}
              {uploadConfig.data &&
                `/ 최대 ${uploadConfig.data.upload_max_bytes / 1024 / 1024} MiB`}
            </p>
            <div className="editor-feature-tools">
              <button onClick={() => setPanel("ai")}>
                <Sparkles size={17} /> AI 디자인 시안
              </button>
              <button onClick={() => setPanel("structure")}>
                <Barcode size={17} /> 바코드와 가공
              </button>
              <button onClick={() => setPanel("bindings")}>
                <Link2 size={17} /> 상품 연결·복제
              </button>
              <button onClick={() => setPanel("history")}>
                <History size={17} /> 텍스트 변경 기록
              </button>
              <button onClick={() => setPanel("3d")} disabled={!geometry}>
                <Box size={17} /> 3D 조립 미리보기
              </button>
            </div>
          </div>
          <div className="panel-section layers-section">
            <h2>
              <Layers3 size={15} /> 레이어 <span>{face.objects.length}</span>
            </h2>
            <div className="layer-list">
              {[...face.objects]
                .sort((a, b) => b.z_index - a.z_index)
                .map((layer) => (
                  <div
                    className={`layer-row ${layer.id === selected ? "selected" : ""}`}
                    key={layer.id}
                  >
                    <button
                      className="layer-main"
                      onClick={() => {
                        setSelected(layer.id);
                        setMobilePane("properties");
                      }}
                    >
                      {layer.type === "text" ? (
                        <Type size={15} />
                      ) : layer.type === "image" ? (
                        <ImagePlus size={15} />
                      ) : (
                        <Square size={15} />
                      )}
                      <span>
                        {layer.type === "text"
                          ? layer.text || "빈 텍스트"
                          : layer.type === "image"
                            ? "업로드 이미지"
                            : layer.type === "barcode"
                              ? `EAN-13 ${layer.barcode_value}`
                              : "도형"}
                      </span>
                    </button>
                    <button
                      className="layer-visible"
                      disabled={readOnly}
                      aria-label={`${layer.visible === false ? "표시" : "숨기기"}: ${layer.text || layer.type}`}
                      onClick={() =>
                        change(layer.id, { visible: layer.visible === false })
                      }
                    >
                      {layer.visible === false ? (
                        <EyeOff size={14} />
                      ) : (
                        <Eye size={14} />
                      )}
                    </button>
                  </div>
                ))}
              {!face.objects.length && (
                <p className="panel-hint">텍스트와 이미지를 추가해 보세요.</p>
              )}
            </div>
          </div>
          <div className="editor-left-bottom">
            <ShieldCheck size={16} />
            <span>검토용 프로젝트</span>
          </div>
        </aside>
        <section className="editor-center">
          <div className="mobile-face-switcher" aria-label="인쇄면 선택">
            {scene.faces.map((f) => (
              <button
                key={f.id}
                aria-pressed={face.id === f.id}
                onClick={() => {
                  setFaceId(f.id);
                  setSelected(null);
                  setZoom(1);
                }}
              >
                {f.name}
              </button>
            ))}
            <button
              className="mobile-preview-button"
              onClick={() => setPanel("3d")}
              disabled={!geometry}
            >
              <Box size={16} /> 3D
            </button>
          </div>
          <div className="canvas-toolbar">
            <div className="history-controls">
              <button
                className="icon-button"
                title="실행 취소"
                aria-label="실행 취소"
                disabled={!historyCount.past || editing}
                onClick={undo}
              >
                <Undo2 size={18} />
              </button>
              <button
                className="icon-button"
                title="다시 실행"
                aria-label="다시 실행"
                disabled={!historyCount.future || editing}
                onClick={redo}
              >
                <Redo2 size={18} />
              </button>
              <span className="toolbar-divider" />
              <span className="canvas-face-label">{face.name}</span>
            </div>
            <label className="guide-toggle">
              <input
                type="checkbox"
                checked={guides}
                onChange={(e) => setGuides(e.target.checked)}
              />{" "}
              안전영역 표시
            </label>
          </div>
          <Canvas
            face={face}
            holes={scene.holes}
            geometryFace={geometryFace}
            readOnly={readOnly}
            selected={selected}
            onSelect={setSelected}
            onChange={change}
            zoom={zoom}
            guides={guides}
            onEditState={setEditingStable}
          />
          <div className="canvas-bottom-bar">
            <div className="canvas-legend">
              <span>
                <i className="seal-line" />
                실링
              </span>
              <span>
                <i className="safe-line" />
                안전영역
              </span>
            </div>
            <div className="zoom-controls">
              <button
                className="icon-button"
                aria-label="축소"
                onClick={() => setZoom((v) => Math.max(0.4, roundMM(v - 0.1)))}
              >
                <Minus size={15} />
              </button>
              <button
                className="zoom-value"
                onClick={() => setZoom(1)}
                title="화면에 맞추기"
              >
                {Math.round(zoom * 100)}%
              </button>
              <button
                className="icon-button"
                aria-label="확대"
                onClick={() => setZoom((v) => Math.min(2.5, roundMM(v + 0.1)))}
              >
                <Plus size={15} />
              </button>
            </div>
          </div>
          <p className="canvas-hint">
            글자를 두 번 클릭하면 직접 편집할 수 있어요. Enter 줄바꿈 ·
            Ctrl+Enter 편집 완료
          </p>
        </section>
        <aside className="editor-right">
          <fieldset className="editor-properties-fieldset" disabled={readOnly}>
            <div className="panel-section">
              <h2>
                {object
                  ? object.type === "text"
                    ? "텍스트 속성"
                    : object.type === "image"
                      ? "이미지 속성"
                      : object.type === "barcode"
                        ? "바코드 속성"
                        : "도형 속성"
                  : "디자인 설정"}
              </h2>
              {object ? (
                <>
                  <div className="property-object-actions">
                    <span>
                      {object.type === "text" ? (
                        <Type size={17} />
                      ) : object.type === "image" ? (
                        <ImagePlus size={17} />
                      ) : (
                        <Square size={17} />
                      )}{" "}
                      선택한 레이어
                    </span>
                    <button
                      className="icon-button danger"
                      aria-label="선택한 레이어 삭제"
                      onClick={() => {
                        commit(removeObject(scene, object.id));
                        setSelected(null);
                      }}
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                  {object.type === "text" && (
                    <>
                      <label className="field property-field">
                        상품 정보 연결
                        <select
                          value={object.binding_key || ""}
                          onChange={(e) =>
                            change(object.id, {
                              binding_key: e.target.value || undefined,
                            })
                          }
                        >
                          <option value="">직접 편집하는 문구</option>
                          {Object.entries({
                            brand_name: "브랜드명",
                            product_name: "상품명",
                            variant_name: "변형명",
                            net_weight: "중량·용량",
                            ingredients: "원재료",
                            allergens: "알레르기",
                            manufacturer: "제조사",
                            storage: "보관 방법",
                          }).map(([key, label]) => (
                            <option key={key} value={key}>
                              {label}
                            </option>
                          ))}
                        </select>
                        <small>
                          상품 연결·복제에서 변경을 확인하고 반영합니다. 문구를
                          직접 수정하면 연결이 해제됩니다.
                        </small>
                      </label>
                      <label className="field property-field">
                        문구
                        <textarea
                          rows={4}
                          value={object.text || ""}
                          onChange={(e) =>
                            change(object.id, { text: e.target.value })
                          }
                          onCompositionStart={() => setEditing(true)}
                          onCompositionEnd={() => setEditing(false)}
                          onBlur={() => setEditing(false)}
                        />
                      </label>
                      <label className="field property-field">
                        글꼴
                        <select value="NotoSansKR" disabled>
                          <option>NotoSansKR</option>
                        </select>
                      </label>
                      <label className="field property-field">
                        글자 굵기
                        <select value={object.font_weight ?? 400} onChange={(event) => change(object.id, { font_weight: Number(event.target.value) as 400 | 700 })}>
                          <option value={400}>보통 · Regular</option>
                          <option value={700}>굵게 · Bold</option>
                        </select>
                        <small>캔버스·3D·PDF에 같은 굵기 글꼴을 사용합니다.</small>
                      </label>
                      <div className="property-row">
                        <label className="field property-field">
                          크기 (pt)
                          <input
                            type="number"
                            min={4}
                            max={180}
                            value={object.font_size_pt || 16}
                            onChange={(e) => {
                              const n = Number(e.target.value);
                              if (n >= 4 && n <= 180)
                                change(object.id, { font_size_pt: n });
                            }}
                          />
                        </label>
                        <label className="field property-field">
                          정렬
                          <select
                            value={object.align || "left"}
                            onChange={(e) =>
                              change(object.id, {
                                align: e.target.value as SceneObject["align"],
                              })
                            }
                          >
                            <option value="left">왼쪽</option>
                            <option value="center">가운데</option>
                            <option value="right">오른쪽</option>
                          </select>
                        </label>
                      </div>
                    </>
                  )}
                  {object.type === "image" && object.asset_id && (
                    <div className="field property-field">
                      <button
                        type="button"
                        className="button button-light button-sm full-width"
                        style={{ whiteSpace: "normal" }}
                        disabled={!!fittingImage}
                        onClick={() => void fitImageLayer(object.id)}
                      >
                        {fittingImage === object.id && <LoaderCircle className="spin" size={15} />}
                        비율 유지하여 면 안에 맞춤
                      </button>
                      <small>이미지 전체를 중앙에 놓고 회전을 0°로 맞춥니다. 비율에 따라 여백이 남습니다.</small>
                    </div>
                  )}
                  {object.type === "barcode" && (
                    <p className="panel-hint">
                      EAN-13 {object.barcode_value}
                      <br />
                      모듈 {object.module_mm} mm · 비율 고정
                      {object.barcode_usage === "sample" && <><br />SAMPLE / 검토용 · 실제 상품 번호가 아닙니다.</>}
                    </p>
                  )}
                  {object.type !== "image" && object.type !== "barcode" && (
                    <label className="field property-field">
                      색상
                      <div className="color-field">
                        <input
                          type="color"
                          aria-label="색상 선택"
                          value={object.color || "#243829"}
                          onChange={(e) =>
                            change(object.id, { color: e.target.value })
                          }
                        />
                        <span>{object.color || "#243829"}</span>
                      </div>
                    </label>
                  )}
                  <h3 className="property-subtitle">
                    위치와 크기 <span>mm</span>
                  </h3>
                  <div className="property-row">
                    {(
                      [
                        ["x_mm", "X"],
                        ["y_mm", "Y"],
                      ] as const
                    ).map(([key, label]) => (
                      <label key={key} className="field property-field">
                        {label}
                        <input
                          type="number"
                          step="0.1"
                          value={object[key]}
                          onChange={(e) => {
                            if (
                              e.target.value !== "" &&
                              Number.isFinite(Number(e.target.value))
                            )
                              change(object.id, {
                                [key]: Number(e.target.value),
                              });
                          }}
                        />
                      </label>
                    ))}
                  </div>
                  <div className="property-row">
                    {(
                      [
                        ["width_mm", "폭"],
                        ["height_mm", "높이"],
                      ] as const
                    ).map(([key, label]) => (
                      <label key={key} className="field property-field">
                        {label}
                        <input
                          type="number"
                          step="0.1"
                          min={1}
                          disabled={object.type === "barcode"}
                          value={object[key]}
                          onChange={(e) => {
                            if (Number(e.target.value) >= 1)
                              change(object.id, {
                                [key]: Number(e.target.value),
                              });
                          }}
                        />
                      </label>
                    ))}
                  </div>
                  <div className="property-row">
                    <label className="field property-field">
                      회전 (°)
                      <input
                        type="number"
                        step="1"
                        value={object.rotation_deg}
                        onChange={(e) => {
                          if (e.target.value !== "")
                            change(object.id, {
                              rotation_deg: Number(e.target.value),
                            });
                        }}
                      />
                    </label>
                    <div className="field property-field">
                      레이어 순서
                      <div className="order-buttons">
                        <button
                          className="icon-button"
                          aria-label="레이어 앞으로"
                          onClick={() => commit(moveLayer(scene, object.id, 1))}
                        >
                          <ArrowUp size={17} />
                        </button>
                        <button
                          className="icon-button"
                          aria-label="레이어 뒤로"
                          onClick={() =>
                            commit(moveLayer(scene, object.id, -1))
                          }
                        >
                          <ArrowDown size={17} />
                        </button>
                      </div>
                    </div>
                  </div>
                  <button
                    type="button"
                    className="button button-light button-sm full-width"
                    onClick={() => {
                      if (current.current) commit(sendLayerToBack(current.current, object.id));
                    }}
                  >
                    <ArrowDown size={15} /> 맨 뒤로 보내기
                  </button>
                  <label className="guide-toggle print-toggle">
                    <input
                      type="checkbox"
                      checked={object.print_enabled !== false}
                      onChange={(e) =>
                        change(object.id, { print_enabled: e.target.checked })
                      }
                    />{" "}
                    PDF 출력에 포함
                  </label>
                </>
              ) : (
                <>
                  <p className="panel-description">
                    캔버스의 요소를 선택하면
                    <br />
                    문구와 배치를 바꿀 수 있어요.
                  </p>
                  <label className="field property-field">
                    {face.name} 배경색
                    <div className="color-field">
                      <input
                        type="color"
                        aria-label="면 배경색"
                        value={face.background}
                        onChange={(e) =>
                          commit({
                            ...scene,
                            faces: scene.faces.map((f) =>
                              f.id === face.id
                                ? { ...f, background: e.target.value }
                                : f,
                            ),
                          })
                        }
                      />
                      <span>{face.background}</span>
                    </div>
                  </label>
                  <div className="color-swatches">
                    {[
                      "#f5f0e5",
                      "#e4ebdd",
                      "#263f2c",
                      "#e79a67",
                      "#fffdf9",
                      "#e9dfe9",
                    ].map((color) => (
                      <button
                        key={color}
                        aria-label={`배경색 ${color}`}
                        style={{ background: color }}
                        onClick={() =>
                          commit({
                            ...scene,
                            faces: scene.faces.map((f) =>
                              f.id === face.id
                                ? { ...f, background: color }
                                : f,
                            ),
                          })
                        }
                      />
                    ))}
                  </div>
                  <div className="demo-background-options">
                    <span>예시 배경 이미지</span>
                    <div>
                      {(
                        [
                          ["forest", "그린"],
                          ["citrus", "시트러스"],
                          ["berry", "베리"],
                        ] as const
                      ).map(([palette, label]) => (
                        <button
                          key={palette}
                          disabled={uploading}
                          className={`demo-palette demo-palette-${palette}`}
                          onClick={() => void demoBackground(palette)}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                    <p>자체 제작 예시 · 실제 AI 생성 아님</p>
                  </div>
                  <div className="editor-spec">
                    <span>
                      포장 형태
                      <strong>
                        {{
                          "three-side-seal": "3면 실링",
                          "stand-up-pouch": "스탠드 파우치",
                          "folding-box": "접이식 상자",
                        }[project.template_id] || project.template_id}
                      </strong>
                    </span>
                    <span>
                      완성 폭<strong>{project.width_mm} mm</strong>
                    </span>
                    <span>
                      완성 높이<strong>{project.height_mm} mm</strong>
                    </span>
                    <span>
                      인쇄면<strong>{scene.faces.length}개 면</strong>
                    </span>
                  </div>
                </>
              )}
            </div>
            <div className="panel-section check-section">
              <h2>
                <ShieldCheck size={16} /> 확인할 사항
              </h2>
              {warnings.length ? (
                <button
                  className="preflight-warning"
                  onClick={() => setSelected(warnings[0].id)}
                >
                  <Info size={17} />
                  <span>
                    안전영역 밖의 요소 {warnings.length}개
                    <small>중요 문구는 초록 점선 안쪽에 놓아 주세요.</small>
                  </span>
                </button>
              ) : (
                <div className="preflight-ok">
                  <Check size={16} />
                  <span>요소가 기본 안전영역 안에 있어요.</span>
                </div>
              )}
              <p className="panel-hint">
                회전한 요소와 실제 가공 조건은 PDF와 제조사 도면으로 확인해
                주세요.
              </p>
            </div>
          </fieldset>
        </aside>
      </div>
      {panel && (
        <Dialog
          title={
            {
              ai: "AI 디자인 스튜디오",
              structure: "바코드와 가공 요소",
              bindings: "상품 연결과 복제",
              exports: "제조 조건과 출력 검수",
              "3d": "3D 조립 미리보기",
              history: "면별 텍스트 변경 기록",
            }[panel]
          }
          onClose={() => setPanel(null)}
        >
          {panel === "ai" && (
            <AIStudio
              projectId={id}
              faceId={face.id}
              referenceAssets={face.objects
                .filter((o) => o.type === "image" && o.asset_id)
                .map((o) => ({ id: o.asset_id! }))}
              saveCurrent={saveCurrent}
              onSelect={useGeneratedAsset}
              readOnly={readOnly}
            />
          )}
          {panel === "structure" && (
            <StructureTools
              scene={scene}
              faceId={face.id}
              onCommit={commit}
              readOnly={readOnly}
              geometry={project.geometry as { faces: FaceStructure[] } | undefined}
              onGeometry={(geometry) => setProject((current) => current ? { ...current, geometry } : current)}
              onFaceSelect={(faceId) => { setFaceId(faceId); setSelected(null); setPanel(null); }}
            />
          )}
          {panel === "bindings" && (
            <BindingTools
              project={project}
              saveCurrent={saveCurrent}
              onServerProject={onServerProject}
              readOnly={readOnly}
            />
          )}
          {panel === "history" && <TextHistory projectId={id} saveCurrent={saveCurrent} readOnly={readOnly} />}
          {panel === "exports" && (
            <ExportTools
              project={project}
              scene={scene}
              saveCurrent={saveCurrent}
              onServerProject={onServerProject}
              onCommit={commit}
              onFaceSelect={(id) => {
                setFaceId(id);
                setSelected(null);
                setPanel(null);
              }}
              readOnly={readOnly}
            />
          )}
          {panel === "3d" && geometry && (
            <div className="editor-preview3d">
              <div className="alert alert-info">
                실제 소재의 변형과 광택을 보증하지 않는 구조 미리보기입니다.
                면을 클릭하면 해당 2D 편집면을 엽니다.
              </div>
              <PackagingPreview
                scene={
                  {
                    ...scene,
                    active_face_id: face.id,
                  } as unknown as PackagingPreviewProps["scene"]
                }
                geometry={geometry}
                onFaceSelect={(id) => {
                  setFaceId(id);
                  setSelected(null);
                  setPanel(null);
                }}
                assetUrl={(id) => `/api/v1/assets/${id}/content`}
                verificationMode
              />
            </div>
          )}
        </Dialog>
      )}
      {exportMessage && (
        <div className="export-toast" role="status">
          <span className="export-toast-icon">
            {exporting ? (
              <LoaderCircle className="spin" size={22} />
            ) : (
              <FileText size={22} />
            )}
          </span>
          <div>
            <strong>
              {exportId
                ? "검토 파일 준비 완료"
                : exporting
                  ? "파일을 만들고 있어요"
                  : "출력 안내"}
            </strong>
            <p>{exportMessage}</p>
            {exportId && (
              <a
                className="text-link"
                href={`/api/v1/exports/${exportId}/download`}
                target="_blank"
                rel="noreferrer"
              >
                PDF 다운로드 <Download size={14} />
              </a>
            )}
          </div>
          {!exporting && (
            <button
              className="icon-button"
              aria-label="출력 안내 닫기"
              onClick={() => setExportMessage("")}
            >
              <X size={17} />
            </button>
          )}
        </div>
      )}
    </main>
  );
}
