# Helm — AI Router & Home Assistant iPhone App

**Helm** steers both your AI models (`DeepSeek v4.1-Flash`, `Claude`, `Gemini`) and your Home Assistant smart home from a single native iPhone app.

1. **`ha-addon/` — Home Assistant OS (HAOS) Add-on (Python / FastAPI / SQLite)**:
   - Fast triage with **DeepSeek v4.1-Flash** via [Cheaper Inference](https://www.cheaperinference.com/) (`https://api.cheaperinference.com/v1` with `x-ci-model-lock`).
   - Automatic escalation to **Claude** or **Gemini** based on an editable routing prompt (plus manual override from the iPhone app).
   - Direct Home Assistant Core control via `SUPERVISOR_TOKEN` (`http://supervisor/core/api`) for searching entities, reading states/calendars, and calling any service, scene, or script.
   - **Backend-enforced Safety Gate** that blocks sensitive domains (`lock`, `alarm_control_panel`, `cover`, or custom entity IDs) until approved with **Face ID** on your iPhone.
   - **ElevenLabs** streaming TTS proxy (`POST /v1/tts`) so your ElevenLabs API key stays on your home server.
2. **`ios-app/` — Native SwiftUI iPhone App (iOS 17+, zero external dependencies)**:
   - Multi-thread chat with synced SQLite history, SSE token streaming, model badges, and per-message model override (`Auto`, `DeepSeek`, `Claude`, `Gemini`).
   - **Hands-Free Voice Mode** using on-device iOS `SFSpeechRecognizer` + streamed **ElevenLabs** voice replies.
   - Inline **Approve with Face ID** / **Cancel** confirmation cards (`LocalAuthentication`) for sensitive Home Assistant actions.
   - Connects remotely over your **Cloudflare Tunnel** using a Bearer token stored in **iOS Keychain**.

---

## 1. Install the HAOS Add-on

### Option A: Install via GitHub Repository in Home Assistant
1. Push this repository (`https://github.com/MS2620/Helm`) to GitHub.
2. In Home Assistant, go to **Settings → Add-ons → Add-on Store → ⋮ (top right) → Repositories**.
3. Paste `https://github.com/MS2620/Helm` and click **Add**.
4. Find **Helm AI Router**, click **Install**, then open the **Configuration** tab.

### Option B: Install as a Local Add-on
1. Copy the `ha-addon/` folder to `/addons/local/helm_ai_router` on your Home Assistant OS machine (via Samba / SSH / VS Code Server).
2. Go to **Settings → Add-ons → Add-on Store → ⋮ → Check for updates**, then install **Helm AI Router**.

### Add-on Configuration
Fill in the Add-on **Configuration** tab:
- `app_api_token`: Generate a long random token (e.g., `openssl rand -hex 32`) — you will paste this into the Helm iPhone app.
- `deepseek`:
  - `provider_type`: `openai_compatible`
  - `base_url`: `https://api.cheaperinference.com/v1`
  - `api_key`: Your Cheaper Inference API key
  - `model_id`: `deepseek-v4.1-flash`
  - `model_lock`: `true`
- `claude`:
  - Use `provider_type: anthropic` with `https://api.anthropic.com` (or `https://api.cheaperinference.com` / `openai_compatible` if routing Claude through Cheaper Inference).
- `gemini`:
  - Use `provider_type: gemini` with `https://generativelanguage.googleapis.com` (or `openai_compatible` if routing through an OpenAI-compatible gateway).
- `elevenlabs`:
  - `api_key`: Your ElevenLabs API key
  - `voice_id`: Your preferred ElevenLabs Voice ID
  - `model_id`: `eleven_flash_v2_5`

---

## 2. Expose via Cloudflare Tunnel

In your Cloudflare Zero Trust dashboard (or Cloudflare Tunnel HAOS Add-on configuration):
1. Add a Public Hostname (for example, `helm.yourdomain.com`).
2. Point the service to `http://homeassistant.local:8098` (or whichever host port is set in the Add-on's **Network** section).
3. Test from any terminal:
   ```bash
   curl https://helm.yourdomain.com/health
   # {"status":"ok","version":"0.1.0"}
   ```

---

## 3. Build & Install the Helm iPhone App (`.ipa` via GitHub Actions or Xcode)

### Option A: Automatic Unsigned `.ipa` from GitHub Actions (No Mac Required to Build)
Every push to `main` triggers [`.github/workflows/build.yml`](file:///d:/Projects/eveything/Helm/.github/workflows/build.yml), which:
1. Runs the HAOS Add-on `pytest` suite (`backend-test`) and the macOS Xcode iOS build (`build`) **in parallel**.
2. Uses cached `xcodegen` + `ldid` binaries, cached Xcode `DerivedData`, and incremental git-diff file timestamping so only changed Swift files recompile.
3. Packages `Helm-unsigned.ipa` (pseudo-signed with `ldid` using [`Helm.entitlements`](file:///d:/Projects/eveything/Helm/ios-app/Config/Helm.entitlements)), uploads it as a build artifact, and publishes a **GitHub Pre-Release** (`build-<run_number>`) with `Helm.ipa` attached.
4. Sideload `Helm.ipa` onto your iPhone using **Sideloadly**, **AltStore**, **SideStore**, or **Feather** (which re-signs it with your Apple ID).
5. **Optional TestFlight Upload**: Set the repository variable `ENABLE_TESTFLIGHT=true` and add `ASC_KEY_P8_BASE64`, `ASC_KEY_ID`, `ASC_ISSUER_ID`, and `APPLE_TEAM_ID` to repository secrets to automatically archive and upload to TestFlight on `main`.

### Option B: Build Directly in Xcode on Mac
1. Clone this repository on your Mac.
2. Generate the Xcode project using [XcodeGen](https://github.com/yonaskolb/XcodeGen):
   ```bash
   brew install xcodegen
   cd ios-app
   xcodegen generate
   open Helm.xcodeproj
   ```
3. In Xcode, select **Signing & Capabilities**, pick your Apple ID / Development Team, connect your iPhone, and press **Run (`⌘R`)**.
4. When Helm opens, enter your Cloudflare Tunnel URL (`https://helm.yourdomain.com`) and your `app_api_token`, tap **Test Connection**, and tap **Save**.
