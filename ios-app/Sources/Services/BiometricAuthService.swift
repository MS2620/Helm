import Foundation
import LocalAuthentication

enum BiometricAuthError: LocalizedError {
    case unavailable(String)
    case failed(String)

    var errorDescription: String? {
        switch self {
        case .unavailable(let reason):
            return "Face ID / Passcode authentication is unavailable: \(reason)"
        case .failed(let reason):
            return "Authentication was not completed: \(reason)"
        }
    }
}

final class BiometricAuthService {
    static let shared = BiometricAuthService()

    private init() {}

    /// Authenticates the user with Face ID (with device passcode fallback) before confirming a sensitive HA action.
    func authenticateForSensitiveAction(summary: String) async throws {
        let context = LAContext()
        context.localizedFallbackTitle = "Use Passcode"

        var authError: NSError?
        let policy: LAPolicy = .deviceOwnerAuthentication

        guard context.canEvaluatePolicy(policy, error: &authError) else {
            throw BiometricAuthError.unavailable(
                authError?.localizedDescription ?? "Device authentication is not configured."
            )
        }

        let reason = "Confirm Home Assistant action: \(summary)"
        do {
            let success = try await context.evaluatePolicy(policy, localizedReason: reason)
            if !success {
                throw BiometricAuthError.failed("Identity verification did not succeed.")
            }
        } catch {
            throw BiometricAuthError.failed(error.localizedDescription)
        }
    }
}
