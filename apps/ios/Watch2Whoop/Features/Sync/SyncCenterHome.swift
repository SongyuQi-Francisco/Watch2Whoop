import SwiftUI
import UIKit

/// The sync dial (docs/design/2.0.1 `07-me`, `08-sync-center`): one tick per day of the last 30, a missing day in `mid`,
/// days before the first data faint; the newest day just left of 12 o'clock. The content sits in the middle.
struct SyncDaysDial<Center: View>: View {
  let days: SyncDays?
  let side: CGFloat
  @ViewBuilder var center: () -> Center

  var body: some View {
    let marks = days?.marks ?? Array(repeating: SyncDays.Mark.before, count: SyncParams.dialDays)
    ZStack {
      Circle().stroke(Palette.track, lineWidth: 1.5).frame(width: side - 2, height: side - 2)
      Canvas { ctx, size in
        let c = CGPoint(x: size.width / 2, y: size.height / 2)
        let n = max(1, marks.count)
        let outer = side / 2 - side * 0.05, len = side * 0.07
        for (i, m) in marks.enumerated() {
          let a = Angle.degrees(Double(i + 1) / Double(n) * 360 - 90).radians
          var p = Path()
          p.move(to: CGPoint(x: c.x + cos(a) * outer, y: c.y + sin(a) * outer))
          p.addLine(to: CGPoint(x: c.x + cos(a) * (outer - len), y: c.y + sin(a) * (outer - len)))
          let color: Color = m == .missing ? Palette.mid : (m == .ok ? Palette.tickMajor : Palette.track)
          ctx.stroke(p, with: .color(color), style: StrokeStyle(lineWidth: side < 240 ? 2 : 2.5, lineCap: .round))
        }
      }
      .frame(width: side, height: side)
      center()
    }
    .frame(width: side, height: side)
  }
}

/// The type lanes of the sync center: one row per core kind, one dot per day of the last 14 (filled = on the server,
/// ring in `mid` = missing, tap = re-sync that day; faint = before the first data), three dates under them.
struct SyncLanes: View {
  let days: SyncDays
  let resync: (String) -> Void

  var body: some View {
    let dates = days.laneDays
    VStack(spacing: 0) {
      ForEach(days.lanes) { lane in
        HStack(spacing: 14) {
          Text(lane.label).font(Typo.label).foregroundColor(Palette.text2).lineLimit(1).minimumScaleFactor(0.7)
            .frame(width: 50, alignment: .leading)
          HStack(spacing: 0) {
            ForEach(Array(lane.marks.enumerated()), id: \.offset) { i, m in
              if i > 0 { Spacer(minLength: 0) }
              dot(m, day: i < dates.count ? dates[i] : nil, label: lane.label)
            }
          }
        }
        .frame(height: 30)
      }
      HStack(spacing: 0) {
        ForEach(Array(BodyGlanceRules.dateLabels(dates).enumerated()), id: \.offset) { i, d in
          if i > 0 { Spacer(minLength: 4) }
          Text(d)
        }
      }
      .font(Typo.axis).foregroundColor(Palette.text2)
      .padding(.leading, 64).padding(.top, 4)
    }
  }

  private func missingLabel(_ kind: String, _ day: String) -> String {
    tr("\(kind) \(day) 缺，重新同步", "\(kind) \(day) missing, re-sync")
  }

  @ViewBuilder private func dot(_ m: SyncDays.Mark, day: String?, label: String) -> some View {
    switch m {
    case .ok:
      Circle().fill(Palette.text2).frame(width: 9, height: 9).frame(width: 14, height: 14).accessibilityHidden(true)
    case .before:
      Circle().fill(Palette.past).frame(width: 3, height: 3).frame(width: 14, height: 14).accessibilityHidden(true)
    case .missing:
      Button { if let day { resync(day) } } label: {
        Circle().stroke(Palette.mid, lineWidth: 1.75).frame(width: 9, height: 9)
          .frame(width: 18, height: 30).contentShape(Rectangle())
      }
      .buttonStyle(.plain)
      .accessibilityLabel(missingLabel(label, TodayModel.shortDay(day)))
    }
  }
}

