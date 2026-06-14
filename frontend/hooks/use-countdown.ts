"use client";

import { useEffect, useMemo, useState } from "react";

export type CountdownPhase = "UPCOMING" | "ACTIVE" | "ENDED";

export function useCountdown(startAt: string, endAt: string) {
  const startMs = useMemo(() => new Date(startAt).getTime(), [startAt]);
  const endMs = useMemo(() => new Date(endAt).getTime(), [endAt]);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const phase: CountdownPhase =
    now < startMs ? "UPCOMING" : now >= endMs ? "ENDED" : "ACTIVE";
  const target = phase === "UPCOMING" ? startMs : endMs;
  const remainingMs = Math.max(0, target - now);
  const totalSeconds = Math.floor(remainingMs / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const text = [hours, minutes, seconds]
    .map((value) => String(value).padStart(2, "0"))
    .join(":");

  return { phase, remainingMs, text };
}
