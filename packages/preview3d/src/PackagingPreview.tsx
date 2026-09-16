"use client";

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import type { Scene, Face } from "../../contracts/scene.generated";
import { ean13Geometry } from "./barcode";

type StructuralFace = {
  id: string;
  name: string;
  width_mm: number;
  height_mm: number;
  assembly: {
    position_mm: number[];
    rotation_deg: number[];
    uv_rotation_deg: number;
    mirror_u: boolean;
    mirror_v: boolean;
  };
};
export type PreviewGeometry = {
  template_id?: string;
  faces: StructuralFace[];
  assumptions?: string[];
};
export type PackagingPreviewProps = {
  scene: Scene;
  geometry: PreviewGeometry;
  onFaceSelect: (id: string) => void;
  assetUrl?: (id: string) => string;
  verificationMode?: boolean;
};

async function faceTexture(
  face: Face,
  scene: Scene,
  assetUrl: PackagingPreviewProps["assetUrl"],
  verification: boolean,
) {
  const scale = Math.min(5, 1600 / Math.max(face.width_mm, face.height_mm));
  const canvas = document.createElement("canvas");
  canvas.width = Math.ceil(face.width_mm * scale);
  canvas.height = Math.ceil(face.height_mm * scale);
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("2D 캔버스를 사용할 수 없습니다.");
  ctx.scale(scale, scale);
  ctx.fillStyle = face.background ?? "#ffffff";
  ctx.fillRect(0, 0, face.width_mm, face.height_mm);
  const pt = 25.4 / 72;
  for (const obj of [...(face.objects ?? [])].sort(
    (a, b) => (a.z_index ?? 0) - (b.z_index ?? 0),
  )) {
    if (obj.visible === false || obj.print_enabled === false) continue;
    ctx.save();
    ctx.translate(obj.x_mm, obj.y_mm);
    ctx.rotate(((obj.rotation_deg ?? 0) * Math.PI) / 180);
    ctx.globalAlpha = obj.opacity ?? 1;
    if (obj.type === "text") {
      const size = (obj.font_size_pt ?? 18) * pt,
        spacing = (obj.letter_spacing ?? 0) * pt;
      ctx.font = `${size}px NotoSansKR`;
      ctx.fillStyle = obj.color ?? "#172c28";
      ctx.textBaseline = "alphabetic";
      const width = (text: string) =>
        ctx.measureText(text).width +
        Math.max(0, [...text].length - 1) * spacing;
      const lines: string[] = [];
      for (const paragraph of (obj.text ?? "")
        .replaceAll("\r\n", "\n")
        .replaceAll("\r", "\n")
        .replaceAll("\t", "    ")
        .split("\n")) {
        let line = "";
        for (const char of paragraph) {
          if (line && width(line + char) > obj.width_mm) {
            lines.push(line);
            line = char;
          } else line += char;
        }
        lines.push(line);
      }
      lines.forEach((line, index) => {
        let x =
          obj.align === "center"
            ? (obj.width_mm - width(line)) / 2
            : obj.align === "right"
              ? obj.width_mm - width(line)
              : 0;
        const y = 0.88 * size + index * size * (obj.line_height ?? 1.2);
        for (const char of line) {
          ctx.fillText(char, x, y);
          x += ctx.measureText(char).width + spacing;
        }
      });
    } else if (obj.type === "image") {
      if (!obj.asset_id || !assetUrl)
        throw new Error("이미지 자산 연결을 확인해 주세요.");
      const image = await new Promise<HTMLImageElement>((resolve, reject) => {
        const item = new Image();
        item.crossOrigin = "anonymous";
        item.onload = () => resolve(item);
        item.onerror = () =>
          reject(new Error("이미지 자산을 불러오지 못했습니다."));
        item.src = assetUrl(obj.asset_id!);
      });
      ctx.drawImage(image, 0, 0, obj.width_mm, obj.height_mm);
    } else if (obj.type === "barcode") {
      const barcode = ean13Geometry(
        obj.barcode_value ?? "",
        obj.module_mm ?? 0.33,
        obj.bar_height_mm ?? 22.85,
      );
      ctx.globalAlpha = 1;
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, barcode.width_mm, barcode.height_mm);
      ctx.fillStyle = "#000000";
      for (const bar of barcode.bars)
        ctx.fillRect(bar.x_mm, 0, bar.width_mm, barcode.bar_height_mm);
      ctx.font = `${(9 * pt * barcode.module_mm) / 0.33}px NotoSansKR`;
      ctx.textAlign = "center";
      ctx.fillText(
        barcode.value,
        barcode.width_mm / 2,
        barcode.height_mm - 1.2,
      );
    } else {
      ctx.fillStyle = obj.fill ?? obj.color ?? "#172c28";
      ctx.strokeStyle = obj.stroke ?? obj.color ?? "#172c28";
      ctx.lineWidth = obj.stroke_width_mm ?? 0;
      ctx.beginPath();
      if (obj.shape === "ellipse" || obj.shape === "circle")
        ctx.ellipse(
          obj.width_mm / 2,
          obj.height_mm / 2,
          obj.width_mm / 2,
          obj.height_mm / 2,
          0,
          0,
          Math.PI * 2,
        );
      else ctx.rect(0, 0, obj.width_mm, obj.height_mm);
      ctx.fill();
      if (obj.stroke && (obj.stroke_width_mm ?? 0) > 0) ctx.stroke();
    }
    ctx.restore();
  }
  for (const hole of scene.holes ?? []) {
    const direct = hole.face_id === face.id,
      mirror =
        (hole.face_id === "front" && face.id === "back") ||
        (hole.face_id === "back" && face.id === "front");
    if (!direct && !mirror) continue;
    ctx.save();
    ctx.globalCompositeOperation = "destination-out";
    ctx.beginPath();
    ctx.arc(
      mirror ? face.width_mm - hole.center_x_mm : hole.center_x_mm,
      hole.center_y_mm,
      hole.diameter_mm / 2,
      0,
      Math.PI * 2,
    );
    ctx.fill();
    ctx.restore();
  }
  if (verification) {
    ctx.fillStyle = "rgba(255,255,255,.93)";
    ctx.fillRect(2, 2, Math.min(face.width_mm - 4, 48), 11);
    ctx.fillStyle = "#642770";
    ctx.font = "5px NotoSansKR";
    ctx.fillText(`${face.name} ↑`, 4, 9);
  }
  return canvas;
}