/// The sync center, 2.0.1 (`08-sync-center`): the answer (the sync dial with the missing days), one primary button
/// (开启同步 / 立即同步 / 重试), the one self-help step, then the evidence (four type lanes of 14 days, a missing dot
/// re-syncs that day). 「更多工具」 pushes the full 2.0.0 sync center (`SyncCenterView`: the status and issues in words,
/// sync result, missing-days list, history backfill, local history, re-sync ranges, types, cellular, log).
struct SyncCenterHome: View {
  @EnvironmentObject private var session: AccountSession
  @EnvironmentObject private var app: AppModel
  @ObservedObject private var sync = HealthSyncService.shared
  @State private var missing: MissingDays?
  @State private var busy = false
  @State private var message: String?
  @State private var tools = false
  @State private var resyncDay: String?

  var body: some View {
    let d = sync.diagnosis
    let days = missing.map { SyncDays.of($0, fallbackEnd: LocalDay.add(LocalDay.string(Date(), .current), -1)) }
    ScrollView(.vertical, showsIndicators: false) {
      VStack(spacing: 0) {
        SyncDaysDial(days: days, side: CGFloat(SyncParams.centerDialSide)) { dialCenter(days) }
          .padding(.top, 18)
          .accessibilityElement(children: .combine)
        let p = SyncGlanceRules.primary(permission: sync.permission, issues: d.issues)
        PrimaryButton(title: SyncGlanceRules.title(p), busy: busy || sync.running) { Task { await primary(p) } }
          .padding(.top, 28)
        if let step = SyncGlanceRules.helpStep(issues: d.issues, hints: d.hints) {
          Button { Task { await help(step) } } label: {
            HStack(spacing: 6) {
              Icon(.settings, size: 17).foregroundColor(Palette.mid)
              Text(SyncGlanceRules.shortLabel(step)).font(.system(size: 15, weight: .medium)).foregroundColor(Palette.text)
              Icon(.chevronRight, size: 14).foregroundColor(Palette.text3)
            }
            .frame(maxWidth: .infinity, minHeight: 44)
            .contentShape(Rectangle())
          }
          .buttonStyle(PressScale())
          .padding(.top, 6)
          .accessibilityHint(step.text)
        }
        if let message {
          Text(message).font(Typo.caption).foregroundColor(Palette.text2).multilineTextAlignment(.center).padding(.top, 8)
        }
        if let days, !days.lanes.isEmpty {
          Rectangle().fill(Palette.hair).frame(height: 1).padding(.top, 30)
          SyncLanes(days: days) { resyncDay = $0 }.padding(.top, 6)
        }
        Button { tools = true } label: {
          HStack(spacing: 4) {
            Text(tr("更多工具", "More tools")).font(.system(size: 15, weight: .medium)).foregroundColor(Palette.text2)
            Icon(.chevronRight, size: 14).foregroundColor(Palette.text3)
          }
          .frame(maxWidth: .infinity, minHeight: 44)
          .contentShape(Rectangle())
        }
        .buttonStyle(PressScale())
        .padding(.top, 30)
      }
      .padding(.horizontal, Metrics.gutter)
      .padding(.bottom, 32)
      .frame(maxWidth: Metrics.maxContentWidth)
      .frame(maxWidth: .infinity)
    }
    .background(Palette.bg.ignoresSafeArea())
    .navigationTitle(tr("同步中心", "Sync center"))
    .navigationBarTitleDisplayMode(.inline)
    .navigationDestination(isPresented: $tools) { SyncCenterView() }
    .refreshable { await primary(.syncNow) }
    .task {
      await sync.refreshPermission()
      await sync.reloadDetails(server: true)
      await loadMissing()
      #if DEBUG
      if RecordDevShot.value("W2W_DEV_SYNC_TOOLS") == "1" { tools = true }
      #endif
    }
    .onChange(of: sync.running) { r in if !r { Task { await loadMissing() } } }
    .onChange(of: sync.job == nil) { done in if done { Task { await loadMissing() } } }
    .confirmationDialog(resyncDay.map { tr("重新同步 \(MissingDays.dayLabel($0))？", "Re-sync \(MissingDays.dayLabel($0))?") } ?? "",
                        isPresented: Binding(get: { resyncDay != nil }, set: { if !$0 { resyncDay = nil } }),
                        titleVisibility: .visible) {
      Button(tr("重新同步", "Re-sync")) {
        guard let day = resyncDay else { return }
        resyncDay = nil
        Task {
          busy = true
          message = await sync.startResync(day: day) ?? tr("已开始重新同步", "Re-sync started")
          busy = false
        }
      }
      Button(tr("取消", "Cancel"), role: .cancel) { resyncDay = nil }
    }
  }

