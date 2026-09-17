"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowRight,
  ArrowUpRight,
  Clock3,
  FileText,
  LoaderCircle,
  Plus,
  Search,
} from "lucide-react";
import { useSession } from "@/components/workspace";
import { canEdit } from "@/lib/business";
import { packagingMedia } from "@/lib/media";
import { ProjectPreview } from "@/components/project-preview";
import { api, errorMessage } from "@/lib/api";
import type { Project } from "@editor/model";
export default function Dashboard() {
  const session = useSession();
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    setError("");
    setLoading(true);
    api<{ items: Project[] }>("/projects")
      .then((result) => {
        if (active) setProjects(result.items);
      })
      .catch((e) => {
        if (active) setError(errorMessage(e));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [retry]);
  const filtered = projects.filter((p) =>
    `${p.name} ${p.product_name} ${p.brand_name}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  return (
    <main className="dashboard-content">
      <div className="page-heading">
        <div className="eyebrow">YOUR CREATIVE SPACE</div>
        <h1>{session?.user.name}님의 작업 공간</h1>
        <p>작은 아이디어가 좋은 패키지가 되는 곳.</p>
      </div>
      <section className="dashboard-banner dashboard-banner-photographic">
        <div>
          <span className="pill">새로운 시작</span>
          <h2>
            다음 제품의 첫인상,
            <br />
            여기서 만들어 보세요.
          </h2>
          <p>포장 규격을 고르고 우리 브랜드의 이야기를 담아보세요.</p>
          {canEdit(session) && (
            <Link href="/app/projects/new" className="button button-dark">
              새 패키지 만들기 <ArrowUpRight size={17} />
            </Link>
          )}
        </div>
        <div className="dashboard-banner-image">
          <img
            src={packagingMedia.hero}
            alt="브랜드의 새 출발에 영감을 주는 식품 패키지 디자인 콘셉트"
            width={1536}
            height={1024}
            decoding="async"
          />
          <span>AI DESIGN CONCEPT</span>
        </div>
      </section>
      <section className="projects-section">
        <div className="projects-heading">
          <h2>
            내 프로젝트 <span>{projects.length}</span>
          </h2>
          <label className="search-field">
            <Search size={17} />
            <input
              aria-label="프로젝트 검색"
              placeholder="프로젝트 검색"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
        </div>
        {loading ? (
          <div className="loading-state">
            <LoaderCircle className="spin" /> 프로젝트를 불러오고 있어요.
          </div>
        ) : error ? (
          <div className="empty-state">
            <p role="alert">{error}</p>
            <button
              className="button button-light"
              onClick={() => setRetry((v) => v + 1)}
            >
              다시 불러오기
            </button>
          </div>
        ) : (
          <div className="project-grid">
            {filtered.map((project, index) => (
              <Link
                key={project.id}
                className="project-card"
                href={`/app/projects/${project.id}/editor`}
              >
                <div className={`project-thumbnail thumbnail-${index % 3}`}>
                  <ProjectPreview project={project} />
                  <span className="project-state">검토용</span>
                </div>
                <div className="project-card-info">
                  <h3>{project.name}</h3>
                  <p>
                    {project.brand_name || "브랜드 미입력"} <span>·</span>{" "}
                    {project.width_mm} × {project.height_mm} mm
                  </p>
                  <div>
                    <span>
                      <Clock3 size={12} />
                      {new Date(project.updated_at).toLocaleDateString("ko-KR")}
                    </span>
                    <ArrowUpRight size={17} />
                  </div>
                </div>
              </Link>
            ))}
            {!query && canEdit(session) && (
              <Link className="new-project-card" href="/app/projects/new">
                <span>
                  <Plus size={26} />
                </span>
                <h3>새로운 아이디어 시작</h3>
                <p>나만의 패키지를 만들어 보세요.</p>
              </Link>
            )}
            {query && !filtered.length && (
              <div className="empty-state">
                <Search />
                <h3>검색 결과가 없어요.</h3>
                <p>다른 상품명이나 프로젝트 이름으로 찾아보세요.</p>
              </div>
            )}
          </div>
        )}
      </section>
      <div className="dashboard-tip">
        <FileText size={19} />
        <div>
          <strong>파일을 출력하기 전에 확인해 주세요.</strong>
          <p>
            현재 제공하는 구조와 파일은 검토용입니다. 실제 인쇄 제작에는 제조사
            도면과 인쇄 조건 확인이 필요합니다.
          </p>
        </div>
        <Link href="/pricing" aria-label="서비스 범위 알아보기">
          <ArrowRight size={18} />
        </Link>
      </div>
    </main>
  );
}
