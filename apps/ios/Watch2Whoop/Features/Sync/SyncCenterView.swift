import SwiftUI
import UIKit

/// The sync center (sync is P0): one button to turn sync on / sync now, the overall state in a word, where it is stuck in
/// plain words with one self-help button each, the first sync's progress, the history backfill (90 days / all, continues
/// in the background), 「立即重新同步」, and per type "not recorded / not synced". Times, counts and codes only.
struct SyncCenterView: View {
  /// Opened from `watch2whoop://backfill?days=…`: start that history backfill right away (0 = all).
  var startHistory: Int?
  @EnvironmentObject private var session: AccountSession
  @EnvironmentObject private var app: AppModel
  @ObservedObject private var sync = HealthSyncService.shared
  @State private var busy = false
  @State private var message: String?
  @State private var showConsent = false
  @State private var showTypes = false
  @State private var showLog = false
  @State private var confirmResync: Int?? = nil
  @State private var tick = Timer.publish(every: 5, on: .main, in: .common).autoconnect()

  var body: some View {
    let d = sync.diagnosis
    ScrollView(.vertical, showsIndicators: false) {
      VStack(alignment: .leading, spacing: 18) {
        statusCard(d)
        if !d.issues.isEmpty {
          VStack(spacing: 8) {
            ForEach(d.issues) { i in
              Banner(text: i.text, tone: d.overall == .attention ? .mid : .text, actionTitle: i.actionTitle,
                     action: i.action.map { a in { Task { await perform(a) } } })
            }
          }
        }
        if let message { Banner(text: message, tone: .text) }
        if sync.engine.active && !sync.engine.firstSyncDone { firstSync }
        history
        LocalHistoryCard(sync: session.sync)
        resync
        types
        settings
        log
      }
      .padding(.horizontal, Metrics.gutter)
      .padding(.top, 8)
      .padding(.bottom, 32)
      .frame(maxWidth: Metrics.maxContentWidth)
      .frame(maxWidth: .infinity)
    }
    .background(Palette.bg.ignoresSafeArea())
    .navigationTitle(tr("同步中心", "Sync center"))
    .navigationBarTitleDisplayMode(.inline)
    .refreshable { await sync.syncNow().map { message = $0 } }
    .task {
      await sync.refreshPermission()
      await sync.reloadDetails(server: true)
      if let days = startHistory { message = await sync.startHistory(days: days) ?? tr("已开始补全历史，会在后台接着传", "History started; it carries on in the background") }
    }
    .onReceive(tick) { _ in
      Task {
        if sync.job != nil { await sync.stepJob() }
        await sync.reloadDetails(server: false)
      }
    }
    .sheet(isPresented: $showConsent) {
      ConsentView(reconfirm: false) { showConsent = false; sync.consentRequired = false }
        .environmentObject(session).environmentObject(app)
    }
    .confirmationDialog(resyncTitle, isPresented: Binding(get: { confirmResync != nil }, set: { if !$0 { confirmResync = nil } }),
                        titleVisibility: .visible) {
      Button(tr("开始", "Start")) {
        let days = confirmResync ?? nil
        confirmResync = nil
        Task { busy = true; message = await sync.startResync(days: days) ?? tr("已开始重新同步", "Re-sync started"); busy = false }
      }
      Button(tr("取消", "Cancel"), role: .cancel) { confirmResync = nil }
    } message: {
      Text((confirmResync ?? nil) == nil
           ? tr("从「健康」里最早的一条开始全部重新上传，数据多的话要很久，比较耗电；中断了会接着传，服务器之后重算全部分数。", "Uploads everything again from the oldest record in Health. With a lot of data this takes long and uses battery; it resumes if interrupted, then the server recomputes all scores.")
           : tr("把这段时间手机上的数据重新上传一遍，服务器再重新导入并重算分数。", "Uploads this period from the phone again; the server then re-imports it and recomputes the scores."))
    }
  }

  // MARK: - parts

  private func statusCard(_ d: SyncDiagnosis) -> some View {
    Card {
      VStack(alignment: .leading, spacing: 14) {
        HStack(spacing: 12) {
          Group {
            switch d.overall {
            case .syncing: ProgressView().tint(Palette.text2)
            case .ok: Icon(.circleCheck, size: 26).foregroundColor(Palette.good)
            case .attention: Icon(.circleAlert, size: 26).foregroundColor(Palette.mid)
            case .off: Icon(.circleAlert, size: 26).foregroundColor(Palette.text3)
            }
          }
          .frame(width: 28, height: 28)
          VStack(alignment: .leading, spacing: 2) {
            Text(d.headline).font(Typo.heroWord).foregroundColor(Palette.text)
            Text(tr("上次后台同步 \(SyncDiagnosis.ago(sync.engine.lastBackgroundAt))", "Last background sync \(SyncDiagnosis.ago(sync.engine.lastBackgroundAt))"))
              .font(Typo.caption).foregroundColor(Palette.text2)
          }
        }
        PrimaryButton(title: sync.permission == .notAsked ? tr("开启同步", "Turn on sync") : tr("立即同步", "Sync now"),
                      busy: busy || sync.running) { Task { await syncNow() } }
      }
    }
  }

