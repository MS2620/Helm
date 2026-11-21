import SwiftUI

struct ActionConfirmationCard: View {
    let action: PendingAction
    let onApprove: () -> Void
    let onCancel: () -> Void

    @State private var isAuthenticating: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                Image(systemName: "lock.shield.fill")
                    .font(.title3)
                    .foregroundStyle(statusColor)

                VStack(alignment: .leading, spacing: 2) {
                    Text(headerTitle)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(.primary)

                    Text("\(action.domain).\(action.service) • \(action.entityId)")
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                }

                Spacer()

                statusPill
            }

            Text(action.summary)
                .font(.callout)
                .foregroundStyle(.primary)

            if action.status == "pending" && !action.isExpired {
                HStack(spacing: 10) {
                    Button(role: .cancel) {
                        onCancel()
                    } label: {
                        Text("Cancel")
                            .font(.subheadline.weight(.medium))
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 10)
                    }
                    .buttonStyle(.bordered)

                    Button {
                        isAuthenticating = true
                        onApprove()
                        isAuthenticating = false
                    } label: {
                        HStack(spacing: 6) {
                            Image(systemName: "faceid")
                            Text("Approve with Face ID")
                                .font(.subheadline.weight(.semibold))
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 10)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.orange)
                    .disabled(isAuthenticating)
                }
            }
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(Color(uiColor: .secondarySystemGroupedBackground))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(statusColor.opacity(0.35), lineWidth: 1)
        )
    }

    private var headerTitle: String {
        switch action.status {
        case "executed":
            return "Home Assistant Action Executed"
        case "cancelled":
            return "Home Assistant Action Cancelled"
        case "expired":
            return "Home Assistant Action Expired"
        default:
            return action.isExpired ? "Home Assistant Action Expired" : "Sensitive Action Requires Face ID"
        }
    }

    private var statusColor: Color {
        switch action.status {
        case "executed":
            return .green
        case "cancelled", "expired":
            return .secondary
        default:
            return action.isExpired ? .secondary : .orange
        }
    }

    private var statusPill: some View {
        let label: String = {
            if action.status == "pending" && action.isExpired {
                return "Expired"
            }
            return action.status.capitalized
        }()

        return Text(label)
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(statusColor.opacity(0.15), in: Capsule())
            .foregroundStyle(statusColor)
    }
}
