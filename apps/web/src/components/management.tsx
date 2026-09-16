"use client";
import { LoaderCircle, X, Info } from "lucide-react";
import { useEffect, useRef } from "react";
export function ManagementPage({
  eyebrow,
  title,
  description,
  actions,
  children,
}: {
  eyebrow: string;
  title: string;
  description: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <main className="management-page">
      <header className="management-heading">
        <div className="page-heading">
          <div className="eyebrow">{eyebrow}</div>
          <h1>{title}</h1>
          <p>{description}</p>
        </div>
        {actions}
      </header>
      {children}
    </main>
  );
}
export function Feedback({
  error,
  notice,
}: {
  error?: string;
  notice?: string;
}) {
  return (
    <>
      {error && (
        <div className="alert alert-error" role="alert">
          <Info size={17} />
          {error}
        </div>
      )}
      {notice && (
        <div className="alert alert-info" role="status">
          <Info size={17} />
          {notice}
        </div>
      )}
    </>
  );
}
export function Loading() {
  return (
    <div className="loading-state">
      <LoaderCircle className="spin" /> 데이터를 불러오고 있어요.
    </div>
  );
}
export function Dialog({
  title,
  children,
  onClose,
}: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
    return () => ref.current?.close();
  }, []);
  return (
    <dialog ref={ref} className="management-dialog" onCancel={onClose}>
      <header>
        <h2>{title}</h2>
        <button className="icon-button" onClick={onClose} aria-label="창 닫기">
          <X size={20} />
        </button>
      </header>
      {children}
    </dialog>
  );
}
export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="management-empty">{children}</div>;
}