  private var firstSync: some View {
    RowGroup(title: tr("首次同步（近 14 天）", "First sync (last 14 days)")) {
      VStack(alignment: .leading, spacing: 8) {
        HStack {
          Text(tr("\(sync.engine.firstDone)/\(sync.engine.firstTypes) 类", "\(sync.engine.firstDone)/\(sync.engine.firstTypes) types"))
            .font(Typo.body).foregroundColor(Palette.text)
          Spacer()
          Text("\(sync.engine.firstPercent)%").font(Typo.body.monospacedDigit()).foregroundColor(Palette.text2)
        }
        ProgressBar(fraction: Double(sync.engine.firstPercent) / 100)
      }
      .padding(.vertical, 12)
    }
  }

  private var history: some View {
    let b = sync.backfill
    return RowGroup(title: tr("补全历史", "Fill in history")) {
      VStack(alignment: .leading, spacing: 10) {
        if b.active || b.done {
          HStack {
            Text(b.target == 0 ? tr("全部历史", "All history") : tr("近 \(b.target) 天", "Last \(b.target) days"))
              .font(Typo.body).foregroundColor(Palette.text)
            Spacer()
            Text(b.done ? tr("完成", "Done") : "\(b.percent)%").font(Typo.body.monospacedDigit())
              .foregroundColor(b.done ? Palette.good : Palette.text2)
          }
          if !b.done { ProgressBar(fraction: Double(b.percent) / 100) }
          if b.active {
            Text(tr("在后台接着传，关掉 app 也会继续；充电并连 Wi-Fi 时最快。", "Carries on in the background, even with the app closed; fastest while charging on Wi-Fi."))
              .font(Typo.caption).foregroundColor(Palette.text2)
          }
        } else {
          Text(tr("首次只同步近 14 天。补上更早的数据，趋势和基线更准。", "The first sync reads the last 14 days. Older data makes trends and baselines more accurate."))
            .font(Typo.caption).foregroundColor(Palette.text2)
        }
        if !b.active && (b.target != 0 || !b.done) {
          HStack(spacing: 10) {
            if !(b.done && b.target >= SyncSettings.backfillDays) {
              historyButton(tr("近 90 天", "90 days"), days: SyncSettings.backfillDays)
            }
            historyButton(tr("全部历史", "All history"), days: 0)
          }
        }
      }
      .padding(.vertical, 12)
    }
  }

  private func historyButton(_ title: String, days: Int) -> some View {
    SecondaryButton(icon: .calendarClock, title: title, background: Palette.surface2) {
      Task {
        busy = true
        message = await sync.startHistory(days: days) ?? tr("已开始补全历史，会在后台接着传", "History started; it carries on in the background")
        busy = false
      }
    }
    .disabled(busy)
  }

  private var resyncTitle: String {
    guard let c = confirmResync else { return "" }
    return c == nil ? tr("重新同步全部历史？", "Re-sync all history?") : tr("重新同步近 \(c!) 天？", "Re-sync the last \(c!) days?")
  }

  private var resync: some View {
    RowGroup(title: tr("立即重新同步", "Re-sync now")) {
      VStack(alignment: .leading, spacing: 10) {
        if let j = sync.job, !j.history {
          HStack {
            Text(j.days == nil ? tr("全部历史", "All history") : tr("近 \(j.days!) 天", "Last \(j.days!) days"))
              .font(Typo.body).foregroundColor(Palette.text)
            Spacer()
            Text("\(sync.jobPercent)%").font(Typo.body.monospacedDigit()).foregroundColor(Palette.text2)
          }
          ProgressBar(fraction: Double(sync.jobPercent) / 100)
          Button(tr("停止", "Stop")) { Task { await sync.cancelJob() } }.font(Typo.caption).foregroundColor(Palette.bad)
        } else {
          Text(tr("某几天的数据不对或缺了：把这段时间重新上传，服务器再重算。", "Some days look wrong or incomplete: upload that period again and the server recomputes."))
            .font(Typo.caption).foregroundColor(Palette.text2)
          HStack(spacing: 8) {
            ForEach([7, 30, 90], id: \.self) { d in
              rangeButton(tr("\(d) 天", "\(d) days")) { confirmResync = .some(d) }
            }
            rangeButton(tr("全部", "All")) { confirmResync = .some(nil) }
          }
        }
      }
      .padding(.vertical, 12)
    }
  }

  private func rangeButton(_ title: String, _ action: @escaping () -> Void) -> some View {
    Button(action: action) {
      Text(title).font(.system(size: 13, weight: .medium)).foregroundColor(Palette.text)
        .padding(.vertical, 8).padding(.horizontal, 14)
        .background(Capsule().fill(Palette.surface2))
    }
    .buttonStyle(PressScale())
    .disabled(busy || sync.job != nil)
  }

