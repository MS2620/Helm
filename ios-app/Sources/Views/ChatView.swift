import SwiftUI

struct ChatView: View {
    @ObservedObject var viewModel: ChatViewModel
    @FocusState private var isInputFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            if let error = viewModel.errorMessage {
                HStack(spacing: 8) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(.orange)
                    Text(error)
                        .font(.caption)
                        .foregroundStyle(.primary)
                        .lineLimit(3)
                    Spacer()
                    Button("Dismiss") {
                        viewModel.errorMessage = nil
                    }
                    .font(.caption.weight(.semibold))
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 8)
                .background(Color(uiColor: .secondarySystemBackground))
            }

            VoiceOverlayView(voiceManager: viewModel.voiceManager)

            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 14) {
                        if viewModel.messages.isEmpty && !viewModel.isStreaming {
                            emptyConversationState
                        }

                        ForEach(viewModel.messages) { message in
                            MessageBubbleView(
                                message: message,
                                onSpeak: {
                                    Task { await viewModel.speakMessage(message.content) }
                                }
                            )
                            .id(message.id)
                        }

                        ForEach(viewModel.pendingActions.filter { $0.status == "pending" }) { action in
                            ActionConfirmationCard(
                                action: action,
                                onApprove: {
                                    Task { await viewModel.approveActionWithFaceID(action) }
                                },
                                onCancel: {
                                    Task { await viewModel.cancelPendingAction(action) }
                                }
                            )
                            .id("action_\(action.id)")
                        }

                        if viewModel.isStreaming {
                            streamingBubble
                                .id("streaming_indicator")
                        }
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 16)
                }
                .onChange(of: viewModel.messages.count) { _, _ in
                    scrollToBottom(proxy: proxy)
                }
                .onChange(of: viewModel.streamingText) { _, _ in
                    scrollToBottom(proxy: proxy)
                }
                .onChange(of: viewModel.pendingActions.count) { _, _ in
                    scrollToBottom(proxy: proxy)
                }
            }

            Divider()

            composerBar
        }
        .navigationTitle(currentThreadTitle)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Picker("Model Override", selection: $viewModel.modelOverride) {
                        ForEach(ModelChoice.allCases) { choice in
                            Label(choice.displayName, systemImage: choice.symbolName)
                                .tag(choice)
                        }
                    }
                } label: {
                    HStack(spacing: 4) {
                        Image(systemName: viewModel.modelOverride.symbolName)
                        Text(viewModel.modelOverride.shortBadge)
                            .font(.caption.weight(.semibold))
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 5)
                    .background(Color(uiColor: .secondarySystemFill), in: Capsule())
                }
                .accessibilityLabel("Select Model Routing Mode")
            }

            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    viewModel.toggleVoiceReplies()
                } label: {
                    Image(systemName: viewModel.settings.autoSpeakRepliesInVoiceMode ? "speaker.wave.2.fill" : "speaker.slash.fill")
                        .foregroundStyle(viewModel.settings.autoSpeakRepliesInVoiceMode ? Color.accentColor : Color.secondary)
                        .accessibilityLabel("Toggle ElevenLabs Spoken Replies")
                }
            }

            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    viewModel.isShowingSettings = true
                } label: {
                    Image(systemName: "slider.horizontal.3")
                        .accessibilityLabel("Settings")
                }
            }
        }
    }

    private var currentThreadTitle: String {
        if let id = viewModel.selectedThreadId,
           let thread = viewModel.threads.first(where: { $0.id == id }) {
            return thread.title
        }
        return "Helm"
    }

    private var emptyConversationState: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Fast Triage + Smart Home Control")
                .font(.title3.weight(.semibold))

            Text("DeepSeek v4.1-Flash handles quick answers and Home Assistant actions directly, or escalates complex prompts to Claude and Gemini.")
                .font(.subheadline)
                .foregroundStyle(.secondary)

            VStack(alignment: .leading, spacing: 8) {
                suggestionChip("Turn on the living room lights to 60%")
                suggestionChip("Unlock the front door")
                suggestionChip("Write a Python script to analyze Home Assistant energy logs")
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(Color(uiColor: .secondarySystemGroupedBackground))
        )
        .padding(.top, 20)
    }

    private func suggestionChip(_ prompt: String) -> some View {
        Button {
            viewModel.composerText = prompt
            Task { await viewModel.sendCurrentMessage() }
        } label: {
            HStack {
                Text(prompt)
                    .font(.subheadline)
                    .foregroundStyle(.primary)
                    .multilineTextAlignment(.leading)
                Spacer()
                Image(systemName: "arrow.up.right")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .background(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(Color(uiColor: .tertiarySystemGroupedBackground))
            )
        }
        .buttonStyle(.plain)
    }

    private var streamingBubble: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                ModelBadgePill(
                    modelName: viewModel.activeModelName,
                    routingReason: viewModel.activeRoutingReason
                )
                if let toolStatus = viewModel.activeToolStatus {
                    Text(toolStatus)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            if viewModel.streamingText.isEmpty {
                ProgressView()
                    .controlSize(.small)
                    .padding(.vertical, 4)
            } else {
                Text(viewModel.streamingText)
                    .font(.body)
                    .textSelection(.enabled)
            }
        }
        .padding(12)
        .background(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(Color(uiColor: .secondarySystemBackground))
        )
    }

    private var composerBar: some View {
        HStack(alignment: .bottom, spacing: 10) {
            Button {
                Task { await viewModel.voiceManager.toggleHandsFree() }
            } label: {
                Image(systemName: viewModel.voiceManager.state == .inactive ? "mic.fill" : "waveform.circle.fill")
                    .font(.title3)
                    .foregroundStyle(viewModel.voiceManager.state == .inactive ? Color.primary : Color.red)
                    .frame(width: 38, height: 38)
                    .background(Color(uiColor: .secondarySystemBackground), in: Circle())
            }
            .accessibilityLabel("Toggle Hands-Free Voice Mode")

            TextField("Message or Home Assistant command…", text: $viewModel.composerText, axis: .vertical)
                .lineLimit(1...5)
                .focused($isInputFocused)
                .padding(.horizontal, 12)
                .padding(.vertical, 9)
                .background(
                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                        .fill(Color(uiColor: .secondarySystemBackground))
                )
                .onSubmit {
                    Task { await viewModel.sendCurrentMessage() }
                }

            Button {
                isInputFocused = false
                Task { await viewModel.sendCurrentMessage() }
            } label: {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.system(size: 32))
                    .foregroundStyle(canSend ? Color.accentColor : Color.secondary.opacity(0.4))
            }
            .disabled(!canSend)
            .accessibilityLabel("Send Message")
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(Color(uiColor: .systemBackground))
    }

    private var canSend: Bool {
        !viewModel.isStreaming &&
        !viewModel.composerText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private func scrollToBottom(proxy: ScrollViewProxy) {
        withAnimation(.easeOut(duration: 0.2)) {
            if viewModel.isStreaming {
                proxy.scrollTo("streaming_indicator", anchor: .bottom)
            } else if let lastAction = viewModel.pendingActions.last(where: { $0.status == "pending" }) {
                proxy.scrollTo("action_\(lastAction.id)", anchor: .bottom)
            } else if let lastMessage = viewModel.messages.last {
                proxy.scrollTo(lastMessage.id, anchor: .bottom)
            }
        }
    }
}

