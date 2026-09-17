"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage, setEditorLease, clearEditorLease } from "./api";
export type EditorLease = {
  status: "active" | "available";
  editable: boolean;
  holder: { name: string; is_self: boolean } | null;
  expires_at: string | null;
  lease_token?: string;
  heartbeat_seconds?: number;
};
export function useEditorLease(
  projectId: string,
  allowed: boolean,
  ready: boolean,
) {
  const editor = useRef<string>(crypto.randomUUID()),
    token = useRef<string | null>(null),
    active = useRef(false),
    pending = useRef(false);
  const [lease, setLease] = useState<EditorLease>(),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [owned, setOwned] = useState(false);
  const expiry = useRef(0),
    generation = useRef(0),
    ownedProject = useRef<string | null>(null);
  const lose = useCallback(() => {
    token.current = null;
    ownedProject.current = null;
    expiry.current = 0;
    setEditorLease(projectId, null);
    setOwned(false);
  }, [projectId]);
  const status = useCallback(async () => {
    const value = await api<EditorLease>(
      `/projects/${projectId}/edit-session?editor_id=${editor.current}`,
    );
    if (active.current) setLease(value);
    return value;
  }, [projectId]);
  const acquire = useCallback(async () => {
    if (!allowed || !ready || pending.current) return;
    const attempt = generation.current;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      const value = await api<EditorLease>(
        `/projects/${projectId}/edit-session`,
        { method: "POST", body: JSON.stringify({ editor_id: editor.current }) },
      );
      if (!active.current || generation.current !== attempt) {
        if (value.lease_token)
          void api(`/projects/${projectId}/edit-session`, {
            method: "DELETE",
            body: JSON.stringify({
              editor_id: editor.current,
              lease_token: value.lease_token,
            }),
            keepalive: true,
          }).catch(() => {});
        return;
      }
      if (
        !value.editable ||
        !value.lease_token ||
        !(Date.parse(value.expires_at || "") > Date.now())
      )
        throw new Error("편집 권한을 확보하지 못했습니다.");
      token.current = value.lease_token;
      ownedProject.current = projectId;
      expiry.current = Date.parse(value.expires_at || "");
      setEditorLease(projectId, value.lease_token);
      setLease(value);
      setOwned(true);
    } catch (e) {
      if (active.current && generation.current === attempt) {
        lose();
        setError(errorMessage(e));
        await status().catch(() => {});
      }
    } finally {
      if (generation.current === attempt) {
        pending.current = false;
        if (active.current) setBusy(false);
      }
    }
  }, [projectId, allowed, ready, lose, status]);
  const release = useCallback(async () => {
    const value = token.current;
    if (!value) return;
    setBusy(true);
    lose();
    try {
      await api(`/projects/${projectId}/edit-session`, {
        method: "DELETE",
        body: JSON.stringify({ editor_id: editor.current, lease_token: value }),
        keepalive: true,
      });
      lose();
      await status();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }, [projectId, lose, status]);
  useEffect(() => {
    if (!ready) return;
    active.current = true;
    setEditorLease(projectId, null);
    generation.current += 1;
    pending.current = false;
    let stopped = false;
    // Deferring initial acquisition avoids duplicate ownership requests during Strict Mode setup.
    const first = setTimeout(() => {
      if (allowed) void acquire();
      else void status().catch((e) => setError(errorMessage(e)));
    }, 0);
    const timer = setInterval(async () => {
      if (stopped) return;
      if (token.current && allowed) {
        if (Date.now() >= expiry.current) {
          lose();
          setError(
            "편집 권한이 만료되었습니다. 편집 이어가기를 눌러 다시 확인해 주세요.",
          );
          return;
        }
        try {
          const renewing = token.current;
          const value = await api<EditorLease>(
            `/projects/${projectId}/edit-session`,
            {
              method: "PATCH",
              body: JSON.stringify({
                editor_id: editor.current,
                lease_token: renewing,
              }),
            },
          );
          if (stopped || token.current !== renewing) return;
          if (
            !value.editable ||
            !value.lease_token ||
            !(Date.parse(value.expires_at || "") > Date.now())
          )
            throw new Error("편집 권한을 유지하지 못했습니다.");
          token.current = value.lease_token;
          expiry.current = Date.parse(value.expires_at || "");
          setEditorLease(projectId, value.lease_token);
          setLease(value);
          setError("");
        } catch (e) {
          if (!stopped) {
            lose();
            setError(errorMessage(e));
          }
        }
      } else await status().catch(() => {});
    }, 30000);
    const expiration = setInterval(() => {
      if (token.current && Date.now() >= expiry.current) {
        lose();
        setError(
          "편집 권한이 만료되었습니다. 저장되지 않은 내용은 이 브라우저에 보관합니다.",
        );
      }
    }, 1000);
    const pagehide = () => {
      if (token.current)
        void api(`/projects/${projectId}/edit-session`, {
          method: "DELETE",
          body: JSON.stringify({
            editor_id: editor.current,
            lease_token: token.current,
          }),
          keepalive: true,
        }).catch(() => {});
    };
    window.addEventListener("pagehide", pagehide);
    return () => {
      active.current = false;
      generation.current += 1;
      stopped = true;
      clearTimeout(first);
      clearInterval(timer);
      clearInterval(expiration);
      window.removeEventListener("pagehide", pagehide);
      pagehide();
      token.current = null;
      expiry.current = 0;
      clearEditorLease(projectId);
    };
  }, [projectId, allowed, ready, acquire, lose, status]);
  return {
    lease,
    error,
    busy,
    owned: ready && allowed && owned && ownedProject.current === projectId,
    acquire,
    release,
    lose,
  };
}
