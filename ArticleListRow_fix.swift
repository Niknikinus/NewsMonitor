// Add this button to ArticleListRow in CenterPanelView.swift
// Place in the HStack after source name and date

// In ArticleListRow body, replace the outer VStack with:

struct ArticleListRow: View {
    let article: Article
    let isSelected: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(article.title)
                .font(.system(size: 12, weight: .medium)).lineLimit(2)
                .textSelection(.enabled)

            HStack(spacing: 6) {
                Text(article.sourceName)
                    .font(.system(size: 10)).foregroundStyle(.tertiary)
                if let pub = article.publishedAt {
                    Text("·").font(.system(size: 10)).foregroundStyle(.tertiary)
                    Text(pub.relativeString)
                        .font(.system(size: 10)).foregroundStyle(.tertiary)
                }
                if !article.keyAngle.isEmpty {
                    Text("·").font(.system(size: 10)).foregroundStyle(.tertiary)
                    Text(article.keyAngle)
                        .font(.system(size: 10, weight: .medium)).foregroundStyle(.secondary).lineLimit(1)
                }

                Spacer()

                // Copy link button
                Button {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(article.url, forType: .string)
                } label: {
                    Image(systemName: "link")
                        .font(.system(size: 10))
                        .foregroundStyle(.tertiary)
                }
                .buttonStyle(.plain)
                .help("Скопировать ссылку")

                // Open in browser
                Button {
                    if let url = URL(string: article.url) {
                        NSWorkspace.shared.open(url)
                    }
                } label: {
                    Image(systemName: "arrow.up.right.square")
                        .font(.system(size: 10))
                        .foregroundStyle(.tertiary)
                }
                .buttonStyle(.plain)
                .help("Открыть в браузере")
            }

            if !article.summary.isEmpty {
                Text(article.summary)
                    .font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(2).lineSpacing(1)
                    .textSelection(.enabled)
            }
        }
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 8)
            .fill(isSelected ? Color.accentColor.opacity(0.1) : Color.clear))
        .overlay(RoundedRectangle(cornerRadius: 8)
            .strokeBorder(isSelected ? Color.accentColor.opacity(0.3) : Color.clear, lineWidth: 1))
    }
}