export function PackagingPreview({
  scene,
  geometry,
  onFaceSelect,
  assetUrl,
  verificationMode = false,
}: PackagingPreviewProps) {
  const host = useRef<HTMLDivElement>(null),
    select = useRef(onFaceSelect),
    url = useRef(assetUrl);
  select.current = onFaceSelect;
  url.current = assetUrl;
  const [fallback, setFallback] = useState(false),
    [error, setError] = useState(""),
    [pictures, setPictures] = useState<Record<string, string>>({}),
    [ready, setReady] = useState(false);
  useEffect(() => {
    let disposed = false,
      frame = 0,
      cleanup = () => {};
    setReady(false);
    setError("");
    setFallback(false);
    setPictures({});
    const start = async () => {
      const loadedFonts = await document.fonts.load("16px NotoSansKR");
      if (!loadedFonts.length)
        throw new Error("검증된 NotoSansKR 글꼴을 불러오지 못했습니다.");
      const textures = await Promise.all(
        scene.faces.map(async (face) => ({
          face,
          canvas: await faceTexture(face, scene, url.current, verificationMode),
        })),
      );
      if (disposed) return;
      setPictures(
        Object.fromEntries(
          textures.map(({ face, canvas }) => [
            face.id,
            canvas.toDataURL("image/png"),
          ]),
        ),
      );
      if (!host.current) return;
      if (
        scene.faces.some(
          (face) => !geometry.faces.some((item) => item.id === face.id),
        )
      )
        throw new Error("2D와 3D 면 매핑이 일치하지 않습니다.");
      let renderer: THREE.WebGLRenderer;
      try {
        renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
      } catch {
        setFallback(true);
        setReady(true);
        return;
      }
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      const mount = host.current;
      mount.appendChild(renderer.domElement);
      renderer.domElement.setAttribute(
        "aria-label",
        "포장 전체 면 3D 미리보기. 드래그하여 회전하고 면을 선택할 수 있습니다.",
      );
      cleanup = () => {
        renderer.dispose();
        renderer.domElement.remove();
      };
      const world = new THREE.Scene();
      world.background = new THREE.Color("#e9e4da");
      const maximum = Math.max(
        ...geometry.faces.flatMap((face) => [face.width_mm, face.height_mm]),
      );
      const factor = 2 / maximum;
      const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 100);
      camera.position.set(2.7, 1.7, 4);
      const controls = new OrbitControls(camera, renderer.domElement);
      controls.enableDamping = true;
      controls.minDistance = 1.5;
      controls.maxDistance = 8;
      const meshes: THREE.Mesh[] = [],
        maps: THREE.CanvasTexture[] = [];
      for (const { face, canvas } of textures) {
        const structural = geometry.faces.find((item) => item.id === face.id);
        if (!structural)
          throw new Error("2D와 3D 면 매핑이 일치하지 않습니다.");
        const texture = new THREE.CanvasTexture(canvas);
        texture.colorSpace = THREE.SRGBColorSpace;
        texture.anisotropy = Math.min(
          8,
          renderer.capabilities.getMaxAnisotropy(),
        );
        maps.push(texture);
        // No UV mirroring: outside view sees the same left/right/up orientation as the editor.
        texture.center.set(0.5, 0.5);
        texture.rotation = THREE.MathUtils.degToRad(
          structural.assembly.uv_rotation_deg,
        );
        texture.repeat.set(
          structural.assembly.mirror_u ? -1 : 1,
          structural.assembly.mirror_v ? -1 : 1,
        );
        const mesh = new THREE.Mesh(
          new THREE.PlaneGeometry(
            face.width_mm * factor,
            face.height_mm * factor,
          ),
          new THREE.MeshBasicMaterial({
            map: texture,
            side: THREE.FrontSide,
            transparent: true,
            alphaTest: 0.05,
          }),
        );
        mesh.position.fromArray(
          structural.assembly.position_mm.map((n) => n * factor),
        );
        mesh.rotation.set(
          ...(structural.assembly.rotation_deg.map(
            THREE.MathUtils.degToRad,
          ) as [number, number, number]),
        );
        mesh.userData.faceId = face.id;
        world.add(mesh);
        meshes.push(mesh);
      }
      const bounds = new THREE.Box3();
      meshes.forEach((mesh) => {
        mesh.updateMatrixWorld();
        bounds.expandByObject(mesh);
      });
      const center = bounds.getCenter(new THREE.Vector3());
      controls.target.copy(center);
      const direction = new THREE.Vector3(2.7, 1.7, 4).normalize();
      const right = new THREE.Vector3()
        .crossVectors(camera.up, direction)
        .normalize();
      const up = new THREE.Vector3().crossVectors(direction, right).normalize();
      const corners = [bounds.min.x, bounds.max.x].flatMap((x) =>
        [bounds.min.y, bounds.max.y].flatMap((y) =>
          [bounds.min.z, bounds.max.z].map((z) =>
            new THREE.Vector3(x, y, z).sub(center),
          ),
        ),
      );
      const resize = () => {
        const w = Math.max(1, mount.clientWidth),
          h = mount.clientHeight || 420;
        renderer.setSize(w, h);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        const vertical = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)),
          horizontal = vertical * camera.aspect;
        const distance = Math.max(
          ...corners.flatMap((point) => [
            point.dot(direction) + Math.abs(point.dot(up)) / (vertical * 0.72),
            point.dot(direction) +
              Math.abs(point.dot(right)) / (horizontal * 0.82),
          ]),
        );
        camera.position.copy(center).addScaledVector(direction, distance);
        controls.minDistance = distance * 0.4;
        controls.maxDistance = distance * 3;
        controls.update();
      };
      resize();
      const observer = new ResizeObserver(resize);
      observer.observe(mount);
      let down = { x: 0, y: 0 };
      const pointerDown = (event: PointerEvent) => {
        down = { x: event.clientX, y: event.clientY };
      };
      const pointerUp = (event: PointerEvent) => {
        if (Math.hypot(event.clientX - down.x, event.clientY - down.y) > 5)
          return;
        const box = renderer.domElement.getBoundingClientRect();
        const ray = new THREE.Raycaster();
        ray.setFromCamera(
          new THREE.Vector2(
            ((event.clientX - box.left) / box.width) * 2 - 1,
            (-(event.clientY - box.top) / box.height) * 2 + 1,
          ),
          camera,
        );
        const hit = ray.intersectObjects(meshes)[0];
        if (hit) select.current(hit.object.userData.faceId);
      };
      renderer.domElement.addEventListener("pointerdown", pointerDown);
      renderer.domElement.addEventListener("pointerup", pointerUp);
      const draw = () => {
        if (disposed) return;
        controls.update();
        renderer.render(world, camera);
        frame = requestAnimationFrame(draw);
      };
      draw();
      setReady(true);
      cleanup = () => {
        cancelAnimationFrame(frame);
        observer.disconnect();
        controls.dispose();
        for (const mesh of meshes) {
          mesh.geometry.dispose();
          (mesh.material as THREE.Material).dispose();
        }
        maps.forEach((map) => map.dispose());
        renderer.dispose();
        renderer.domElement.remove();
      };
    };
    start().catch((reason) => {
      cleanup();
      if (!disposed) {
        setError(
          reason instanceof Error
            ? reason.message
            : "미리보기를 불러오지 못했습니다.",
        );
        setFallback(true);
        setReady(true);
      }
    });
    return () => {
      disposed = true;
      cleanup();
    };
  }, [scene, geometry, verificationMode]);
  return (
    <section aria-label="전체 면 미리보기" style={{ minWidth: 0 }}>
      {!ready && <p role="status">현재 편집 면을 3D에 연결하고 있습니다.</p>}
      {error && (
        <p role="alert">
          {error} 아래 면 목록에서 2D 편집을 계속할 수 있습니다.
        </p>
      )}
      <div
        ref={host}
        style={{
          height: 420,
          width: "100%",
          display: fallback ? "none" : "block",
          borderRadius: 16,
          overflow: "hidden",
        }}
      />
      {fallback && (
        <p>WebGL 미리보기를 사용할 수 없어 전체 면 2D 보기로 전환했습니다.</p>
      )}
      <div
        style={{ display: "flex", flexWrap: "wrap", gap: 10, marginTop: 12 }}
      >
        {scene.faces.map((face) => (
          <button
            key={face.id}
            type="button"
            onClick={() => onFaceSelect(face.id)}
            aria-label={`${face.name} 2D 편집으로 이동`}
            style={{
              border:
                scene.active_face_id === face.id
                  ? "2px solid #285845"
                  : "1px solid #c7c2b9",
              borderRadius: 10,
              padding: 8,
              background: "#fff",
              color: "#252e29",
            }}
          >
            {fallback && pictures[face.id] && (
              <img
                src={pictures[face.id]}
                alt={`${face.name} 같은 장면 미리보기`}
                style={{
                  width: 100,
                  height: 100,
                  objectFit: "contain",
                  display: "block",
                }}
              />
            )}
            {face.name}{" "}
            {scene.reviewed_face_ids?.includes(face.id)
              ? "· 확인됨"
              : "· 확인 필요"}
          </button>
        ))}
      </div>
      <p style={{ fontSize: 12, color: "#666", lineHeight: 1.7 }}>
        현재 디자인을 각 면에 적용한 조립 미리보기입니다. 드래그하여 돌려 보고,
        면을 누르면 해당 면을 편집할 수 있습니다.{" "}
        {geometry.template_id === "folding-box"
          ? "접는 방향과 면의 배치를 확인하기 위한 모습으로, 접착과 잠금 부분의 실제 결합을 재현하지 않습니다."
          : "내용물을 채웠을 때의 부풀음, 소재의 두께와 유연성, 실링 부분의 주름은 단순화되어 있습니다."}{" "}
        최종 가공과 충전 적합성은 제조사와 확인해 주세요.
      </p>
    </section>
  );
}
export default PackagingPreview;
