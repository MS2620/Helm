import SwiftUI

@main
struct AIHomeAssistantApp: App {
    @StateObject private var viewModel = ChatViewModel()

    var body: some Scene {
        WindowGroup {
            NavigationSplitView {
                ThreadListView(viewModel: viewModel)
            } detail: {
                ChatView(viewModel: viewModel)
            }
            .task {
                await viewModel.bootstrap()
            }
            .sheet(isPresented: $viewModel.isShowingSettings) {
                SettingsView(viewModel: viewModel)
            }
        }
    }
}
