/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "live" talks to caveat.api; anything else uses the recorded mock transport. */
  readonly VITE_CAVEAT_TRANSPORT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
