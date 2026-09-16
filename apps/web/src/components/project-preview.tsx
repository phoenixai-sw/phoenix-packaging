import type { Project } from "@editor/model";
export function ProjectPreview({ project }: { project: Project }) {
  const face =
    project.scene?.faces?.find((f) => f.id === "front") ||
    project.scene?.faces?.[0];
  if (!face)
    return <div className="project-preview-placeholder">미리보기 준비 중</div>;
  return (
    <svg
      className="project-scene-preview"
      viewBox={`0 0 ${face.width_mm} ${face.height_mm}`}
      role="img"
      aria-label={`${project.name}의 저장된 앞면 디자인`}
    >
      <rect
        width={face.width_mm}
        height={face.height_mm}
        fill={face.background || "#f5f0e5"}
      />
      {[...face.objects]
        .sort((a, b) => a.z_index - b.z_index)
        .filter((o) => o.visible !== false)
        .map((o) => (
          <g
            key={o.id}
            transform={`translate(${o.x_mm} ${o.y_mm}) rotate(${o.rotation_deg || 0})`}
            opacity={o.opacity ?? 1}
          >
            {o.type === "text" ? (
              <foreignObject width={o.width_mm} height={o.height_mm}>
                <div
                  style={{
                    fontFamily: "NotoSansKR",
                    fontSize: ((o.font_size_pt || 16) * 25.4) / 72,
                    color: o.color || "#243829",
                    lineHeight: o.line_height ?? 1.2,
                    letterSpacing: ((o.letter_spacing || 0) * 25.4) / 72,
                    textAlign: o.align || "left",
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-all",
                    overflow: "hidden",
                    height: "100%",
                  }}
                >
                  {o.text}
                </div>
              </foreignObject>
            ) : o.type === "image" ? (
              <image
                href={`/api/v1/assets/${o.asset_id}/content`}
                width={o.width_mm}
                height={o.height_mm}
                preserveAspectRatio="none"
              />
            ) : o.shape === "ellipse" || o.shape === "circle" ? (
              <ellipse
                cx={o.width_mm / 2}
                cy={o.height_mm / 2}
                rx={o.width_mm / 2}
                ry={o.height_mm / 2}
                fill={o.fill || o.color || "#43664c"}
                stroke={o.stroke}
                strokeWidth={o.stroke_width_mm || 0}
              />
            ) : (
              <rect
                width={o.width_mm}
                height={o.height_mm}
                fill={o.fill || o.color || "#43664c"}
                stroke={o.stroke}
                strokeWidth={o.stroke_width_mm || 0}
              />
            )}
          </g>
        ))}
    </svg>
  );
}
