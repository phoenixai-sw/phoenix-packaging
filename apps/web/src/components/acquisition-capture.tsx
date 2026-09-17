"use client";
import { useEffect } from "react";
import { captureAcquisition } from "@/lib/acquisition";
export function AcquisitionCapture() {
  useEffect(() => { captureAcquisition(); }, []);
  return null;
}
