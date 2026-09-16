"use client";
import { useState } from "react";
import { LoaderCircle } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { Feedback } from "./management";
export function PrinterIntake({
  projectId,
  jobId,
  onDone,
}: {
  projectId: string;
  jobId: string;
  onDone: () => void;
}) {
  const [manufacturer, setManufacturer] = useState("");
  const [status, setStatus] = useState("submitted");
  const [source, setSource] = useState("test");
  const [rejectionKind, setRejectionKind] = useState("technical");
  const [category, setCategory] = useState("file");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/printer-intakes", {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId,
          job_id: jobId,
          manufacturer,
          status,
          record_source: source,
          ...(status === "rejected" ? { rejection_kind: rejectionKind } : {}),
          category,
          notes,
        }),
      });
      onDone();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit}>
      <p className="dialog-description">
        시험 기록과 제조사 회신을 구분해 기록하세요. 제조사 회신은 완료된 제작용
        출력에만 연결할 수 있습니다. 작성자가 확인한 기록이며, 도면 승인이나
        출력 검수 상태를 자동으로 바꾸지 않습니다.
      </p>
      <label className="field">
        기록 출처
        <select value={source} onChange={(e) => setSource(e.target.value)}>
          <option value="test">내부 시험 기록</option>
          <option value="manufacturer">실제 제조사 회신</option>
        </select>
      </label>
      <label className="field">
        제조사
        <input
          required
          maxLength={160}
          value={manufacturer}
          onChange={(e) => setManufacturer(e.target.value)}
        />
      </label>
      <div className="form-two-columns">
        <label className="field">
          입고 상태
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="submitted">검토 요청함</option>
            <option value="accepted">기술적 문제 없이 접수됨</option>
            <option value="rejected">수정 요청 받음</option>
          </select>
        </label>
        <label className="field">
          확인 항목
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          >
            {Object.entries({
              geometry: "도면·가공",
              font: "글꼴",
              color: "색상",
              resolution: "해상도",
              content: "표시사항",
              file: "파일 형식",
              other: "기타",
            }).map(([key, label]) => (
              <option value={key} key={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>
      {status === "rejected" && (
        <label className="field">
          수정 요청 분류
          <select
            value={rejectionKind}
            onChange={(e) => setRejectionKind(e.target.value)}
          >
            <option value="technical">기술적 반려 · 파일·인쇄 조건 문제</option>
            <option value="aesthetic">미적 수정 · 취향·디자인 변경</option>
          </select>
        </label>
      )}
      <label className="field">
        제조사 의견과 확인 내용
        <textarea
          required
          minLength={3}
          maxLength={4000}
          rows={4}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
        />
      </label>
      <Feedback error={error} />
      <button className="button button-dark full-width" disabled={busy}>
        {busy ? (
          <LoaderCircle size={17} className="spin" />
        ) : (
          "확인한 결과 기록"
        )}
      </button>
    </form>
  );
}
