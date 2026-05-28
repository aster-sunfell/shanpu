import { readPublicConfig } from "@/lib/config/public-config";

export function usePublicConfig() {
  return readPublicConfig();
}
