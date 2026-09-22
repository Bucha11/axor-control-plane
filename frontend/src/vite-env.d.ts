// Build-time environment exposed by Vite. Declared locally so the identity
// login base URL is typed without depending on vite/client being in `types`.
interface ImportMetaEnv {
  /** Base URL of the axor-identity login service (default `/identity`). */
  readonly VITE_IDENTITY_URL?: string;
  /** Address agents use to reach the proxy's /t/{tool}/ routes (default http://127.0.0.1:8401). */
  readonly VITE_PROXY_PUBLIC_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
