import SwiftUI

struct SettingsView: View {
    @ObservedObject var viewModel: ChatViewModel
    @Environment(\.dismiss) private var dismiss

    @State private var draftSettings: AppConnectionSettings = AppConnectionSettings(
        serverURL: "",
        apiToken: "",
        cfAccessClientId: "",
        cfAccessClientSecret: "",
        autoSpeakRepliesInVoiceMode: true
    )
    @State private var connectionStatusMessage: String?
    @State private var isTestingConnection: Bool = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("https://assistant.yourdomain.com", text: $draftSettings.serverURL)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()

                    SecureField("App Bearer Token (app_api_token)", text: $draftSettings.apiToken)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                } header: {
                    Text("HAOS Add-on & Cloudflare Tunnel")
                } footer: {
                    Text("Point this to your Cloudflare Tunnel hostname forwarding to port 8099 of your HAOS AI Router Add-on.")
                }

                Section {
                    TextField("CF-Access-Client-Id (Optional)", text: $draftSettings.cfAccessClientId)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()

                    SecureField("CF-Access-Client-Secret (Optional)", text: $draftSettings.cfAccessClientSecret)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                } header: {
                    Text("Cloudflare Access Service Token (Optional)")
                } footer: {
                    Text("Only required if you added a Cloudflare Access Service Auth policy in front of your tunnel hostname.")
                }

                Section("Voice & ElevenLabs") {
                    Toggle("Auto-Speak Replies in Hands-Free Mode", isOn: $draftSettings.autoSpeakRepliesInVoiceMode)
                }

                Section {
                    Button {
                        Task { await runConnectionTest() }
                    } label: {
                        HStack {
                            Text("Test Connection")
                            Spacer()
                            if isTestingConnection {
                                ProgressView()
                            }
                        }
                    }
                    .disabled(!draftSettings.isConfigured || isTestingConnection)

                    if let status = connectionStatusMessage {
                        Text(status)
                            .font(.footnote)
                            .foregroundStyle(status.hasPrefix("Connected") ? .green : .red)
                    }
                }
            }
            .navigationTitle("Connection Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        dismiss()
                    }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        viewModel.saveSettings(draftSettings)
                        Task { await viewModel.refreshThreads() }
                        dismiss()
                    }
                    .disabled(!draftSettings.isConfigured)
                }
            }
            .onAppear {
                draftSettings = viewModel.settings
            }
        }
    }

    private func runConnectionTest() async {
        isTestingConnection = true
        connectionStatusMessage = nil
        do {
            let msg = try await viewModel.apiClient.testConnection(settings: draftSettings)
            connectionStatusMessage = msg
        } catch {
            connectionStatusMessage = error.localizedDescription
        }
        isTestingConnection = false
    }
}
