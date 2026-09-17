"use client";
import { useEffect, useRef, useState } from "react";
import {
  Stage,
  Layer,
  Rect,
  Text,
  Image as KImage,
  Transformer,
  Line,
  Shape as KShape,
  Group,
  Circle,
} from "react-konva";
import type Konva from "konva";
import type { Face, SceneObject } from "./model";
import { roundMM } from "./model";
import { drawEditableText, fontFamily, fontData, loadObjectFonts } from "./fonts";
import {
  cropPixels,
  objectBounds,
  selectionInRect,
  snapTranslation,
} from "./layout";
type SelectEvent = Konva.KonvaEventObject<MouseEvent | TouchEvent>;
import { ean13Geometry } from "@preview3d/barcode";
import { contourPoints, linePoints, type FaceStructure } from "./structure";
type Region = {
  x_mm: number;
  y_mm: number;
  width_mm: number;
  height_mm: number;
};
type GeometryFace = Pick<FaceStructure, "regions">;
function BarcodeObject({
  object,
  scale,
  selected,
  onSelect,
  onChange,
  readOnly,
}: {
  object: SceneObject;
  scale: number;
  selected: boolean;
  onSelect: (event: SelectEvent) => void;
  onChange: (patch: Partial<SceneObject>) => void;
  readOnly: boolean;
}) {
  let barcode;
  try {
    barcode = ean13Geometry(
      object.barcode_value || "",
      object.module_mm ?? 0.33,
      object.bar_height_mm ?? 22.85,
    );
  } catch {
    return (
      <Rect
        x={object.x_mm * scale}
        y={object.y_mm * scale}
        width={object.width_mm * scale}
        height={object.height_mm * scale}
        fill="#ffe7dc"
        stroke="#ca593a"
        onClick={onSelect}
      />
    );
  }
  return (
    <Group
      id={object.id}
      x={object.x_mm * scale}
      y={object.y_mm * scale}
      rotation={object.rotation_deg}
      draggable={!readOnly && !object.locked}
      onClick={onSelect}
      onTap={onSelect}
    >
      <Rect
        width={barcode.width_mm * scale}
        height={object.height_mm * scale}
        fill="#ffffff"
        stroke={selected ? "#e36d40" : undefined}
        strokeWidth={1}
      />
      {barcode.bars.map((bar, index) => (
        <Rect
          key={index}
          x={bar.x_mm * scale}
          y={0}
          width={bar.width_mm * scale}
          height={barcode.bar_height_mm * scale}
          fill="#000000"
          listening={false}
        />
      ))}
      <Text
        text={barcode.value}
        x={0}
        y={(barcode.bar_height_mm + 1) * scale}
        width={barcode.width_mm * scale}
        fontFamily="NotoSansKREditor"
        fontSize={3.175 * (barcode.module_mm / 0.33) * scale}
        align="center"
        fill="#000000"
        listening={false}
      />
      {object.barcode_usage === "sample" && (
        <Text
          text="SAMPLE / 검토용"
          x={0}
          y={(barcode.bar_height_mm + 5.5) * scale}
          width={barcode.width_mm * scale}
          height={3.5 * scale}
          fontFamily="NotoSansKREditor"
          fontSize={((7 * 25.4) / 72) * scale}
          lineHeight={1}
          align="center"
          fill="#000000"
          listening={false}
        />
      )}
    </Group>
  );
}
function AssetImage({
  object,
  scale,
  onSelect,
  onChange,
  readOnly,
}: {
  object: SceneObject;
  scale: number;
  onSelect: (event: SelectEvent) => void;
  onChange: (patch: Partial<SceneObject>) => void;
  readOnly: boolean;
}) {
  const [image, setImage] = useState<HTMLImageElement>();
  useEffect(() => {
    let active = true;
    setImage(undefined);
    const img = new window.Image();
    img.crossOrigin = "anonymous";
    img.src = `/api/v1/assets/${object.asset_id}/content`;
    img.onload = () => {
      if (active) setImage(img);
    };
    return () => {
      active = false;
    };
  }, [object.asset_id]);
  return (
    <KImage
      id={object.id}
      image={image}
      crop={
        image
          ? cropPixels(object.crop, image.naturalWidth, image.naturalHeight)
          : undefined
      }
      opacity={object.opacity ?? 1}
      x={object.x_mm * scale}
      y={object.y_mm * scale}
      width={object.width_mm * scale}
      height={object.height_mm * scale}
      rotation={object.rotation_deg}
      onClick={onSelect}
      onTap={onSelect}
      draggable={!readOnly && !object.locked}
      onTransformEnd={(e) => {
        const n = e.target;
        onChange({
          x_mm: roundMM(n.x() / scale),
          y_mm: roundMM(n.y() / scale),
          width_mm: roundMM(Math.max(1, (n.width() * n.scaleX()) / scale)),
          height_mm: roundMM(Math.max(1, (n.height() * n.scaleY()) / scale)),
          rotation_deg: roundMM(n.rotation()),
        });
        n.scale({ x: 1, y: 1 });
      }}
    />
  );
}
export default function Canvas({
  face,
  selected,
  onSelect,
  onChange,
  zoom,
  guides,
  onEditState,
  holes = [],
  geometryFace,
  readOnly = false,
  selectedIds = selected ? [selected] : [],
  onSelectMany,
  onBatchChange,
  snapping = true,
  rulers = true,
}: {
  face: Face;
  selected: string | null;
  onSelect: (id: string | null, additive?: boolean) => void;
  selectedIds?: string[];
  onSelectMany?: (ids: string[]) => void;
  onBatchChange?: (
    changes: Array<{ id: string; patch: Partial<SceneObject> }>,
  ) => void;
  snapping?: boolean;
  rulers?: boolean;
  onChange: (id: string, patch: Partial<SceneObject>) => void;
  zoom: number;
  guides: boolean;
  onEditState: (editing: boolean) => void;
  holes?: Array<{
    id: string;
    face_id: string;
    center_x_mm: number;
    center_y_mm: number;
    diameter_mm: number;
  }>;
  geometryFace?: GeometryFace;
  readOnly?: boolean;
}) {
  const host = useRef<HTMLDivElement>(null);
  const stage = useRef<Konva.Stage>(null);
  const transformer = useRef<Konva.Transformer>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const [available, setAvailable] = useState({ width: 650, height: 620 });
  const [editing, setEditing] = useState<string | null>(null);
  const [textValue, setTextValue] = useState("");
  const composing = useRef(false);
  const [fontReady, setFontReady] = useState(false);
  const [fontError, setFontError] = useState("");
  const [fontRetry, setFontRetry] = useState(0);
  const fontSignature = [...new Set(face.objects.map(o => o.font_asset_id).filter(Boolean))].sort().join(":");
  useEffect(() => {
    let active = true;
    setFontReady(false); setFontError("");
    loadObjectFonts(face.objects).then(() => { if (active) setFontReady(true); })
      .catch((error: unknown) => { if (active) setFontError(error instanceof Error ? error.message : "글꼴을 불러오지 못했습니다."); });
    return () => { active = false; };
  }, [fontSignature, fontRetry]);
  useEffect(() => {
    if (!host.current) return;
    const observer = new ResizeObserver((entries) => {
      const r = entries[0].contentRect;
      setAvailable({ width: r.width, height: r.height });
    });
    observer.observe(host.current);
    return () => observer.disconnect();
  }, []);
  const scale =
    Math.max(
      0.01,
      Math.min(
        (available.width - (available.width < 600 ? 76 : 110)) / face.width_mm,
        (available.height - 100) / face.height_mm,
        2.5,
      ),
    ) * zoom;
  const width = face.width_mm * scale;
  const height = face.height_mm * scale;
  const current = face.objects.find((o) => o.id === editing);
  useEffect(() => {
    const node = selected ? stage.current?.findOne(`#${selected}`) : null;
    transformer.current?.nodes(
      node &&
        selectedIds.length === 1 &&
        !editing &&
        node.draggable() &&
        !readOnly &&
        face.objects.find((o) => o.id === selected)?.type !== "barcode"
        ? [node]
        : [],
    );
    transformer.current?.getLayer()?.batchDraw();
  }, [selected, selectedIds, editing, face.objects, fontReady, readOnly]);
  useEffect(() => {
    setEditing(null);
    onEditState(false);
  }, [face.id, onEditState]);
  function startEdit(object: SceneObject) {
    if (object.locked || readOnly || (object.font_asset_id && !fontData(object.font_asset_id))) return;
    setEditing(object.id);
    setTextValue(object.text || "");
    onEditState(true);
    setTimeout(() => {
      textarea.current?.focus();
    }, 0);
  }
  function endEdit(cancel = false) {
    if (
      !editing ||
      composing.current ||
      (!cancel && (readOnly || current?.locked))
    )
      return;
    if (!cancel) onChange(editing, { text: textValue });
    setEditing(null);
    onEditState(false);
  }
  const drag = useRef<{
    ids: string[];
    origin: { x: number; y: number };
    dx: number;
    dy: number;
  } | null>(null);
  const marqueeStart = useRef<{ x: number; y: number } | null>(null);
  const [marquee, setMarquee] = useState<{
    x_mm: number;
    y_mm: number;
    width_mm: number;
    height_mm: number;
  } | null>(null);
  const [snapGuides, setSnapGuides] = useState<
    Array<{ axis: "x" | "y"; position: number }>
  >([]);
  function choose(id: string, event: SelectEvent) {
    const e = event.evt as MouseEvent;
    onSelect(id, !!(e.shiftKey || e.ctrlKey || e.metaKey));
  }
  function dragStart(e: Konva.KonvaEventObject<DragEvent>) {
    const object = face.objects.find((o) => o.id === e.target.id());
    if (!object || readOnly || object.locked) return;
    const ids = (
      selectedIds.includes(object.id) ? selectedIds : [object.id]
    ).filter((id) => !face.objects.find((o) => o.id === id)?.locked);
    if (!selectedIds.includes(object.id)) onSelect(object.id);
    drag.current = {
      ids,
      origin: { x: object.x_mm, y: object.y_mm },
      dx: 0,
      dy: 0,
    };
  }
  function dragMove(e: Konva.KonvaEventObject<DragEvent>) {
    const d = drag.current;
    if (!d) return;
    const dx = e.target.x() / scale - d.origin.x,
      dy = e.target.y() / scale - d.origin.y;
    const move = snapping
      ? snapTranslation(
          face,
          d.ids,
          dx,
          dy,
          6 / scale,
          geometryFace?.regions?.safe,
        )
      : { dx, dy, guides: [] };
    d.dx = move.dx;
    d.dy = move.dy;
    setSnapGuides(move.guides);
    for (const id of d.ids) {
      const o = face.objects.find((o) => o.id === id)!;
      stage.current
        ?.findOne(`#${id}`)
        ?.position({ x: (o.x_mm + d.dx) * scale, y: (o.y_mm + d.dy) * scale });
    }
  }
  function dragEnd() {
    const d = drag.current;
    drag.current = null;
    setSnapGuides([]);
    if (!d || readOnly) return;
    const changes = d.ids.map((id) => {
      const o = face.objects.find((o) => o.id === id)!;
      return {
        id,
        patch: { x_mm: roundMM(o.x_mm + d.dx), y_mm: roundMM(o.y_mm + d.dy) },
      };
    });
    if (onBatchChange) onBatchChange(changes);
    else changes.forEach((change) => onChange(change.id, change.patch));
  }
  function beginMarquee(e: Konva.KonvaEventObject<MouseEvent | TouchEvent>) {
    if (e.target !== stage.current && e.target.name() !== "face-background")
      return;
    endEdit();
    const p = stage.current?.getPointerPosition();
    if (!p) return;
    marqueeStart.current = { x: p.x / scale, y: p.y / scale };
    setMarquee(null);
  }
  function moveMarquee() {
    const a = marqueeStart.current,
      p = stage.current?.getPointerPosition();
    if (!a || !p) return;
    const x = p.x / scale,
      y = p.y / scale;
    setMarquee({
      x_mm: Math.min(a.x, x),
      y_mm: Math.min(a.y, y),
      width_mm: Math.abs(x - a.x),
      height_mm: Math.abs(y - a.y),
    });
  }
  function endMarquee() {
    if (!marqueeStart.current) return;
    marqueeStart.current = null;
    if (
      marquee &&
      marquee.width_mm * scale > 3 &&
      marquee.height_mm * scale > 3
    )
      onSelectMany?.(selectionInRect(face, marquee));
    else onSelect(null);
    setMarquee(null);
  }
  const rulerStep = scale < 1 ? 20 : scale < 2 ? 10 : 5;
  const patch = (object: SceneObject) => (changes: Partial<SceneObject>) =>
    onChange(object.id, changes);
  return (
    <div className="canvas-host" ref={host}>
      <div className="canvas-scroll">
        <div
          className="canvas-board"
          style={{ width: width + 60, height: height + 60 }}
        >
          {rulers && (
            <>
              <svg
                className="canvas-ruler horizontal"
                width={width}
                height={22}
                aria-label="가로 눈금자 mm"
              >
                {Array.from(
                  { length: Math.floor(face.width_mm / rulerStep) + 1 },
                  (_, i) => i * rulerStep,
                ).map((mm) => (
                  <g key={mm}>
                    <line x1={mm * scale} x2={mm * scale} y1={14} y2={22} />
                    <text x={mm * scale + 2} y={11}>
                      {mm}
                    </text>
                  </g>
                ))}
              </svg>
              <svg
                className="canvas-ruler vertical"
                width={24}
                height={height}
                aria-label="세로 눈금자 mm"
              >
                {Array.from(
                  { length: Math.floor(face.height_mm / rulerStep) + 1 },
                  (_, i) => i * rulerStep,
                ).map((mm) => (
                  <g key={mm}>
                    <line x1={17} x2={24} y1={mm * scale} y2={mm * scale} />
                    <text x={2} y={mm * scale + 11}>
                      {mm}
                    </text>
                  </g>
                ))}
              </svg>
            </>
          )}
          <span className="canvas-width-label">{face.width_mm} mm</span>
          <span className="canvas-height-label">{face.height_mm} mm</span>
          <div className="canvas-paper" style={{ width, height }}>
            <Stage
              width={width}
              height={height}
              ref={stage}
              onMouseDown={beginMarquee}
              onTouchStart={beginMarquee}
              onMouseMove={moveMarquee}
              onTouchMove={moveMarquee}
              onMouseUp={endMarquee}
              onTouchEnd={endMarquee}
              onDragStart={dragStart}
              onDragMove={dragMove}
              onDragEnd={dragEnd}
            >
              <Layer>
                <Rect
                  x={0}
                  y={0}
                  width={width}
                  height={height}
                  fill={face.background || "#f5f0e5"}
                  name="face-background"
                />
                {[...face.objects]
                  .sort((a, b) => a.z_index - b.z_index)
                  .filter((o) => o.visible !== false)
                  .map((object) =>
                    object.type === "barcode" ? (
                      <BarcodeObject
                        key={object.id}
                        object={object}
                        scale={scale}
                        selected={selected === object.id}
                        onSelect={(e) => choose(object.id, e)}
                        onChange={patch(object)}
                        readOnly={readOnly}
                      />
                    ) : object.type === "image" ? (
                      <AssetImage
                        key={object.id}
                        object={object}
                        scale={scale}
                        onSelect={(e) => choose(object.id, e)}
                        onChange={patch(object)}
                        readOnly={readOnly}
                      />
                    ) : object.type === "shape" ? (
                      <KShape
                        key={object.id}
                        id={object.id}
                        x={object.x_mm * scale}
                        y={object.y_mm * scale}
                        width={object.width_mm * scale}
                        height={object.height_mm * scale}
                        rotation={object.rotation_deg}
                        fill={object.fill || object.color || "#43664c"}
                        opacity={object.opacity ?? 1}
                        stroke={object.stroke}
                        strokeWidth={(object.stroke_width_mm ?? 0) * scale}
                        sceneFunc={(context, node) => {
                          context.beginPath();
                          if (
                            object.shape === "ellipse" ||
                            object.shape === "circle"
                          )
                            context.ellipse(
                              node.width() / 2,
                              node.height() / 2,
                              node.width() / 2,
                              node.height() / 2,
                              0,
                              0,
                              Math.PI * 2,
                            );
                          else context.rect(0, 0, node.width(), node.height());
                          context.closePath();
                          context.fillStrokeShape(node);
                        }}
                        draggable={!readOnly && !object.locked}
                        onClick={(e) => choose(object.id, e)}
                        onTap={(e) => choose(object.id, e)}
                        onTransformEnd={(e) => {
                          const n = e.target;
                          patch(object)({
                            x_mm: roundMM(n.x() / scale),
                            y_mm: roundMM(n.y() / scale),
                            width_mm: roundMM(
                              Math.max(1, (n.width() * n.scaleX()) / scale),
                            ),
                            height_mm: roundMM(
                              Math.max(1, (n.height() * n.scaleY()) / scale),
                            ),
                            rotation_deg: roundMM(n.rotation()),
                          });
                          n.scale({ x: 1, y: 1 });
                        }}
                      />
                    ) : (
                      <Text
                        key={`${object.id}-${fontReady ? "font-ready" : "font-loading"}`}
                        id={object.id}
                        text={object.text || ""}
                        x={object.x_mm * scale}
                        y={object.y_mm * scale}
                        width={object.width_mm * scale}
                        height={object.height_mm * scale}
                        fontSize={
                          (((object.font_size_pt || 16) * 25.4) / 72) * scale
                        }
                        fontFamily={fontFamily(object)}
                        fontStyle={String(object.font_weight ?? 400)}
                        sceneFunc={(context) => {
                          if (object.font_asset_id && !fontData(object.font_asset_id)) return;
                          drawEditableText(context._context, object, scale);
                        }}
                        fill={object.color || "#263b2d"}
                        rotation={object.rotation_deg}
                        align={object.align || "left"}
                        lineHeight={object.line_height ?? 1.2}
                        letterSpacing={
                          (((object.letter_spacing ?? 0) * 25.4) / 72) * scale
                        }
                        opacity={object.opacity ?? 1}
                        wrap="char"
                        visible={editing !== object.id}
                        draggable={!readOnly && !object.locked}
                        onClick={(e) => choose(object.id, e)}
                        onTap={(e) => choose(object.id, e)}
                        onDblClick={() => startEdit(object)}
                        onDblTap={() => startEdit(object)}
                        onTransformEnd={(e) => {
                          const n = e.target;
                          patch(object)({
                            x_mm: roundMM(n.x() / scale),
                            y_mm: roundMM(n.y() / scale),
                            width_mm: roundMM(
                              Math.max(5, (n.width() * n.scaleX()) / scale),
                            ),
                            height_mm: roundMM(
                              Math.max(5, (n.height() * n.scaleY()) / scale),
                            ),
                            rotation_deg: roundMM(n.rotation()),
                          });
                          n.scale({ x: 1, y: 1 });
                        }}
                      />
                    ),
                  )}
                {guides && !geometryFace && (
                  <>
                    <Rect
                      x={10 * scale}
                      y={10 * scale}
                      width={(face.width_mm - 20) * scale}
                      height={(face.height_mm - 20) * scale}
                      stroke="#cc5b62"
                      strokeWidth={0.8}
                      dash={[4, 4]}
                      listening={false}
                    />
                    <Rect
                      x={15 * scale}
                      y={15 * scale}
                      width={(face.width_mm - 30) * scale}
                      height={(face.height_mm - 30) * scale}
                      stroke="#3a816a"
                      strokeWidth={0.8}
                      dash={[3, 5]}
                      listening={false}
                    />
                    <Line
                      points={[0, 10 * scale, width, 10 * scale]}
                      stroke="#cc5b6233"
                      listening={false}
                    />
                  </>
                )}
                {guides && geometryFace?.regions && (
                  <>
                    {geometryFace.regions.no_print?.map((r, i) => (
                      <Rect
                        key={`forbidden-${i}`}
                        x={r.x_mm * scale}
                        y={r.y_mm * scale}
                        width={r.width_mm * scale}
                        height={r.height_mm * scale}
                        fill="#ce76721b"
                        stroke="#ce767233"
                        strokeWidth={0.5}
                        listening={false}
                      />
                    ))}
                    {geometryFace.regions.safe && (
                      <Rect
                        x={geometryFace.regions.safe.x_mm * scale}
                        y={geometryFace.regions.safe.y_mm * scale}
                        width={geometryFace.regions.safe.width_mm * scale}
                        height={geometryFace.regions.safe.height_mm * scale}
                        stroke="#3a816a"
                        dash={[3, 5]}
                        strokeWidth={0.8}
                        listening={false}
                      />
                    )}
                    {geometryFace.regions.fold?.map((line, i) => (
                      <Line
                        key={`fold-${i}`}
                        points={[
                          line.x1_mm * scale,
                          line.y1_mm * scale,
                          line.x2_mm * scale,
                          line.y2_mm * scale,
                        ]}
                        stroke="#7b72aa"
                        dash={[5, 4]}
                        strokeWidth={0.8}
                        listening={false}
                      />
                    ))}
                    {geometryFace.regions.zipper && (
                      <>
                        <Rect
                          {...{
                            x: geometryFace.regions.zipper.band.x_mm * scale,
                            y: geometryFace.regions.zipper.band.y_mm * scale,
                            width:
                              geometryFace.regions.zipper.band.width_mm * scale,
                            height:
                              geometryFace.regions.zipper.band.height_mm *
                              scale,
                          }}
                          fill="#8760a225"
                          stroke="#8760a2"
                          strokeWidth={0.7}
                          listening={false}
                        />
                        <Line
                          points={linePoints(
                            geometryFace.regions.zipper.line,
                            scale,
                          )}
                          stroke="#8760a2"
                          strokeWidth={1.2}
                          listening={false}
                        />
                      </>
                    )}
                    {geometryFace.regions.tear_line && (
                      <Line
                        points={linePoints(
                          geometryFace.regions.tear_line,
                          scale,
                        )}
                        stroke="#c34b78"
                        dash={[5, 3]}
                        strokeWidth={1}
                        listening={false}
                      />
                    )}
                  </>
                )}
                {geometryFace?.regions?.tear_notches?.map((notch, index) => (
                  <Group key={`notch-${index}`} listening={false}>
                    <Line
                      points={contourPoints(notch.points_mm, scale)}
                      closed
                      fill="#000000"
                      globalCompositeOperation="destination-out"
                    />
                    {guides && (
                      <Line
                        points={contourPoints(notch.points_mm, scale)}
                        stroke="#c34b78"
                        strokeWidth={1}
                      />
                    )}
                  </Group>
                ))}
                {holes
                  .filter(
                    (h) =>
                      h.face_id === face.id ||
                      (h.face_id === "front" && face.id === "back") ||
                      (h.face_id === "back" && face.id === "front"),
                  )
                  .map((h) => (
                    <Group key={h.id} listening={false}>
                      <Circle
                        key={h.id}
                        x={
                          (h.face_id === face.id
                            ? h.center_x_mm
                            : face.width_mm - h.center_x_mm) * scale
                        }
                        y={h.center_y_mm * scale}
                        radius={(h.diameter_mm / 2) * scale}
                        fill="#000000"
                        globalCompositeOperation="destination-out"
                      />
                      {guides && (
                        <Circle
                          x={
                            (h.face_id === face.id
                              ? h.center_x_mm
                              : face.width_mm - h.center_x_mm) * scale
                          }
                          y={h.center_y_mm * scale}
                          radius={(h.diameter_mm / 2) * scale}
                          stroke="#d8643d"
                          strokeWidth={1}
                        />
                      )}
                    </Group>
                  ))}
                {selectedIds.length > 1 &&
                  face.objects
                    .filter((o) => selectedIds.includes(o.id))
                    .map((o) => {
                      const b = objectBounds(o);
                      return (
                        <Rect
                          key={`selection-${o.id}`}
                          x={b.left * scale}
                          y={b.top * scale}
                          width={b.width * scale}
                          height={b.height * scale}
                          stroke={o.locked ? "#777" : "#e4673d"}
                          dash={[4, 3]}
                          strokeWidth={1}
                          listening={false}
                        />
                      );
                    })}
                {marquee && (
                  <Rect
                    x={marquee.x_mm * scale}
                    y={marquee.y_mm * scale}
                    width={marquee.width_mm * scale}
                    height={marquee.height_mm * scale}
                    fill="#e4673d20"
                    stroke="#e4673d"
                    strokeWidth={1}
                    listening={false}
                  />
                )}
                {snapGuides.map((g, i) => (
                  <Line
                    key={`snap-${i}`}
                    points={
                      g.axis === "x"
                        ? [g.position * scale, 0, g.position * scale, height]
                        : [0, g.position * scale, width, g.position * scale]
                    }
                    stroke="#9c4db1"
                    dash={[3, 3]}
                    listening={false}
                  />
                ))}
                <Transformer
                  ref={transformer}
                  rotateEnabled
                  anchorSize={7}
                  borderStroke="#ea6339"
                  anchorStroke="#ea6339"
                  anchorFill="#fff"
                  keepRatio={false}
                  flipEnabled={false}
                  boundBoxFunc={(oldBox, newBox) =>
                    newBox.width < 10 || newBox.height < 10 ? oldBox : newBox
                  }
                />
              </Layer>
            </Stage>
            {current && (
              <textarea
                ref={textarea}
                className="canvas-textarea"
                aria-label="캔버스 한글 문구 편집"
                value={textValue}
                readOnly={readOnly || current.locked}
                onChange={(e) => setTextValue(e.target.value)}
                onCompositionStart={() => {
                  composing.current = true;
                }}
                onCompositionEnd={() => {
                  composing.current = false;
                }}
                onBlur={() => endEdit()}
                onKeyDown={(e) => {
                  if (e.nativeEvent.isComposing || composing.current) return;
                  if (e.key === "Escape") {
                    e.preventDefault();
                    endEdit(true);
                  }
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                    e.preventDefault();
                    endEdit();
                  }
                }}
                style={{
                  left: current.x_mm * scale,
                  top: current.y_mm * scale,
                  width: current.width_mm * scale,
                  height: Math.max(current.height_mm * scale, 70),
                  fontSize:
                    (((current.font_size_pt || 16) * 25.4) / 72) * scale,
                  color: current.color,
                  textAlign: current.align,
                  lineHeight: current.line_height ?? 1.2,
                  fontFamily: fontFamily(current),
                  fontWeight: current.font_weight ?? 400,
                  fontKerning: "none",
                  fontVariantLigatures: "none",
                  letterSpacing:
                    (((current.letter_spacing ?? 0) * 25.4) / 72) * scale,
                  transform: `rotate(${current.rotation_deg}deg)`,
                }}
              />
            )}
          </div>
        </div>
      </div>
      {!fontReady && (
        <div className="canvas-font-notice" role={fontError ? "alert" : "status"}>{fontError || "글꼴 원본을 불러오고 있어요…"}{fontError && <button type="button" onClick={() => setFontRetry(value => value + 1)}>글꼴 다시 불러오기</button>}</div>
      )}
    </div>
  );
}
