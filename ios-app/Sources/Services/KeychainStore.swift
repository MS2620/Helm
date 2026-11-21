import Foundation
import Security

final class KeychainStore {
    static let shared = KeychainStore()
    private let service = "com.ms2620.Helm"

    private enum Keys {
        static let serverURL = "server_url"
        static let apiToken = "api_token"
        static let cfClientId = "cf_access_client_id"
        static let cfClientSecret = "cf_access_client_secret"
        static let autoSpeak = "auto_speak_voice_mode"
    }

    private init() {}

    func loadSettings() -> AppConnectionSettings {
        let url = readString(forKey: Keys.serverURL) ?? ""
        let token = readString(forKey: Keys.apiToken) ?? ""
        let cfId = readString(forKey: Keys.cfClientId) ?? ""
        let cfSecret = readString(forKey: Keys.cfClientSecret) ?? ""
        let autoSpeak = UserDefaults.standard.object(forKey: Keys.autoSpeak) as? Bool ?? true

        return AppConnectionSettings(
            serverURL: url,
            apiToken: token,
            cfAccessClientId: cfId,
            cfAccessClientSecret: cfSecret,
            autoSpeakRepliesInVoiceMode: autoSpeak
        )
    }

    func saveSettings(_ settings: AppConnectionSettings) {
        writeString(settings.serverURL.trimmingCharacters(in: .whitespacesAndNewlines), forKey: Keys.serverURL)
        writeString(settings.apiToken.trimmingCharacters(in: .whitespacesAndNewlines), forKey: Keys.apiToken)
        writeString(settings.cfAccessClientId.trimmingCharacters(in: .whitespacesAndNewlines), forKey: Keys.cfClientId)
        writeString(settings.cfAccessClientSecret.trimmingCharacters(in: .whitespacesAndNewlines), forKey: Keys.cfClientSecret)
        UserDefaults.standard.set(settings.autoSpeakRepliesInVoiceMode, forKey: Keys.autoSpeak)
    }

    private func writeString(_ value: String, forKey account: String) {
        let data = Data(value.utf8)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account
        ]
        SecItemDelete(query as CFDictionary)

        guard !value.isEmpty else { return }

        var insert = query
        insert[kSecValueData as String] = data
        insert[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        SecItemAdd(insert as CFDictionary, nil)
    }

    private func readString(forKey account: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]
        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess, let data = result as? Data else {
            return nil
        }
        return String(data: data, encoding: .utf8)
    }
}