struct MessageBubbleView: View {
    let message: ChatMessage
    var onSpeak: (() -> Void)? = nil

    private var isUser: Bool {
        message.role == "user"
    }

    var body: some View {
        HStack {
            if isUser { Spacer(minLength: 42) }

            VStack(alignment: isUser ? .trailing : .leading, spacing: 6) {
                if !isUser {
                    HStack(spacing: 8) {
                        if let model = message.modelUsed {
                            ModelBadgePill(modelName: model, routingReason: message.routingReason)
                        }
                        if let onSpeak {
                            Button {
                                onSpeak()
                            } label: {
                                Label("Speak", systemImage: "speaker.wave.2")
                                    .font(.caption2.weight(.medium))
                                    .foregroundStyle(.secondary)
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel("Speak message with ElevenLabs")
                        }
                    }
                }

                Text(message.content)
                    .font(.body)
                    .foregroundStyle(isUser ? Color.white : Color.primary)
                    .textSelection(.enabled)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(
                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .fill(isUser ? Color.accentColor : Color(uiColor: .secondarySystemBackground))
                    )
            }

            if !isUser { Spacer(minLength: 42) }
        }
    }
}

struct ModelBadgePill: View {
    let modelName: String
    let routingReason: String?

    var body: some View {
        HStack(spacing: 5) {
            Image(systemName: iconName)
                .font(.caption2)
            Text(displayName)
                .font(.caption2.weight(.semibold))
            if let reason = routingReason,
               !reason.isEmpty,
               !["deepseek", "system", "ha_direct"].contains(modelName.lowercased()) {
                Text("• Escalated")
                    .font(.caption2)
                    .opacity(0.85)
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .background(badgeColor.opacity(0.14), in: Capsule())
        .foregroundStyle(badgeColor)
    }

    private var displayName: String {
        switch modelName.lowercased() {
        case "deepseek": return "DeepSeek v4.1-Flash"
        case "claude": return "Claude"
        case "gemini": return "Gemini"
        case "ha_direct": return "Home Assistant (Instant)"
        case "system": return "Face ID Confirmed"
        default: return modelName.capitalized
        }
    }

    private var iconName: String {
        switch modelName.lowercased() {
        case "deepseek": return "bolt.fill"
        case "claude": return "sparkles"
        case "gemini": return "globe"
        case "ha_direct": return "house.fill"
        case "system": return "faceid"
        default: return "cpu"
        }
    }

    private var badgeColor: Color {
        switch modelName.lowercased() {
        case "deepseek": return .blue
        case "claude": return .orange
        case "gemini": return .indigo
        case "ha_direct": return .teal
        case "system": return .green
        default: return .secondary
        }
    }
}
