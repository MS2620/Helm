import SwiftUI

struct VoiceOverlayView: View {
    @ObservedObject var voiceManager: VoiceSessionManager

    var body: some View {
        if voiceManager.state != .inactive {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 10) {
                    Image(systemName: iconName)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(accentColor)
                        .symbolEffect(.pulse, isActive: voiceManager.state == .listening || voiceManager.state == .speaking)

                    VStack(alignment: .leading, spacing: 2) {
                        Text(statusTitle)
                            .font(.subheadline.weight(.semibold))
                        if !voiceManager.liveTranscript.isEmpty {
                            Text(voiceManager.liveTranscript)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        }
                    }

                    Spacer()

                    Button {
                        voiceManager.stopHandsFreeSession()
                    } label: {
                        Label("Stop Voice", systemImage: "xmark.circle.fill")
                            .labelStyle(.iconOnly)
                            .font(.title3)
                            .foregroundStyle(.secondary)
                    }
                    .accessibilityLabel("Stop Hands-Free Voice Mode")
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(Color(uiColor: .secondarySystemBackground))
            )
            .padding(.horizontal, 12)
            .padding(.top, 6)
        }
    }

    private var iconName: String {
        switch voiceManager.state {
        case .inactive:
            return "mic.slash"
        case .listening:
            return "waveform"
        case .processing:
            return "ellipsis.bubble"
        case .speaking:
            return "speaker.wave.2.fill"
        case .pausedForFaceID:
            return "faceid"
        }
    }

    private var statusTitle: String {
        switch voiceManager.state {
        case .inactive:
            return "Voice Off"
        case .listening:
            return "Hands-Free Listening (pause 1.5s to send)"
        case .processing:
            return "Routing & Thinking…"
        case .speaking:
            return "Speaking with ElevenLabs…"
        case .pausedForFaceID:
            return "Paused — Approve or Cancel with Face ID below"
        }
    }

    private var accentColor: Color {
        switch voiceManager.state {
        case .listening:
            return .red
        case .processing:
            return .blue
        case .speaking:
            return .purple
        case .pausedForFaceID:
            return .orange
        case .inactive:
            return .secondary
        }
    }
}
