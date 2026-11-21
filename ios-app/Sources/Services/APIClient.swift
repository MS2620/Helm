import Foundation

enum APIClientError: LocalizedError {
    case invalidServerURL
    case unauthorized
    case httpError(statusCode: Int, message: String)

    var errorDescription: String? {
        switch self {
        case .invalidServerURL:
            return "Check your Cloudflare Tunnel URL in Settings (for example, https://assistant.yourdomain.com)."
        case .unauthorized:
            return "Authentication failed (401). Verify your App Bearer Token in Settings."
        case .httpError(let code, let message):
            return "Server returned HTTP \(code): \(message)"
        }
    }
}

final class APIClient {
    private let session: URLSession

    init(session: URLSession = .shared) {
        self.session = session
    }

    private func buildRequest(
        path: String,
        method: String = "GET",
        settings: AppConnectionSettings,
        body: Data? = nil,
        accept: String = "application/json"
    ) throws -> URLRequest {
        let cleanBase = settings.serverURL
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard !cleanBase.isEmpty, let url = URL(string: "\(cleanBase)\(path)") else {
            throw APIClientError.invalidServerURL
        }

        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue(accept, forHTTPHeaderField: "Accept")
        request.setValue("Bearer \(settings.apiToken)", forHTTPHeaderField: "Authorization")

        if !settings.cfAccessClientId.isEmpty && !settings.cfAccessClientSecret.isEmpty {
            request.setValue(settings.cfAccessClientId, forHTTPHeaderField: "CF-Access-Client-Id")
            request.setValue(settings.cfAccessClientSecret, forHTTPHeaderField: "CF-Access-Client-Secret")
        }

        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return request
    }

    private func validateResponse(_ response: URLResponse, data: Data? = nil) throws {
        guard let http = response as? HTTPURLResponse else { return }
        if http.statusCode == 401 {
            throw APIClientError.unauthorized
        }
        if http.statusCode >= 400 {
            let text = data.flatMap { String(data: $0, encoding: .utf8) } ?? HTTPURLResponse.localizedString(forStatusCode: http.statusCode)
            throw APIClientError.httpError(statusCode: http.statusCode, message: text)
        }
    }

    func testConnection(settings: AppConnectionSettings) async throws -> String {
        let req = try buildRequest(path: "/v1/config/models", settings: settings)
        let (data, resp) = try await session.data(for: req)
        try validateResponse(resp, data: data)
        return "Connected to HAOS AI Router Add-on."
    }

    func listThreads(settings: AppConnectionSettings) async throws -> [ChatThread] {
        let req = try buildRequest(path: "/v1/threads", settings: settings)
        let (data, resp) = try await session.data(for: req)
        try validateResponse(resp, data: data)
        return try JSONDecoder().decode(ThreadListResponse.self, from: data).threads
    }

    func createThread(title: String = "New Chat", settings: AppConnectionSettings) async throws -> ChatThread {
        let payload = try JSONSerialization.data(withJSONObject: ["title": title])
        let req = try buildRequest(path: "/v1/threads", method: "POST", settings: settings, body: payload)
        let (data, resp) = try await session.data(for: req)
        try validateResponse(resp, data: data)
        return try JSONDecoder().decode(ChatThread.self, from: data)
    }

    func fetchThreadDetail(threadId: String, settings: AppConnectionSettings) async throws -> ThreadDetailResponse {
        let req = try buildRequest(path: "/v1/threads/\(threadId)", settings: settings)
        let (data, resp) = try await session.data(for: req)
        try validateResponse(resp, data: data)
        return try JSONDecoder().decode(ThreadDetailResponse.self, from: data)
    }

    func deleteThread(threadId: String, settings: AppConnectionSettings) async throws {
        let req = try buildRequest(path: "/v1/threads/\(threadId)", method: "DELETE", settings: settings)
        let (data, resp) = try await session.data(for: req)
        try validateResponse(resp, data: data)
    }

