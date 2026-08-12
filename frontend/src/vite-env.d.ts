/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_ORIGIN?: string;
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_WS_URL?: string;
  readonly VITE_USE_MOCK?: string;
  readonly VITE_DEFAULT_DISTRICT_ID?: string;
  readonly VITE_DEFAULT_PRIORITY_SCORE_ID?: string;
  readonly VITE_3D_ANALYSIS_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
