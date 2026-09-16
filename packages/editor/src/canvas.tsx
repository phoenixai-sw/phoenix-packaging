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
} from "react-konva";
import type Konva from "konva";
import type { Face, SceneObject } from "./model";
import { roundMM } from "./model";
function AssetImage({
  object,
  scale,
  onSelect,
  onChange,
}: {
  object: SceneObject;
  scale: number;
  onSelect: () => void;
  onChange: (patch: Partial<SceneObject>) => void;
}) {
  const [image, setImage] = useState<HTMLImageElement>();
  useEffect(() => {
    let active = true;
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
      opacity={object.opacity ?? 1}
      x={object.x_mm * scale}
      y={object.y_mm * scale}
      width={object.width_mm * scale}
      height={object.height_mm * scale}
      rotation={object.rotation_deg}
      onClick={onSelect}
      onTap={onSelect}
      draggable={!object.locked}
      onDragEnd={(e) =>
        onChange({
          x_mm: roundMM(e.target.x() / scale),
          y_mm: roundMM(e.target.y() / scale),
        })
      }
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
}: {
  face: Face;
  selected: string | null;
  onSelect: (id: string | null) => void;
  onChange: (id: string, patch: Partial<SceneObject>) => void;
  zoom: number;
  guides: boolean;
  onEditState: (editing: boolean) => void;
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
  useEffect(() => {
    document.fonts.load('16px "NotoSansKR"').then(() => setFontReady(true));
  }, []);
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
    Math.min(
      (available.width - 110) / face.width_mm,
      (available.height - 100) / face.height_mm,
      2.5,
    ) * zoom;
  const width = face.width_mm * scale;
  const height = face.height_mm * scale;
  const current = face.objects.find((o) => o.id === editing);
  useEffect(() => {
    const node = selected ? stage.current?.findOne(`#${selected}`) : null;
    transformer.current?.nodes(
      node && !editing && node.draggable() ? [node] : [],
    );
    transformer.current?.getLayer()?.batchDraw();
  }, [selected, editing, face.objects, fontReady]);
  useEffect(() => {
    setEditing(null);
    onEditState(false);
  }, [face.id, onEditState]);
  function startEdit(object: SceneObject) {
    if (object.locked) return;
    setEditing(object.id);
    setTextValue(object.text || "");
    onEditState(true);
    setTimeout(() => {
      textarea.current?.focus();
    }, 0);
  }
  function endEdit(cancel = false) {
    if (!editing || composing.current) return;
    if (!cancel) onChange(editing, { text: textValue });
    setEditing(null);
    onEditState(false);
  }
  const patch = (object: SceneObject) => (changes: Partial<SceneObject>) =>
    onChange(object.id, changes);
  return (
    <div className="canvas-host" ref={host}>
      <div className="canvas-scroll">
        <div
          className="canvas-board"
          style={{ width: width + 60, height: height + 60 }}
        >
          <span className="canvas-width-label">{face.width_mm} mm</span>
          <span className="canvas-height-label">{face.height_mm} mm</span>
          <div className="canvas-paper" style={{ width, height }}>
            <Stage
              width={width}
              height={height}
              ref={stage}
              onMouseDown={(e) => {
                if (e.target === e.target.getStage()) {
                  onSelect(null);
                  endEdit();
                }
              }}
            >
              <Layer>
                <Rect
                  x={0}
                  y={0}
                  width={width}
                  height={height}
                  fill={face.background || "#f5f0e5"}
                  onClick={() => {
                    onSelect(null);
                    endEdit();
                  }}
                />
                {[...face.objects]
                  .sort((a, b) => a.z_index - b.z_index)
                  .filter((o) => o.visible !== false)
                  .map((object) =>
                    object.type === "image" ? (
                      <AssetImage
                        key={object.id}
                        object={object}
                        scale={scale}
                        onSelect={() => onSelect(object.id)}
                        onChange={patch(object)}
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
                        draggable={!object.locked}
                        onClick={() => onSelect(object.id)}
                        onTap={() => onSelect(object.id)}
                        onDragEnd={(e) =>
                          patch(object)({
                            x_mm: roundMM(e.target.x() / scale),
                            y_mm: roundMM(e.target.y() / scale),
                          })
                        }
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
                        fontFamily="NotoSansKR"
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
                        draggable={!object.locked}
                        onClick={() => onSelect(object.id)}
                        onTap={() => onSelect(object.id)}
                        onDblClick={() => startEdit(object)}
                        onDblTap={() => startEdit(object)}
                        onDragEnd={(e) =>
                          patch(object)({
                            x_mm: roundMM(e.target.x() / scale),
                            y_mm: roundMM(e.target.y() / scale),
                          })
                        }
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
                {guides && (
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
        <div className="canvas-font-notice">한글 글꼴을 불러오고 있어요…</div>
      )}
    </div>
  );
}