    func confirmAction(actionId: String, settings: AppConnectionSettings) async throws -> PendingAction {
        let req = try buildRequest(path: "/v1/actions/\(actionId)/confirm", method: "POST", settings: settings)
        let (data, resp) = try await session.data(for: req)
        try validateResponse(resp, data: data)
        return try JSONDecoder().decode(ActionMutationResponse.self, from: data).action
    }

    func cancelAction(actionId: String, settings: AppConnectionSettings) async throws -> PendingAction {
        let req = try buildRequest(path: "/v1/actions/\(actionId)/cancel", method: "POST", settings: settings)
        let (data, resp) = try await session.data(for: req)
        try validateResponse(resp, data: data)
        return try JSONDecoder().decode(ActionMutationResponse.self, from: data).action
    }

    func fetchTTSAudio(text: String, settings: AppConnectionSettings) async throws -> Data {
        let body = try JSONSerialization.data(withJSONObject: ["text": text])
        let req = try buildRequest(
            path: "/v1/tts",
            method: "POST",
            settings: settings,
            body: body,
            accept: "audio/mpeg"
        )
        let (data, resp) = try await session.data(for: req)
        try validateResponse(resp, data: data)
        return data
    }

    func streamMessage(
        threadId: String,
        content: String,
        modelOverride: ModelChoice,
        settings: AppConnectionSettings
    ) -> AsyncThrowingStream<SSEStreamEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let body = try JSONSerialization.data(withJSONObject: [
                        "content": content,
                        "model_override": modelOverride.rawValue
                    ])
                    let req = try self.buildRequest(
                        path: "/v1/threads/\(threadId)/messages",
                        method: "POST",
                        settings: settings,
                        body: body,
                        accept: "text/event-stream"
                    )

                    let (bytes, response) = try await self.session.bytes(for: req)
                    try self.validateResponse(response)

                    var currentEvent = "message"
                    for try await line in bytes.lines {
                        if Task.isCancelled { break }
                        let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
                        if trimmed.isEmpty { continue }

                        if trimmed.hasPrefix("event:") {
                            currentEvent = trimmed.dropFirst("event:".count).trimmingCharacters(in: .whitespaces)
                        } else if trimmed.hasPrefix("data:") {
                            let rawJSON = trimmed.dropFirst("data:".count).trimmingCharacters(in: .whitespaces)
                            if let parsed = Self.parseSSEPayload(event: currentEvent, jsonString: rawJSON) {
                                continuation.yield(parsed)
                            }
                        }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }

            continuation.onTermination = { _ in
                task.cancel()
            }
        }
    }

    private static func parseSSEPayload(event: String, jsonString: String) -> SSEStreamEvent? {
        guard let data = jsonString.data(using: .utf8) else { return nil }

        if event == "action_required" {
            if let pending = try? JSONDecoder().decode(PendingAction.self, from: data) {
                return .actionRequired(pending)
            }
        }

        guard let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return nil
        }

        switch event {
        case "routing":
            let model = dict["model"] as? String ?? "deepseek"
            let escalated = dict["escalated"] as? Bool ?? false
            let reason = dict["reason"] as? String ?? ""
            return .routing(model: model, escalated: escalated, reason: reason)

        case "tool_call":
            let tool = dict["tool"] as? String ?? "ha_tool"
            let status = dict["status"] as? String ?? "running"
            return .toolCall(tool: tool, status: status)

        case "delta":
            let text = dict["text"] as? String ?? ""
            return .delta(text: text)

        case "done":
            let msgId = dict["message_id"] as? String ?? UUID().uuidString
            let model = dict["model"] as? String ?? "deepseek"
            let escalated = dict["escalated"] as? Bool ?? false
            let reason = dict["routing_reason"] as? String ?? ""
            let fullText = dict["full_text"] as? String ?? ""
            return .done(
                messageId: msgId,
                model: model,
                escalated: escalated,
                routingReason: reason,
                fullText: fullText
            )

        case "error":
            let msg = dict["message"] as? String ?? "Unknown streaming error."
            return .error(message: msg)

        default:
            return nil
        }
    }
}
