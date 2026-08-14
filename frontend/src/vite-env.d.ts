// Build-time environment exposed by Vite. Declared locally so the identity
// login base URL is typed without depending on vite/client being in `types`.
interface ImportMetaEnv {
  /** Base URL of the axor-identity login service (default `/identity`). */
  readonly VITE_IDENTITY_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