  @ViewBuilder private func dialCenter(_ days: SyncDays?) -> some View {
    VStack(spacing: 2) {
      Text(tr("近30天", "30 days")).font(.system(size: 12, weight: .medium)).tracking(1.5).foregroundColor(Palette.text2)
      if let days, days.missing > 0 {
        HStack(alignment: .firstTextBaseline, spacing: 3) {
          Text(tr("缺", "")).font(.system(size: 20, weight: .light))
          Text("\(days.missing)").font(.system(size: 64, weight: .ultraLight).monospacedDigit())
          Text(tr("天", "d missing")).font(.system(size: 20, weight: .light))
        }
        .foregroundColor(Palette.mid)
      } else if let days, days.counted > 0 {
        HStack(alignment: .firstTextBaseline, spacing: 2) {
          Text("\(days.synced)").font(.system(size: 64, weight: .ultraLight).monospacedDigit()).foregroundColor(Palette.text)
          Text("/\(days.counted)").font(.system(size: 20, weight: .light).monospacedDigit()).foregroundColor(Palette.text2)
        }
      } else {
        Text("—").font(.system(size: 64, weight: .ultraLight)).foregroundColor(Palette.text2)
      }
      Text(sync.diagnosis.overall == .ok ? SyncGlanceRules.verdict(days ?? SyncDays(days: [], marks: [], lanes: [])) : shortHeadline)
        .font(Typo.verdict).foregroundColor(Palette.text).lineLimit(1).minimumScaleFactor(0.7)
    }
    .frame(width: CGFloat(SyncParams.centerDialSide) * 0.72)
  }

  /// The phone's state in a word under the number (the full sentence stays on 「更多工具」).
  private var shortHeadline: String {
    switch sync.diagnosis.overall {
    case .ok: return tr("已同步", "Synced")
    case .syncing: return tr("同步中", "Syncing")
    case .processing: return tr("计算中", "Computing")
    case .attention: return tr("需处理", "Check")
    case .off: return tr("未开同步", "Sync off")
    }
  }

  private func loadMissing() async {
    if let j = try? await session.api.json(.get(Endpoint.syncMissingDays)) { missing = MissingDays(j) }
  }

  private func primary(_ p: SyncGlanceRules.Primary) async {
    busy = true
    defer { busy = false }
    if p == .turnOn { _ = await sync.requestPermission() }
    message = await sync.syncNow()
    await loadMissing()
  }

  /// The self-help step: the simple ones right here, the ones with their own dialogs or steps on 「更多工具」.
  private func help(_ step: SyncDiagnosis.Issue) async {
    switch step.action {
    case .openSettings?: if let u = URL(string: UIApplication.openSettingsURLString) { await UIApplication.shared.open(u) }
    case .openHealthApp?: if let u = URL(string: "x-apple-health://") { await UIApplication.shared.open(u) }
    case .allowCellular?: await sync.setCellularHistory(true); await primary(.syncNow)
    case .retryHistory?:
      _ = await HealthSyncBridge.History.pump(seconds: SyncSettings.pumpSeconds)
      await sync.reloadDetails(server: false)
    default: tools = true
    }
  }
}
