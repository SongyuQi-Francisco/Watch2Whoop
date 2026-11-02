import SwiftUI
import UniformTypeIdentifiers

// 从 Hevy 导入 (flag hevy_import, training/hevy_import.py, `hevy-import.js` on the page): upload the CSV Hevy exports, see
// what came in, match the exercise names the library did not know. The server's import is idempotent.

/// GET /v1/users/me/training/import/hevy.
struct HevyStatus: Decodable, Equatable {
  var last: Summary?
  var unmatched: [Unmatched] = []
  var csvBytes: Int?
  var uploadBytes: Int?

  struct Summary: Decodable, Equatable {
    var workouts = 0
    var sets = 0
    var unmatched: [Unmatched] = []
    var skippedRows = 0
    var start: String?
    var end: String?

    enum CodingKeys: String, CodingKey { case workouts, sets, unmatched, start, end, skippedRows = "skipped_rows" }

    init(from d: Decoder) throws {
      let c = try d.container(keyedBy: CodingKeys.self)
      workouts = try c.decodeIfPresent(Int.self, forKey: .workouts) ?? 0
      sets = try c.decodeIfPresent(Int.self, forKey: .sets) ?? 0
      unmatched = try c.decodeIfPresent([Unmatched].self, forKey: .unmatched) ?? []
      skippedRows = try c.decodeIfPresent(Int.self, forKey: .skippedRows) ?? 0
      start = try c.decodeIfPresent(String.self, forKey: .start)
      end = try c.decodeIfPresent(String.self, forKey: .end)
    }

    var days: String? {
      guard let start else { return nil }
      return start == end || end == nil ? start : "\(start) – \(end ?? "")"
    }
  }

  struct Unmatched: Decodable, Equatable, Identifiable {
    let name: String
    var sets = 0
    var id: String { name }
    enum CodingKeys: String, CodingKey { case name, sets }
    init(from d: Decoder) throws {
      let c = try d.container(keyedBy: CodingKeys.self)
      name = try c.decode(String.self, forKey: .name)
      sets = try c.decodeIfPresent(Int.self, forKey: .sets) ?? 0
    }
  }

  enum CodingKeys: String, CodingKey { case last, unmatched, limits }
  private enum LimitKeys: String, CodingKey { case csv = "csv_bytes", upload = "upload_bytes" }

  init() {}

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    last = try c.decodeIfPresent(Summary.self, forKey: .last)
    unmatched = try c.decodeIfPresent([Unmatched].self, forKey: .unmatched) ?? []
    if let l = try? c.nestedContainer(keyedBy: LimitKeys.self, forKey: .limits) {
      csvBytes = try l.decodeIfPresent(Int.self, forKey: .csv)
      uploadBytes = try l.decodeIfPresent(Int.self, forKey: .upload)
    }
  }
}

/// An exercise of the library (`/training/exercises`), to match an unknown name to.
struct HevyLibraryExercise: Decodable, Equatable, Identifiable {
  let slug: String
  var nameZh: String?
  var nameEn: String?
  var id: String { slug }
  var name: String { Lang.isZh ? (nameZh ?? nameEn ?? slug) : (nameEn ?? nameZh ?? slug) }
  enum CodingKeys: String, CodingKey { case slug, nameZh = "name_zh", nameEn = "name_en" }
}

struct HevyImportView: View {
  @EnvironmentObject private var session: AccountSession
  @State private var status: HevyStatus?
  @State private var library: [HevyLibraryExercise] = []
  @State private var picking = false
  @State private var busy = false
  @State private var note: String?
  @State private var message: String?

  private static let path = "/v1/users/me/training/import/hevy"