  private var types: some View {
    let rows = sync.rows.filter { $0.phone != nil || $0.server != nil || showTypes }
    let behind = rows.filter { $0.verdict == .notSynced }.count
    return RowGroup(title: behind > 0 ? tr("手机上 / 服务器上（\(behind) 类没同步）", "Phone / server (\(behind) not synced)")
                    : tr("手机上 / 服务器上", "Phone / server")) {
      if sync.server == nil {
        Text(tr("读取中…", "Loading…")).font(Typo.caption).foregroundColor(Palette.text2).padding(.vertical, 12)
      } else if rows.isEmpty {
        Text(tr("手机上还没有这些数据", "No data on the phone yet")).font(Typo.caption).foregroundColor(Palette.text2).padding(.vertical, 12)
      } else {
        ForEach(Array(rows.prefix(showTypes ? rows.count : 8).enumerated()), id: \.element.id) { i, r in
          if i > 0 { RowDivider(inset: 0) }
          HStack(spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
              Text(r.type.label).font(Typo.body).foregroundColor(Palette.text)
              Text("\(Self.day(r.phone)) / \(Self.day(r.server))").font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
            }
            Spacer()
            VerdictTag(verdict: r.verdict)
          }
          .padding(.vertical, 10)
        }
        if rows.count > 8 || !showTypes {
          Button(showTypes ? tr("收起", "Show less") : tr("全部类型", "All types")) { showTypes.toggle() }
            .font(Typo.caption).foregroundColor(Palette.text2).padding(.vertical, 10)
        }
      }
    }
  }

  private var settings: some View {
    RowGroup {
      ToggleRow(title: tr("用蜂窝数据补历史", "Use cellular data for history"),
                subtitle: tr("新数据任何网络都传；首次同步和历史默认只用 Wi-Fi", "New data goes on any network; history uses Wi-Fi unless allowed"),
                isOn: Binding(get: { sync.engine.cellularHistory }, set: { on in Task { await sync.setCellularHistory(on) } }))
    }
  }

  private var log: some View {
    DisclosureGroup(isExpanded: $showLog) {
      Text(sync.engine.log.suffix(60).reversed().map(Self.logLine).joined(separator: "\n"))
        .font(.system(size: 11, design: .monospaced)).foregroundColor(Palette.text2)
        .frame(maxWidth: .infinity, alignment: .leading)
        .textSelection(.enabled)
        .padding(.top, 8)
    } label: {
      Text(tr("同步日志", "Sync log")).font(Typo.section).foregroundColor(Palette.text2)
    }
    .tint(Palette.text2)
  }

  // MARK: - actions

  private func syncNow() async {
    busy = true
    defer { busy = false }
    if sync.permission == .notAsked { await sync.requestPermission() }
    message = await sync.syncNow()
  }

  private func perform(_ a: SyncDiagnosis.Action) async {
    switch a {
    case .requestPermission: await sync.requestPermission(); await syncNow()
    case .openHealthApp: if let u = URL(string: "x-apple-health://") { await UIApplication.shared.open(u) }
    case .openSettings: if let u = URL(string: UIApplication.openSettingsURLString) { await UIApplication.shared.open(u) }
    case .syncNow, .retry: await syncNow()
    case .signIn: await app.signOut()
    case .consent: showConsent = true
    case .allowCellular: await sync.setCellularHistory(true); await syncNow()
    case .retryHistory: _ = await HealthSyncBridge.History.pump(seconds: SyncSettings.pumpSeconds); await sync.reloadDetails(server: false)
    }
  }

  // MARK: - formatting

  private static let dayFormat: DateFormatter = {
    let f = DateFormatter()
    f.setLocalizedDateFormatFromTemplate("MMMd HH:mm")
    return f
  }()

  static func day(_ d: Date?) -> String { d.map { dayFormat.string(from: $0) } ?? "—" }

  /// "ISO time\tline" of the engine log → "10/29 09:12 line".
  static func logLine(_ l: String) -> String {
    let parts = l.split(separator: "\t", maxSplits: 1).map(String.init)
    guard parts.count == 2, let at = ISO8601.parse(parts[0]) else { return l }
    return "\(dayFormat.string(from: at)) \(parts[1])"
  }
}

/// 没同步 / 已同步 / 没采集.
struct VerdictTag: View {
  let verdict: TypeVerdict

  var body: some View {
    let (label, tone): (String, Tone) = {
      switch verdict {
      case .notSynced: return (tr("没同步", "Not synced"), .mid)
      case .synced: return (tr("已同步", "Synced"), .good)
      case .notCollected: return (tr("没采集", "Not recorded"), .text)
      }
    }()
    Text(label).font(.system(size: 12, weight: .medium)).foregroundColor(tone == .text ? Palette.text3 : tone.color)
      .padding(.vertical, 4).padding(.horizontal, 9)
      .background(Capsule().fill(tone == .good ? Palette.goodSoft : tone == .mid ? Palette.midSoft : Palette.surface2))
  }
}
