import Foundation
import SwiftUI

@MainActor
final class ChatViewModel: ObservableObject {
    @Published var settings: AppConnectionSettings
    @Published var threads: [ChatThread] = []
    @Published var selectedThreadId: String?
    @Published var messages: [ChatMessage] = []
    @Published var pendingActions: [PendingAction] = []

    @Published var composerText: String = ""
    @Published var modelOverride: ModelChoice = .auto

    @Published var isStreaming: Bool = false
    @Published var streamingText: String = ""
    @Published var activeModelName: String = "deepseek"
    @Published var activeEscalated: Bool = false
    @Published var activeRoutingReason: String = ""
    @Published var activeToolStatus: String?

    @Published var errorMessage: String?
    @Published var isShowingSettings: Bool = false

    let apiClient: APIClient
    let voiceManager: VoiceSessionManager

    init(
        apiClient: APIClient = APIClient(),
        voiceManager: VoiceSessionManager = VoiceSessionManager()
    ) {
        self.apiClient = apiClient
        self.voiceManager = voiceManager
        self.settings = KeychainStore.shared.loadSettings()

        self.voiceManager.onUtteranceFinalized = { [weak self] utterance in
            guard let self else { return }
            Task { @MainActor in
                self.composerText = utterance
                await self.sendCurrentMessage(fromVoice: true)
            }
        }
    }

    func bootstrap() async {
        guard settings.isConfigured else {
            isShowingSettings = true
            return
        }
        await refreshThreads()
    }

    func saveSettings(_ updated: AppConnectionSettings) {
        settings = updated
        KeychainStore.shared.saveSettings(updated)
    }

