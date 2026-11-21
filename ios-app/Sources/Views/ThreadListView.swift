import SwiftUI

struct ThreadListView: View {
    @ObservedObject var viewModel: ChatViewModel

    var body: some View {
        List(selection: Binding(
            get: { viewModel.selectedThreadId },
            set: { newId in
                if let newId {
                    Task { await viewModel.selectThread(newId) }
                }
            }
        )) {
            Section("Conversations") {
                if viewModel.threads.isEmpty {
                    ContentUnavailableView(
                        "No Conversations Yet",
                        systemImage: "bubble.left.and.bubble.right",
                        description: Text("Start a new chat to route questions across DeepSeek, Claude, Gemini, and Home Assistant.")
                    )
                } else {
                    ForEach(viewModel.threads) { thread in
                        NavigationLink(value: thread.id) {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(thread.title)
                                    .font(.body.weight(.medium))
                                    .lineLimit(1)
                                Text(formattedTimestamp(thread.updatedAt))
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            .padding(.vertical, 2)
                        }
                        .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                            Button(role: .destructive) {
                                Task { await viewModel.deleteThread(thread) }
                            } label: {
                                Label("Delete", systemImage: "trash")
                            }
                        }
                    }
                }
            }
        }
        .navigationTitle("Helm")
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Button {
                    viewModel.isShowingSettings = true
                } label: {
                    Image(systemName: "gearshape")
                        .accessibilityLabel("Connection Settings")
                }
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    Task { await viewModel.createNewThread() }
                } label: {
                    Image(systemName: "square.and.pencil")
                        .accessibilityLabel("New Conversation")
                }
            }
        }
        .refreshable {
            await viewModel.refreshThreads()
        }
    }

    private func formattedTimestamp(_ isoString: String) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let date = formatter.date(from: isoString) ?? ISO8601DateFormatter().date(from: isoString)
        guard let date else { return "" }
        return date.formatted(date: .abbreviated, time: .shortened)
    }
}
