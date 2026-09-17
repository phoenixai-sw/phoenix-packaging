"use client";
import { useEffect } from "react";
import { apiRequest } from "@/lib/api-contract";

/** Local input timestamps only: no keystrokes, cursor coordinates, text or images. */
export function UsageActivityTracker({ projectId, readOnly }: { projectId: string; readOnly: boolean }) {
  useEffect(() => {
    if (readOnly) return;
    let lastInput = 0, pending = false, stopped = false;
    const onInput = (event: Event) => { if (event.isTrusted && document.visibilityState === "visible") lastInput = Date.now(); };
    const events = ["pointerdown","keydown","input"];
    events.forEach(name => window.addEventListener(name,onInput,{passive:true}));
    const timer = window.setInterval(() => {
      if (stopped || pending || document.visibilityState !== "visible" || !lastInput || Date.now()-lastInput > 35000) return;
      pending = true;
      apiRequest("post","/v1/metrics/activity", {body:{project_id:projectId}})
        .catch(() => { /* Optional estimate; auth/lease failure never alters editing. */ })
        .finally(() => { pending = false; });
    },30000);
    return () => { stopped=true; window.clearInterval(timer); events.forEach(name => window.removeEventListener(name,onInput)); };
  },[projectId,readOnly]);
  return null;
}