    func refreshThreads() async {
        guard settings.isConfigured else { return }
        do {
            errorMessage = nil
            let fetched = try await apiClient.listThreads(settings: settings)
            threads = fetched
            if let current = selectedThreadId, fetched.contains(where: { $0.id == current }) {
                await loadThreadDetail(threadId: current)
            } else if let first = fetched.first {
                selectedThreadId = first.id
                await loadThreadDetail(threadId: first.id)
            } else {
                await createNewThread()
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func createNewThread() async {
        guard settings.isConfigured else {
            isShowingSettings = true
            return
        }
        do {
            errorMessage = nil
            let created = try await apiClient.createThread(settings: settings)
            threads.insert(created, at: 0)
            selectedThreadId = created.id
            messages = []
            pendingActions = []
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func selectThread(_ threadId: String) async {
        selectedThreadId = threadId
        await loadThreadDetail(threadId: threadId)
    }

    func loadThreadDetail(threadId: String) async {
        guard settings.isConfigured else { return }
        do {
            let detail = try await apiClient.fetchThreadDetail(threadId: threadId, settings: settings)
            messages = detail.messages
            pendingActions = detail.pendingActions
            if let idx = threads.firstIndex(where: { $0.id == threadId }) {
                threads[idx] = detail.thread
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func deleteThread(_ thread: ChatThread) async {
        do {
            try await apiClient.deleteThread(threadId: thread.id, settings: settings)
            threads.removeAll { $0.id == thread.id }
            if selectedThreadId == thread.id {
                selectedThreadId = threads.first?.id
                if let nextId = selectedThreadId {
                    await loadThreadDetail(threadId: nextId)
                } else {
                    messages = []
                    pendingActions = []
                }
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func sendCurrentMessage(fromVoice: Bool = false) async {
        let text = composerText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isStreaming else { return }

        if selectedThreadId == nil {
            await createNewThread()
        }
        guard let threadId = selectedThreadId else { return }

        composerText = ""
        errorMessage = nil

        let optimisticUserMsg = ChatMessage(
            id: UUID().uuidString,
            threadId: threadId,
            role: "user",
            content: text,
            modelUsed: nil,
            routingReason: nil,
            toolEvents: [],
            createdAt: ISO8601DateFormatter().string(from: Date())
        )
        messages.append(optimisticUserMsg)

        isStreaming = true
        streamingText = ""
        activeModelName = modelOverride == .auto ? "deepseek" : modelOverride.rawValue
        activeEscalated = false
        activeRoutingReason = ""
        activeToolStatus = nil

        var triggeredFaceIDPause = false
        var completedReplyText = ""

        do {
            let stream = apiClient.streamMessage(
                threadId: threadId,
                content: text,
                modelOverride: modelOverride,
                settings: settings
            )

            for try await event in stream {
                switch event {
                case .routing(let model, let escalated, let reason):
                    activeModelName = model
                    activeEscalated = escalated
                    activeRoutingReason = reason

                case .toolCall(let tool, let status):
                    if status == "running" {
                        activeToolStatus = "Running \(tool.replacingOccurrences(of: "ha_", with: ""))…"
                    } else {
                        activeToolStatus = nil
                    }

                case .actionRequired(let pendingAction):
                    if !pendingActions.contains(where: { $0.id == pendingAction.id }) {
                        pendingActions.append(pendingAction)
                    }
                    triggeredFaceIDPause = true
                    voiceManager.pauseForFaceIDConfirmation()

                case .delta(let chunk):
                    streamingText += chunk

                case .done(let msgId, let model, _, let reason, let fullText):
                    completedReplyText = fullText
                    let assistantMsg = ChatMessage(
                        id: msgId,
                        threadId: threadId,
                        role: "assistant",
                        content: fullText,
                        modelUsed: model,
                        routingReason: reason,
                        toolEvents: [],
                        createdAt: ISO8601DateFormatter().string(from: Date())
                    )
                    messages.append(assistantMsg)
                    streamingText = ""
                    activeToolStatus = nil

                case .error(let errMsg):
                    errorMessage = errMsg
                }
            }

            await loadThreadDetail(threadId: threadId)
        } catch {
            errorMessage = error.localizedDescription
        }

        isStreaming = false
        activeToolStatus = nil

        // Play ElevenLabs voice reply if hands-free voice mode is active
        let shouldSpeak = (fromVoice || voiceManager.state != .inactive) && settings.autoSpeakRepliesInVoiceMode
        if shouldSpeak, !completedReplyText.isEmpty {
            await speakWithElevenLabs(
                text: completedReplyText,
                resumeListeningAfter: !triggeredFaceIDPause
            )
        } else if fromVoice && !triggeredFaceIDPause && voiceManager.state == .processing {
            voiceManager.startListening()
        }
    }

    func approveActionWithFaceID(_ action: PendingAction) async {
        do {
            errorMessage = nil
            try await BiometricAuthService.shared.authenticateForSensitiveAction(summary: action.summary)
            let updated = try await apiClient.confirmAction(actionId: action.id, settings: settings)
            if let idx = pendingActions.firstIndex(where: { $0.id == action.id }) {
                pendingActions[idx] = updated
            }
            if let threadId = selectedThreadId {
                await loadThreadDetail(threadId: threadId)
            }
            voiceManager.resumeAfterFaceIDIfActive()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func cancelPendingAction(_ action: PendingAction) async {
        do {
            errorMessage = nil
            let updated = try await apiClient.cancelAction(actionId: action.id, settings: settings)
            if let idx = pendingActions.firstIndex(where: { $0.id == action.id }) {
                pendingActions[idx] = updated
            }
            if let threadId = selectedThreadId {
                await loadThreadDetail(threadId: threadId)
            }
            voiceManager.resumeAfterFaceIDIfActive()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func speakWithElevenLabs(text: String, resumeListeningAfter: Bool) async {
        do {
            let audioData = try await apiClient.fetchTTSAudio(text: text, settings: settings)
            voiceManager.playElevenLabsAudio(data: audioData, resumeListeningAfter: resumeListeningAfter)
        } catch {
            errorMessage = "ElevenLabs TTS: \(error.localizedDescription)"
            if resumeListeningAfter {
                voiceManager.startListening()
            }
        }
    }
}
