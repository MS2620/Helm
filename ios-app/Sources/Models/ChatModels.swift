import Foundation

enum ModelChoice: String, CaseIterable, Identifiable, Codable {
    case auto = "auto"
    case deepseek = "deepseek"
    case claude = "claude"
    case gemini = "gemini"

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .auto: return "Auto (DeepSeek Triage)"
        case .deepseek: return "DeepSeek v4.1-Flash"
        case .claude: return "Claude"
        case .gemini: return "Gemini"
        }
    }

    var shortBadge: String {
        switch self {
        case .auto: return "Auto"
        case .deepseek: return "DeepSeek"
        case .claude: return "Claude"
        case .gemini: return "Gemini"
        }
    }

    var symbolName: String {
        switch self {
        case .auto: return "arrow.triangle.branch"
        case .deepseek: return "bolt.fill"
        case .claude: return "sparkles"
        case .gemini: return "globe"
        }
    }
}

struct ChatThread: Identifiable, Codable, Hashable {
    let id: String
    var title: String
    let createdAt: String
    var updatedAt: String

    enum CodingKeys: String, CodingKey {
        case id
        case title
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

struct ToolEventRecord: Codable, Hashable {
    let tool: String
    let status: String
    let summary: String?
}

struct ChatMessage: Identifiable, Codable, Hashable {
    let id: String
    let threadId: String
    let role: String
    var content: String
    var modelUsed: String?
    var routingReason: String?
    var toolEvents: [ToolEventRecord]
    let createdAt: String

    enum CodingKeys: String, CodingKey {
        case id
        case threadId = "thread_id"
        case role
        case content
        case modelUsed = "model_used"
        case routingReason = "routing_reason"
        case toolEvents = "tool_events"
        case createdAt = "created_at"
    }
}

struct PendingAction: Identifiable, Codable, Hashable {
    let id: String
    let threadId: String
    let domain: String
    let service: String
    let entityId: String
    let summary: String
    var status: String
    let createdAt: String
    let expiresAt: String

    enum CodingKeys: String, CodingKey {
        case id
        case threadId = "thread_id"
        case domain
        case service
        case entityId = "entity_id"
        case summary
        case status
        case createdAt = "created_at"
        case expiresAt = "expires_at"
    }

    var isExpired: Bool {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: expiresAt) {
            return Date() >= date
        }
        let fallback = ISO8601DateFormatter()
        if let date = fallback.date(from: expiresAt) {
            return Date() >= date
        }
        return false
    }
}

struct ThreadDetailResponse: Codable {
    let thread: ChatThread
    let messages: [ChatMessage]
    let pendingActions: [PendingAction]

    enum CodingKeys: String, CodingKey {
        case thread
        case messages
        case pendingActions = "pending_actions"
    }
}

struct ThreadListResponse: Codable {
    let threads: [ChatThread]
}

struct ActionMutationResponse: Codable {
    let action: PendingAction
}

enum SSEStreamEvent {
    case routing(model: String, escalated: Bool, reason: String)
    case toolCall(tool: String, status: String)
    case actionRequired(PendingAction)
    case delta(text: String)
    case done(messageId: String, model: String, escalated: Bool, routingReason: String, fullText: String)
    case error(message: String)
}

struct AppConnectionSettings: Equatable {
    var serverURL: String
    var apiToken: String
    var cfAccessClientId: String
    var cfAccessClientSecret: String
    var autoSpeakRepliesInVoiceMode: Bool

    var isConfigured: Bool {
        !serverURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
        !apiToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
}
