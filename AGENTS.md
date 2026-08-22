# Repository-specific instructions

## Deployment boundary
- The standalone Rust HTTP adapter (`medicine-core-web`, the Compose `web` service, and browser-served development deployment) is for development and testing only. Do not treat it as a production deployment target unless the user explicitly changes this policy.
- Do not prioritize production-grade web authentication, public-network exposure hardening, or web-server deployment work during normal reviews or implementation unless explicitly requested.
- Keep the development web service local-only by default. Do not expose it externally as part of routine setup.
- The static UI is shared with the Android WebView, so changes to shared frontend assets must still be validated for the Android app. “Development-only web” refers to the standalone HTTP deployment, not to the shared UI code itself.
