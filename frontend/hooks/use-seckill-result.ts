"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api/client";
import { apiErrorMessage } from "@/lib/api/errors";
import { orderApi } from "@/lib/api/order";
import { userApi } from "@/lib/api/user";
import { useAuthStore } from "@/lib/auth/token-store";
import type { OrderDetailVO } from "@/types/order";

const POLL_INTERVAL_MS = positiveNumber(
  process.env.NEXT_PUBLIC_POLL_INTERVAL_MS,
  1000,
);
const POLL_MAX_TIMES = positiveNumber(
  process.env.NEXT_PUBLIC_POLL_MAX_TIMES,
  10,
);

type DetailState =
  | {
      orderId: string;
      status: "loading";
      order: null;
      error: null;
      polling: boolean;
      attempts: number;
      exhausted: false;
    }
  | {
      orderId: string;
      status: "ready";
      order: OrderDetailVO;
      error: null;
      polling: boolean;
      attempts: number;
      exhausted: boolean;
    }
  | {
      orderId: string;
      status: "missing";
      order: null;
      error: null;
      polling: false;
      attempts: number;
      exhausted: false;
    }
  | {
      orderId: string;
      status: "error";
      order: null;
      error: string;
      polling: false;
      attempts: number;
      exhausted: false;
    };

function positiveNumber(value: string | undefined, fallback: number) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

async function refreshPoints() {
  try {
    const result = await userApi.myPoints({ force: true });
    useAuthStore.getState().updatePoints(result.points);
  } catch (e) {
    const message = apiErrorMessage(e);
    if (message) toast.error(message);
  }
}

export function useSeckillResult(orderId: string) {
  const [reloadKey, setReloadKey] = useState(0);
  const paidOrderRef = useRef<string | null>(null);
  const [state, setState] = useState<DetailState>({
    orderId,
    status: "loading",
    order: null,
    error: null,
    polling: false,
    attempts: 0,
    exhausted: false,
  });

  const refresh = useCallback(() => {
    setReloadKey((key) => key + 1);
  }, []);

  useEffect(() => {
    let active = true;
    let attempts = 0;
    let timer: number | undefined;

    function clearTimer() {
      if (timer !== undefined) {
        window.clearTimeout(timer);
        timer = undefined;
      }
    }

    function scheduleNext(currentOrder?: OrderDetailVO) {
      if (attempts >= POLL_MAX_TIMES) {
        if (currentOrder) {
          setState({
            orderId,
            status: "ready",
            order: currentOrder,
            error: null,
            polling: false,
            attempts,
            exhausted: currentOrder.status === "PENDING",
          });
        }
        return;
      }

      attempts += 1;
      setState((current) => {
        if (current.status === "ready" && current.orderId === orderId) {
          return {
            ...current,
            polling: true,
            attempts,
            exhausted: false,
          };
        }
        return {
          orderId,
          status: "loading",
          order: null,
          error: null,
          polling: true,
          attempts,
          exhausted: false,
        };
      });
      timer = window.setTimeout(load, POLL_INTERVAL_MS);
    }

    async function load() {
      clearTimer();
      try {
        const order = await orderApi.detail(orderId);
        if (!active) return;

        if (order.status === "PAID" && paidOrderRef.current !== order.orderId) {
          paidOrderRef.current = order.orderId;
          void refreshPoints();
        }

        const shouldPoll = order.source === 4 && order.status === "PENDING";
        setState({
          orderId,
          status: "ready",
          order,
          error: null,
          polling: shouldPoll,
          attempts,
          exhausted: false,
        });

        if (shouldPoll) {
          scheduleNext(order);
        }
      } catch (e) {
        if (!active) return;

        if (e instanceof ApiError && e.code === 40400) {
          if (attempts < POLL_MAX_TIMES) {
            scheduleNext();
            return;
          }
          setState({
            orderId,
            status: "missing",
            order: null,
            error: null,
            polling: false,
            attempts,
            exhausted: false,
          });
          return;
        }

        setState({
          orderId,
          status: "error",
          order: null,
          error: apiErrorMessage(e, "订单加载失败") ?? "订单加载失败",
          polling: false,
          attempts,
          exhausted: false,
        });
      }
    }

    void load();

    return () => {
      active = false;
      clearTimer();
    };
  }, [orderId, reloadKey]);

  return { state, refresh };
}