  var body: some View {
    VStack(alignment: .leading, spacing: 18) {
      VStack(alignment: .leading, spacing: 4) {
        Text(tr("1. Hevy：设置 → 导出数据 → 导出训练", "1. In Hevy: Settings → Export data → Export workouts")).font(Typo.caption).foregroundColor(Palette.text2)
        Text(tr("2. 选择导出的 CSV", "2. Choose the exported CSV")).font(Typo.caption).foregroundColor(Palette.text2)
      }
      PrimaryButton(title: tr("选择 CSV", "Choose the CSV"), busy: busy) { picking = true }
      if let note { Banner(text: note, tone: .good) }
      if let message { Banner(text: message, tone: .bad) }
      if let l = status?.last {
        RowGroup(title: tr("上次导入", "Last import")) {
          ListRow(title: tr("训练次数", "Workouts"), value: "\(l.workouts)")
          RowDivider(inset: 0)
          ListRow(title: tr("组数", "Sets"), value: "\(l.sets)")
          RowDivider(inset: 0)
          ListRow(title: tr("未对上的动作", "Unmatched exercises"), value: "\(l.unmatched.count)")
          if l.skippedRows > 0 {
            RowDivider(inset: 0)
            ListRow(title: tr("跳过的行", "Skipped rows"), value: "\(l.skippedRows)")
          }
          if let d = l.days {
            RowDivider(inset: 0)
            ListRow(title: tr("日期", "Dates"), value: d)
          }
        }
      }
      if let un = status?.unmatched, !un.isEmpty {
        RowGroup(title: tr("对应到动作库", "Match to the library")) {
          ForEach(Array(un.enumerated()), id: \.element.id) { i, u in
            if i > 0 { RowDivider(inset: 0) }
            HStack {
              VStack(alignment: .leading, spacing: 2) {
                Text(u.name).font(Typo.body).foregroundColor(Palette.text)
                Text(tr("\(u.sets) 组", "\(u.sets) sets")).font(Typo.caption).foregroundColor(Palette.text2)
              }
              Spacer()
              Menu {
                ForEach(library) { x in Button(x.name) { Task { await map(u, to: x) } } }
              } label: {
                Text(tr("选择", "Choose")).font(Typo.caption).foregroundColor(Palette.text)
              }
            }
            .padding(.vertical, 10)
          }
        }
      }
    }
    .task { await load() }
    .fileImporter(isPresented: $picking, allowedContentTypes: [.commaSeparatedText, .plainText]) { result in
      if case .success(let url) = result { Task { await upload(url) } }
    }
  }

  private func load() async {
    do {
      let s = try await session.api.send(.get(Self.path), as: HevyStatus.self)
      status = s
      if !s.unmatched.isEmpty && library.isEmpty { await loadLibrary() }
    } catch APIError.notFound {
      message = tr("这个功能还没有对你开放", "This feature is not available to you yet")
    } catch let e as APIError {
      message = e.userMessage
    } catch {}
  }

  private func loadLibrary() async {
    struct Answer: Decodable { var exercises: [HevyLibraryExercise] = [] }
    let a = try? await session.api.send(.get("/v1/users/me/training/exercises", ["limit": "200"]), as: Answer.self)
    // The catalog only: a `custom:` row is a name from the person's own log, there is nothing to match it to.
    library = (a?.exercises ?? []).filter { !$0.slug.hasPrefix("custom:") }.sorted { $0.name < $1.name }
  }

  private func upload(_ url: URL) async {
    busy = true
    note = nil
    message = nil
    defer { busy = false }
    let scoped = url.startAccessingSecurityScopedResource()
    defer { if scoped { url.stopAccessingSecurityScopedResource() } }
    do {
      let csv = try Data(contentsOf: url)
      let tooBig = tr("文件太大", "The file is too large")
      if let limit = status?.csvBytes, csv.count > limit { message = tooBig; return }
      // the public server takes 2 MB per request and a CSV shrinks about ten times (the page gzips it too)
      guard let gz = Gzip.compress(csv) else { message = tr("读不了这个文件", "Can't read this file"); return }
      if let limit = status?.uploadBytes, gz.count > limit { message = tooBig; return }
      var r = APIRequest(method: .post, path: Self.path, body: gz, idempotent: false, timeout: 120)
      r.contentType = "application/gzip"
      let j = try await session.api.json(r)
      let w = j["workouts"] as? Int ?? 0, s = j["sets"] as? Int ?? 0, u = (j["unmatched"] as? [Any])?.count ?? 0
      note = tr("导入完成：\(w) 次训练 · \(s) 组 · \(u) 个动作没对上", "Imported \(w) workouts · \(s) sets · \(u) exercises unmatched")
      await load()
      await session.sync.refresh(.changes)
    } catch let e as APIError {
      message = e.userMessage
    } catch {
      message = tr("读不了这个文件", "Can't read this file")
    }
  }

  private func map(_ u: HevyStatus.Unmatched, to x: HevyLibraryExercise) async {
    struct Body: Encodable { let name: String; let exercise: String }
    do {
      _ = try await session.api.data(try .json(.put, Self.path + "/map", Body(name: u.name, exercise: x.slug)))
      await load()
    } catch let e as APIError {
      message = e.userMessage
    } catch {}
  }
}
