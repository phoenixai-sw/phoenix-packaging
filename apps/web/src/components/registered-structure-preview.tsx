import type { StructureGeometry } from "@/lib/registered-structures";
import styles from "./registered-structures.module.css";

export function RegisteredStructurePreview({
  geometry,
}: {
  geometry: StructureGeometry;
}) {
  return (
    <div className={styles.preview}>
      <svg
        role="img"
        aria-label="등록 구조의 면 배치와 안전영역 미리보기"
        viewBox={`-5 -5 ${geometry.net_width_mm + 10} ${geometry.net_height_mm + 10}`}
      >
        <title>등록 구조 검토 · 제작 승인 도면 아님</title>
        {geometry.structural_parts?.map((part) => (
          <rect
            key={part.id}
            x={part.x_mm}
            y={part.y_mm}
            width={part.width_mm}
            height={part.height_mm}
            fill="#e0e0d8"
            stroke="#949a91"
          />
        ))}
        {geometry.faces.map((face) => (
          <g
            key={face.id}
            transform={`translate(${face.net.x_mm} ${face.net.y_mm}) rotate(${face.net.rotation_deg || 0} ${face.width_mm / 2} ${face.height_mm / 2})`}
          >
            <rect
              width={face.width_mm}
              height={face.height_mm}
              fill="#fffdf8"
              stroke="#203b30"
              strokeWidth={1}
            />
            <rect
              x={face.regions.safe.x_mm}
              y={face.regions.safe.y_mm}
              width={face.regions.safe.width_mm}
              height={face.regions.safe.height_mm}
              fill="#dcecdf"
              fillOpacity={0.45}
              stroke="#538566"
              strokeWidth={0.7}
              strokeDasharray="3 2"
            />
            <text
              x={face.width_mm / 2}
              y={face.height_mm / 2}
              textAnchor="middle"
              dominantBaseline="middle"
              fontSize={Math.max(5, Math.min(14, face.width_mm / 12))}
              fill="#203b30"
            >
              {face.name}
            </text>
          </g>
        ))}
      </svg>
      <p className="field-hint">
        실선: 면 외곽 · 녹색 점선: 안전영역 · 회색: 인쇄하지 않는 구조 부품
      </p>
    </div>
  );
}
