"use client";
import { useEffect, useState } from "react";
import { Plus, Users, LoaderCircle } from "lucide-react";
import {
  ManagementPage,
  Feedback,
  Loading,
  Dialog,
  Empty,
} from "@/components/management";
import {
  useApiData,
  roleOf,
  type Role,
  type WorkspaceData,
} from "@/lib/business";
import { api, errorMessage } from "@/lib/api";
import { useSession } from "@/components/workspace";
type Member = {
  id: string;
  name: string;
  email: string;
  role: Role;
  workspace_ids: string[];
  is_active: boolean;
};
type Team = {
  members: Member[];
  invitations: Array<{ id: string; email: string; role: Role; status: string }>;
  seat_limit: number;
};
export default function TeamPage() {
  const session = useSession();
  const owner = roleOf(session) === "owner";
  const { data, loading, error, refresh } = useApiData<Team>("/team");
  const spaces = useApiData<{ items: WorkspaceData[] }>("/workspaces");
  const [dialog, setDialog] = useState<
    "invite" | "workspace" | "accept" | null
  >(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("editor");
  const [workspaceIds, setWorkspaceIds] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [invitationUrl, setInvitationUrl] = useState("");
  const [formError, setFormError] = useState("");
  useEffect(() => {
    const value = new URLSearchParams(window.location.search).get("invite");
    if (value) {
      setToken(value);
      setDialog("accept");
      window.history.replaceState({}, "", "/app/team");
    }
  }, []);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setFormError("");
    try {
      if (dialog === "workspace") {
        await api("/workspaces", {
          method: "POST",
          body: JSON.stringify({ name }),
        });
        spaces.refresh();
        setNotice("작업 공간을 만들었습니다.");
      } else if (dialog === "accept") {
        await api("/team/invitations/accept", {
          method: "POST",
          body: JSON.stringify({ token }),
        });
        window.location.assign("/app");
        return;
      } else {
        const invitation = await api<{ invitation_url: string }>(
          "/team/invitations",
          {
            method: "POST",
            body: JSON.stringify({ email, role, workspace_ids: workspaceIds }),
          },
        );
        setInvitationUrl(invitation.invitation_url);
        setNotice(
          "초대 링크를 만들었습니다. 지정한 이메일의 사용자에게 직접 전달해 주세요.",
        );
        refresh();
      }
      setDialog(null);
      setName("");
      setEmail("");
    } catch (e) {
      setFormError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function changeMember(member: Member, patch: Partial<Member>) {
    setBusy(true);
    setFormError("");
    try {
      await api(`/team/members/${member.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          role: member.role,
          workspace_ids: member.workspace_ids,
          is_active: member.is_active,
          ...patch,
        }),
      });
      refresh();
    } catch (e) {
      setFormError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <ManagementPage
      eyebrow="BETTER TOGETHER"
      title="팀과 작업 공간"
      description="역할별 접근 권한을 정하고 고객사별 프로젝트를 관리하세요."
      actions={
        <div className="management-actions">
          <button
            className="button button-light"
            onClick={() => {
              setDialog("accept");
              setFormError("");
            }}
          >
            초대 수락
          </button>
          {owner && (
            <button
              className="button button-orange"
              onClick={() => {
                setDialog("invite");
                setFormError("");
              }}
            >
              <Plus size={17} /> 팀원 초대
            </button>
          )}
        </div>
      }
    >
      <Feedback error={error || formError} notice={notice} />
      {invitationUrl && (
        <section className="management-card">
          <h2>초대 링크</h2>
          <p className="field-hint">
            이 링크는 지금만 표시됩니다. 지정한 Gmail 또는 Google Workspace
            계정으로 로그인한 분이 7일 안에 한 번 수락할 수 있습니다.
          </p>
          <label className="field">
            직접 전달할 링크
            <input
              readOnly
              value={invitationUrl}
              onFocus={(e) => e.target.select()}
            />
          </label>
          <button
            className="button button-light"
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(invitationUrl);
                setNotice("초대 링크를 복사했습니다.");
              } catch {
                setFormError("링크를 선택해 직접 복사해 주세요.");
              }
            }}
          >
            링크 복사
          </button>
        </section>
      )}
      {loading ? (
        <Loading />
      ) : (
        <>
          <section className="management-card">
            <div className="management-section-heading">
              <h2>
                <Users size={19} /> 팀원 {data?.members.length || 0} /{" "}
                {data?.seat_limit || 0}명
              </h2>
              <span className="pill">팀 공용 크레딧</span>
            </div>
            <div className="management-table-wrap">
              <table className="management-table">
                <thead>
                  <tr>
                    <th>팀원</th>
                    <th>역할</th>
                    <th>허용 작업 공간</th>
                    <th>상태</th>
                  </tr>
                </thead>
                <tbody>
                  {data?.members.map((member) => (
                    <tr key={member.id}>
                      <td>
                        <strong>{member.name}</strong>
                        <small>{member.email}</small>
                      </td>
                      <td>
                        <select
                          aria-label={`${member.name} 역할`}
                          disabled={!owner || busy || member.role === "owner"}
                          value={member.role}
                          onChange={(e) =>
                            void changeMember(member, {
                              role: e.target.value as Role,
                            })
                          }
                        >
                          <option
                            value="owner"
                            disabled={member.role !== "owner"}
                          >
                            소유자
                          </option>
                          <option value="editor">편집자</option>
                          <option value="viewer">열람자</option>
                        </select>
                      </td>
                      <td>
                        {spaces.data?.items.map((space) => (
                          <label className="compact-check" key={space.id}>
                            <input
                              type="checkbox"
                              disabled={
                                !owner || busy || member.role === "owner"
                              }
                              checked={
                                member.role === "owner" ||
                                member.workspace_ids.includes(space.id)
                              }
                              onChange={(e) =>
                                void changeMember(member, {
                                  workspace_ids: e.target.checked
                                    ? [...member.workspace_ids, space.id]
                                    : member.workspace_ids.filter(
                                        (id) => id !== space.id,
                                      ),
                                })
                              }
                            />
                            {space.name}
                          </label>
                        ))}
                      </td>
                      <td>
                        {member.role === "owner" ? (
                          "활성"
                        ) : (
                          <label className="compact-check">
                            <input
                              type="checkbox"
                              disabled={!owner || busy}
                              checked={member.is_active}
                              onChange={(e) =>
                                void changeMember(member, {
                                  is_active: e.target.checked,
                                })
                              }
                            />
                            활성
                          </label>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="field-hint">
              열람자는 디자인 수정·생성·크레딧 소비 권한이 없습니다. 소유자
              권한과 운영 관리자 권한은 별개입니다.
            </p>
          </section>
          <section className="management-card">
            <div className="management-section-heading">
              <h2>고객사 작업 공간</h2>
              {owner && (
                <button
                  className="button button-light button-sm"
                  onClick={() => {
                    setDialog("workspace");
                    setFormError("");
                  }}
                >
                  <Plus size={14} /> 공간 만들기
                </button>
              )}
            </div>
            {spaces.data?.items.length ? (
              <div className="workspace-chip-list">
                {spaces.data.items.map((space) => (
                  <div key={space.id}>
                    <strong>{space.name}</strong>
                    <p>
                      {space.description ||
                        "프로젝트를 만들 때 선택할 수 있습니다."}
                    </p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="field-hint">
                작업 공간을 만들어 프로젝트 접근 범위를 나눠 보세요.
              </p>
            )}
          </section>
          {!!data?.invitations.length && (
            <section className="management-card">
              <h2>보낸 초대</h2>
              <div className="management-table-wrap">
                <table className="management-table">
                  <tbody>
                    {data.invitations.map((invite) => (
                      <tr key={invite.id}>
                        <td>{invite.email}</td>
                        <td>{invite.role}</td>
                        <td>{invite.status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </>
      )}
      {dialog && (
        <Dialog
          title={
            dialog === "invite"
              ? "새 팀원 초대"
              : dialog === "workspace"
                ? "새 작업 공간"
                : "초대 수락"
          }
          onClose={() => setDialog(null)}
        >
          <form onSubmit={submit}>
            {dialog === "workspace" ? (
              <label className="field">
                공간 이름
                <input
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  maxLength={120}
                />
              </label>
            ) : dialog === "accept" ? (
              <>
                <label className="field">
                  초대 코드
                  <input
                    required
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                    autoComplete="off"
                  />
                </label>
                <p className="field-hint">
                  초대받은 Google 계정으로 로그인한 상태에서 전달받은 코드를
                  입력하세요.
                </p>
              </>
            ) : (
              <>
                <label className="field">
                  초대할 이메일
                  <input
                    type="email"
                    required
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                  />
                </label>
                <label className="field">
                  역할
                  <select
                    value={role}
                    onChange={(e) => setRole(e.target.value as Role)}
                  >
                    <option value="editor">편집자</option>
                    <option value="viewer">열람자</option>
                  </select>
                </label>
                <fieldset className="variant-form">
                  <legend>허용할 작업 공간</legend>
                  {spaces.data?.items.map((space) => (
                    <label className="compact-check" key={space.id}>
                      <input
                        type="checkbox"
                        checked={workspaceIds.includes(space.id)}
                        onChange={(e) =>
                          setWorkspaceIds((ids) =>
                            e.target.checked
                              ? [...ids, space.id]
                              : ids.filter((id) => id !== space.id),
                          )
                        }
                      />
                      {space.name}
                    </label>
                  ))}
                </fieldset>
              </>
            )}
            <Feedback error={formError} />
            <button className="button button-dark full-width" disabled={busy}>
              {busy ? (
                <LoaderCircle className="spin" size={17} />
              ) : dialog === "invite" ? (
                "초대 링크 만들기"
              ) : dialog === "workspace" ? (
                "공간 생성"
              ) : (
                "초대 확인 후 합류"
              )}
            </button>
          </form>
        </Dialog>
      )}
    </ManagementPage>
  );
}
