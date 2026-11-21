# AI Router & Home Assistant Assistant Add-on

Multi-model AI router (`DeepSeek v4.1-Flash`, `Claude`, `Gemini`) with native Home Assistant control, Face ID safety confirmation gate, and ElevenLabs streaming TTS for the companion SwiftUI iPhone app.

## Configuration

1. Set a strong random `app_api_token`.
2. Add your Cheaper Inference API key under `deepseek.api_key` (`https://api.cheaperinference.com/v1`).
3. Configure `claude` and `gemini` (either via official API keys or an OpenAI/Anthropic-compatible gateway).
4. Add your `elevenlabs.api_key` and `elevenlabs.voice_id` for hands-free spoken replies.
5. Point a Cloudflare Tunnel hostname at TCP port `8099`.
