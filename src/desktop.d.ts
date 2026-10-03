export {}
declare global {
  interface Window {
    bingduDesktop?: {
      info: () => Promise<{ version: string; dataDirectory: string; desktop: true }>
      openDataDirectory: () => Promise<string>
      startOidc: (provider: string) => Promise<void>
    }
  }
}
