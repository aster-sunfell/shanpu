"use client";

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

export interface SessionUser {
  userId: number;
  username: string;
  nickname: string;
  points: number;
}

interface AuthState {
  token: string | null;
  user: SessionUser | null;
  hydrated: boolean;
  setHydrated: (hydrated: boolean) => void;
  setSession: (token: string, user: SessionUser) => void;
  updatePoints: (points: number) => void;
  patchUser: (patch: Partial<SessionUser>) => void;
  clearSession: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      user: null,
      hydrated: false,
      setHydrated: (hydrated) => set({ hydrated }),
      setSession: (token, user) => set({ token, user }),
      updatePoints: (points) =>
        set((s) => (s.user ? { user: { ...s.user, points } } : {})),
      patchUser: (patch) =>
        set((s) => (s.user ? { user: { ...s.user, ...patch } } : {})),
      clearSession: () => set({ token: null, user: null }),
    }),
    {
      name: "shanpu.auth",
      storage: createJSONStorage(() => localStorage),
      partialize: (s) => ({ token: s.token, user: s.user }),
      onRehydrateStorage: (state) => (rehydratedState) => {
        (rehydratedState ?? state).setHydrated(true);
      },
    },
  ),
);

export function getToken(): string | null {
  return useAuthStore.getState().token;
}
