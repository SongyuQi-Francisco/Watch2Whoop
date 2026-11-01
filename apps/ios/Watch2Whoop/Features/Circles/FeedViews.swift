import SwiftUI
import UIKit

/// The training feed: one post per person per day, Hevy / Instagram style (circle-feed.js): header, title, stats row,
/// per watch workout its heart rate / distance / load and zone bar, the first exercises (tap for the sets), then the
/// heart (bounces and fills, double-tap the post to like it with a big heart), "N likes", the newest comments and
/// "view all N comments". Newest first, pull to refresh, more when the end comes near.
struct FeedList: View {
  @ObservedObject var store: CirclesStore
  @State private var commentsFor: Int?
  let scrollTo: (Int) -> Void

  var body: some View {
    let posts = store.posts
    LazyVStack(spacing: 12) {
      if posts.isEmpty {
        EmptyFeed(loaded: !store.feed.posts.isEmpty || store.list != nil, error: store.loadError)
      }
      ForEach(posts) { p in
        PostCard(post: p, store: store, preview: store.feed.previewExercises) { commentsFor = p.id }
          .id(p.id)
          .onAppear {
            // the next page while a few posts are still below (no "load more" button)
            if let i = posts.firstIndex(where: { $0.id == p.id }), i >= posts.count - 3 { Task { await store.loadMore() } }
          }
      }
      if store.loadingMore {
        ProgressView().tint(Palette.text2).padding(.vertical, 16)
      } else if store.feed.next == nil && posts.count > 3 {
        Text(tr("没有更早的了", "That's everything")).font(Typo.caption).foregroundColor(Palette.text3).padding(.vertical, 16)
      }
    }
    .sheet(item: Binding(get: { commentsFor.map(PostRef.init) }, set: { commentsFor = $0?.id })) { ref in
      CommentsSheet(store: store, postId: ref.id)
        .environmentObject(store.session)
    }
    .onAppear { focus() }
    .onChange(of: store.focusPost) { _ in focus() }
    .onChange(of: store.feed.posts.count) { _ in focus() }   // a notification's post arrived with the refresh
  }

  /// A notification's post: scroll there once it is laid out, and open its comments.
  private func focus() {
    guard let id = store.focusPost, store.posts.contains(where: { $0.id == id }) else { return }
    store.focusPost = nil
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) {
      scrollTo(id)
      commentsFor = id
    }
  }
}

private struct PostRef: Identifiable { let id: Int }

private struct EmptyFeed: View {
  let loaded: Bool
  let error: String?

  var body: some View {
    VStack(spacing: 10) {
      Icon(.dumbbell, size: 30).foregroundColor(Palette.text3)
      Text(error ?? (loaded ? tr("还没有训练动态", "No workouts yet") : tr("加载中…", "Loading…")))
        .font(Typo.bodyStrong).foregroundColor(Palette.text)
      if loaded && error == nil {
        Text(tr("给圈子勾选「训练」共享，练完就会出现在这里", "Share Training with a circle and workouts show up here"))
          .font(Typo.caption).foregroundColor(Palette.text2).multilineTextAlignment(.center)
      }
    }
    .frame(maxWidth: .infinity)
    .padding(.vertical, 40)
  }
}

struct PostCard: View {
  let post: Post
  @ObservedObject var store: CirclesStore
  var preview = 3
  let openComments: () -> Void
  @State private var showAll = false
  @State private var openSets: Set<Int> = []
  @State private var burst = false
  @State private var bounce = false

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      header
      VStack(alignment: .leading, spacing: 0) {
        Text(post.title).font(.system(size: 18, weight: .bold)).foregroundColor(Palette.text)
          .padding(.top, 12).padding(.bottom, 10)
          .fixedSize(horizontal: false, vertical: true)
        stats
        ForEach(Array(post.activities.enumerated()), id: \.offset) { _, a in
          ActivityLine(activity: a, many: post.activities.count > 1)
        }
        if post.noExercises {
          HStack(spacing: 8) {
            Icon(.dumbbell, size: 18)
            Text(tr("没有记录动作", "No exercises logged")).font(Typo.caption)
          }
          .foregroundColor(Palette.text2).frame(minHeight: 44)
        }
        exercises
      }
      .contentShape(Rectangle())
      .onTapGesture(count: 2) { doubleTap() }
      actions
    }
    .padding(.horizontal, 14).padding(.top, 14).padding(.bottom, 8)
    .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(Palette.surface))
    .overlay(burstHeart)
  }

  // MARK: parts

  private var header: some View {
    HStack(spacing: 10) {
      AvatarView(url: post.user.avatar, name: post.user.name, size: 38)
      VStack(alignment: .leading, spacing: 1) {
        Text(post.user.isSelf ? tr("我", "Me") : post.user.shownName)
          .font(.system(size: 15, weight: .semibold)).foregroundColor(Palette.text).lineLimit(1)
        Text(([FeedFormat.ago(post)] + post.circleRefs.map(\.name)).joined(separator: " · "))
          .font(.system(size: 12.5)).foregroundColor(Palette.text2).lineLimit(1)
      }
      Spacer(minLength: 0)
      if !post.user.isSelf && post.id > 0 {
        ModerationMenu(store: store, kind: .post, target: post.id, user: post.user.id)
      }
    }
  }

  @ViewBuilder private var stats: some View {
    let cells: [(String, String, String?)] = [
      post.durationMin.map { (tr("时长", "Time"), FeedFormat.duration($0), nil) },
      post.activeKcal.map { (tr("消耗", "Energy"), FeedFormat.number($0), "kcal") },
      post.volumeKg > 0 ? (tr("训练量", "Volume"), FeedFormat.number(post.volumeKg), "kg") : nil,
      post.setCount > 0 ? (tr("组数", "Sets"), "\(post.setCount)", nil) : nil,
    ].compactMap { $0 }
    if !cells.isEmpty || post.prCount > 0 {
      HStack(alignment: .top, spacing: 18) {
        ForEach(Array(cells.enumerated()), id: \.offset) { _, c in
          VStack(alignment: .leading, spacing: 2) {
            Text(c.0).font(.system(size: 12)).foregroundColor(Palette.text2)
            HStack(alignment: .firstTextBaseline, spacing: 2) {
              Text(c.1).font(.system(size: 17, weight: .semibold).monospacedDigit()).foregroundColor(Palette.text)
              if let u = c.2 { Text(u).font(.system(size: 12, weight: .medium)).foregroundColor(Palette.text2) }
            }
          }
        }
        if post.prCount > 0 {
          VStack(alignment: .leading, spacing: 2) {
            Text(tr("纪录", "Records")).font(.system(size: 12)).foregroundColor(Palette.text2)
            HStack(spacing: 3) {
              Icon(.trophy, size: 15).foregroundColor(Palette.mid)
              Text("\(post.prCount)").font(.system(size: 17, weight: .semibold).monospacedDigit()).foregroundColor(Palette.text)
            }
          }
        }
        Spacer(minLength: 0)
      }
      .padding(.bottom, 12)
    }
  }

  @ViewBuilder private var exercises: some View {
    let shown = showAll ? post.exercises : Array(post.exercises.prefix(preview))
    if !shown.isEmpty {
      VStack(spacing: 0) {
        Divider().overlay(Palette.hair)
        ForEach(Array(shown.enumerated()), id: \.offset) { i, e in
          Button {
            withAnimation(.easeOut(duration: 0.18)) { if openSets.contains(i) { openSets.remove(i) } else { openSets.insert(i) } }
          } label: {
            HStack(spacing: 12) {
              ExerciseThumb(exercise: e)
              (Text(e.sets.count == 1 ? tr("1 组  ", "1 set  ") : tr("\(e.sets.count) 组  ", "\(e.sets.count) sets  "))
                .foregroundColor(Palette.text2) + Text(e.name).foregroundColor(Palette.text))
                .font(.system(size: 15)).multilineTextAlignment(.leading)
              Spacer(minLength: 4)
              if e.pr {
                Icon(.trophy, size: 15).foregroundColor(Palette.mid)
                  .padding(.horizontal, 7).padding(.vertical, 3)
                  .background(RoundedRectangle(cornerRadius: 8).fill(Palette.midSoft))
                  .accessibilityLabel(tr("个人纪录", "Personal record"))
              }
              Icon(.chevronDown, size: 16).foregroundColor(Palette.text3)
                .rotationEffect(.degrees(openSets.contains(i) ? 180 : 0))
            }
            .frame(minHeight: 56)
            .contentShape(Rectangle())
          }
          .buttonStyle(.plain)
          if openSets.contains(i) {
            VStack(alignment: .leading, spacing: 4) {
              ForEach(Array(e.sets.enumerated()), id: \.offset) { k, s in
                HStack(spacing: 14) {
                  Text("\(k + 1)").foregroundColor(Palette.text2).frame(width: 18, alignment: .trailing)
                  Text(FeedFormat.set(s)).foregroundColor(Palette.text)
                }
                .font(.system(size: 14).monospacedDigit())
              }
            }
            .padding(.leading, 56).padding(.bottom, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
          }
        }
        if post.exercises.count > preview {
          Divider().overlay(Palette.hair)
          Button {
            withAnimation(.easeOut(duration: 0.2)) { showAll.toggle() }
          } label: {
            Text(showAll ? tr("收起", "Show less") : tr("查看另外 \(post.exercises.count - preview) 个动作", "\(post.exercises.count - preview) more exercises"))
              .font(Typo.caption).foregroundColor(Palette.text2)
              .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
          }
          .buttonStyle(.plain)
        }
      }
    }
  }

  private var actions: some View {
    VStack(alignment: .leading, spacing: 2) {
      HStack(spacing: 2) {
        Button { toggleLike() } label: {
          Icon(post.likes.mine ? .heartFill : .heart, size: 26)
            .foregroundColor(post.likes.mine ? Self.likeRed : Palette.text)
            .scaleEffect(bounce ? 1.3 : 1)
            .frame(width: 44, height: 44)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(post.likes.mine ? tr("取消点赞", "Unlike") : tr("点赞", "Like"))
        Button(action: openComments) {
          Icon(.messageCircle, size: 25).foregroundColor(Palette.text).frame(width: 44, height: 44)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(tr("评论", "Comments"))
        Spacer()
      }
      .padding(.horizontal, -8)
      if post.likes.count > 0 {
        Text(post.likes.count == 1 ? tr("1 次赞", "1 like") : tr("\(post.likes.count) 次赞", "\(post.likes.count) likes"))
          .font(.system(size: 14, weight: .semibold).monospacedDigit()).foregroundColor(Palette.text)
      }
      ForEach(post.previewComments) { c in
        (Text(c.user.isSelf ? tr("我", "Me") : c.user.shownName).fontWeight(.semibold) + Text("  ") + Text(c.body))
          .font(.system(size: 14)).foregroundColor(c.local == nil ? Palette.text : Palette.text2)
          .lineLimit(2)
          .onTapGesture(perform: openComments)
      }
      if post.commentCount > post.previewComments.count {
        Button(action: openComments) {
          Text(post.commentCount == 1 ? tr("查看 1 条评论", "View 1 comment") : tr("查看全部 \(post.commentCount) 条评论", "View all \(post.commentCount) comments"))
            .font(.system(size: 14)).foregroundColor(Palette.text2)
            .frame(minHeight: 30)
        }
        .buttonStyle(.plain)
      }
    }
    .padding(.top, 4)
  }

  /// The big heart of a double-tap (Instagram).
  private var burstHeart: some View {
    Icon(.heartFill, size: 92)
      .foregroundColor(Self.likeRed)
      .shadow(color: .black.opacity(0.25), radius: 8, y: 2)
      .scaleEffect(burst ? 1.1 : 0.4)
      .opacity(burst ? 0.95 : 0)
      .allowsHitTesting(false)
  }

  /// The page's like red (#f0364b, circle-feed.js): the one colour of a liked heart, as in every social app.
  static let likeRed = Color(Palette.uiColor(0xF0364B, 0xF0364B))

  private func toggleLike() {
    let mine = !post.likes.mine
    if mine { pop() }
    UIImpactFeedbackGenerator(style: mine ? .medium : .light).impactOccurred()
    store.setLike(post, mine)
  }

  private func doubleTap() {
    withAnimation(.spring(response: 0.3, dampingFraction: 0.55)) { burst = true }
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.65) { withAnimation(.easeOut(duration: 0.25)) { burst = false } }
    UIImpactFeedbackGenerator(style: .medium).impactOccurred()
    if !post.likes.mine {
      pop()
      store.setLike(post, true)
    }
  }

  private func pop() {
    withAnimation(.spring(response: 0.2, dampingFraction: 0.4)) { bounce = true }
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.18) {
      withAnimation(.spring(response: 0.3, dampingFraction: 0.6)) { bounce = false }
    }
  }
}

/// One watch workout under the stats row: average / max heart rate, distance, load, and the minutes per heart-rate zone.
private struct ActivityLine: View {
  let activity: Post.Activity
  let many: Bool

  var body: some View {
    let a = activity
    VStack(alignment: .leading, spacing: 4) {
      if many {
        Text([a.name, a.durationMin.map(FeedFormat.duration)].compactMap { $0 }.joined(separator: " · "))
          .font(.system(size: 13, weight: .semibold)).foregroundColor(Palette.text)
      }
      HStack(spacing: 14) {
        if many, let k = a.activeKcal { chip(.flame, FeedFormat.number(k), "kcal") }
        if a.avgHr != nil || a.maxHr != nil {
          chip(.heartPulse, "\(a.avgHr.map { FeedFormat.number($0) } ?? "—") / \(a.maxHr.map { FeedFormat.number($0) } ?? "—")", "bpm")
        }
        if let d = a.distanceKm, d > 0 { chip(.mapPin, FeedFormat.number(d, digits: d < 10 ? 2 : 1), "km") }
        if let l = a.load, l > 0 { chip(.activity, FeedFormat.number(l), "TRIMP") }
      }
      if let z = a.hrZones, z.reduce(0, +) > 0 { ZoneBar(zones: z) }
    }
    .padding(.bottom, 12)
  }

  private func chip(_ icon: Icon.Name, _ v: String, _ unit: String) -> some View {
    HStack(spacing: 4) {
      Icon(icon, size: 15).foregroundColor(Palette.text2)
      Text(v).font(.system(size: 14).monospacedDigit()).foregroundColor(Palette.text)
      Text(unit).font(.system(size: 11, weight: .medium)).foregroundColor(Palette.text2)
    }
  }
}

private struct ZoneBar: View {
  let zones: [Double]

  var body: some View {
    let total = zones.reduce(0, +)
    VStack(spacing: 4) {
      GeometryReader { g in
        HStack(spacing: 0) {
          ForEach(Array(zones.enumerated()), id: \.offset) { i, v in
            Rectangle().fill(Palette.train.opacity(0.25 + Double(i) * 0.18))
              .frame(width: g.size.width * CGFloat(v / total))
          }
        }
      }
      .frame(height: 6)
      .clipShape(Capsule())
      .background(Capsule().fill(Palette.surface2))
      HStack {
        ForEach(Array(zones.enumerated()), id: \.offset) { i, v in
          Text("Z\(i + 1) \(Int(v.rounded()))′").font(.system(size: 11).monospacedDigit()).foregroundColor(Palette.text2)
          if i < zones.count - 1 { Spacer(minLength: 2) }
        }
      }
    }
    .accessibilityElement(children: .ignore)
    .accessibilityLabel(tr("心率区间", "Heart-rate zones"))
  }
}

private struct ExerciseThumb: View {
  @EnvironmentObject private var session: AccountSession
  let exercise: Post.Exercise
  @State private var image: UIImage?

  var body: some View {
    ZStack {
      RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Palette.surface2)
      if let image {
        Image(uiImage: image).resizable().scaledToFill()
      } else {
        Icon(.dumbbell, size: 22).foregroundColor(Palette.text2)
      }
    }
    .frame(width: 44, height: 44)
    .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
    .task(id: exercise.thumb) {
      guard let t = exercise.thumb else { return }
      image = await ImageCache.shared.image(t, api: session.api)
    }
  }
}

// MARK: - comments

/// A post's comments, newest at the bottom, with the input bar (Instagram). Own comments can be deleted, others'
/// reported with a reason or their writer blocked (long press). Queued ones show a clock until they are on the server, a red mark when the server refused them.
struct CommentsSheet: View {
  @ObservedObject var store: CirclesStore
  let postId: Int
  @Environment(\.dismiss) private var dismiss
  @State private var text = ""
  @FocusState private var focused: Bool
  @State private var confirmDelete: Comment?
  @State private var gateId = "comments:\(UUID().uuidString)"

  var body: some View {
    let post = store.posts.first { $0.id == postId }
    let list = store.commentList(postId)
    VStack(spacing: 0) {
      HStack {
        Text(tr("评论", "Comments")).font(.system(size: 17, weight: .semibold)).foregroundColor(Palette.text)
        Spacer()
        Button { dismiss() } label: {
          Icon(.x, size: 18).foregroundColor(Palette.text2).frame(width: 32, height: 32).background(Circle().fill(Palette.inSheet))
        }
        .buttonStyle(PressScale())
        .accessibilityLabel(tr("关闭", "Close"))
      }
      .padding(.horizontal, Metrics.gutter).padding(.top, 18).padding(.bottom, 8)
      ScrollViewReader { proxy in
        ScrollView {
          LazyVStack(alignment: .leading, spacing: 14) {
            if let post {
              Text(post.title).font(Typo.caption).foregroundColor(Palette.text2).padding(.bottom, 2)
            }
            if let list {
              if list.isEmpty {
                Text(tr("还没有评论，说点什么吧", "No comments yet. Say something!"))
                  .font(Typo.caption).foregroundColor(Palette.text2).padding(.vertical, 24).frame(maxWidth: .infinity)
              }
              ForEach(list) { c in row(c).id(c.id) }
            } else {
              ProgressView().tint(Palette.text2).frame(maxWidth: .infinity).padding(.vertical, 24)
            }
          }
          .padding(.horizontal, Metrics.gutter).padding(.bottom, 12)
        }
        .onChange(of: list?.count ?? 0) { _ in
          if let last = list?.last { withAnimation { proxy.scrollTo(last.id, anchor: .bottom) } }
        }
      }
      inputBar(max: store.feed.commentMax)
    }
    .background(Palette.sheet.ignoresSafeArea())
    .presentationDetents([.medium, .large])
    .presentationDragIndicator(.visible)
    .task { await store.loadComments(postId) }
    .confirmationDialog(tr("删除这条评论？", "Delete this comment?"), isPresented: Binding(get: { confirmDelete != nil }, set: { if !$0 { confirmDelete = nil } }), titleVisibility: .visible) {
      Button(tr("删除", "Delete"), role: .destructive) {
        if let c = confirmDelete { Task { await store.deleteComment(c, post: postId) } }
      }
      Button(tr("取消", "Cancel"), role: .cancel) {}
    }
    .rulesGate(store, host: gateId)
    .overlay(alignment: .top) { ToastOverlay(store: store) }
  }

  private func row(_ c: Comment) -> some View {
    HStack(alignment: .top, spacing: 10) {
      AvatarView(url: c.user.avatar, name: c.user.name, size: 30)
      VStack(alignment: .leading, spacing: 3) {
        Text(c.user.isSelf ? tr("我", "Me") : c.user.shownName).font(.system(size: 13, weight: .semibold)).foregroundColor(Palette.text)
        Text(c.body).font(.system(size: 15)).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true)
        if c.local == .failed {
          if let why = c.error, !why.isEmpty {
            Text(why).font(.system(size: 12)).foregroundColor(Palette.bad).fixedSize(horizontal: false, vertical: true)
          }
          HStack(spacing: 14) {
            Text(tr("没发出去", "Not sent")).foregroundColor(Palette.bad)
            Button(tr("重试", "Retry")) { store.retryQueued(c) }.foregroundColor(Palette.text)
            Button(tr("删掉", "Discard")) { store.discardQueued(c) }.foregroundColor(Palette.text2)
          }
          .font(.system(size: 12, weight: .medium))
        } else if c.createdAt.flatMap(FeedFormat.parseDate) != nil {
          Text(Self.when(c.createdAt!)).font(.system(size: 12)).foregroundColor(Palette.text3)
        }
      }
      Spacer(minLength: 0)
      if c.local == .pending {
        Icon(.clock, size: 14).foregroundColor(Palette.text3).accessibilityLabel(tr("发送中", "Sending"))
      }
    }
    .contentShape(Rectangle())
    .contextMenu {
      if c.user.isSelf {
        Button(role: .destructive) { confirmDelete = c } label: {
          Label { Text(tr("删除", "Delete")) } icon: { Image(Icon.Name.trash.assetName).renderingMode(.template) }
        }
      } else if c.id > 0 {
        ModerationItems(store: store, kind: .comment, target: c.id, user: c.user.id, post: postId)
      }
    }
  }

  private func inputBar(max: Int) -> some View {
    HStack(spacing: 8) {
      TextField(tr("写评论…", "Add a comment…"), text: $text, axis: .vertical)
        .lineLimit(1...4)
        .focused($focused)
        .submitLabel(.send)
        .font(.system(size: 16))
        .padding(.horizontal, 14).padding(.vertical, 11)
        .background(RoundedRectangle(cornerRadius: 22, style: .continuous).fill(Palette.inSheet))
        .onChange(of: text) { t in if t.count > max { text = String(t.prefix(max)) } }
        .onSubmit(send)
      Button(action: send) {
        Icon(.send, size: 20).foregroundColor(canSend ? Palette.bg : Palette.text3)
          .frame(width: 44, height: 44)
          .background(Circle().fill(canSend ? Palette.text : Palette.inSheet))
      }
      .buttonStyle(PressScale())
      .disabled(!canSend)
      .accessibilityLabel(tr("发送", "Send"))
    }
    .padding(.horizontal, Metrics.gutter).padding(.vertical, 10)
    .background(Palette.sheet)
  }

  private var canSend: Bool { !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

  private func send() {
    guard canSend else { return }
    let t = text
    // the community rules first, once (flag circle_rules); then the comment goes to the queue as before
    store.requireRules(host: gateId) {
      store.addComment(postId, t)
      UIImpactFeedbackGenerator(style: .light).impactOccurred()
    }
    text = ""
  }

  static func when(_ iso: String) -> String {
    guard let d = FeedFormat.parseDate(iso) else { return "" }
    let s = Date().timeIntervalSince(d)
    if s < 60 { return tr("刚刚", "Just now") }
    if s < 3600 { return tr("\(Int(s / 60)) 分钟前", "\(Int(s / 60)) min ago") }
    if s < 86_400 { return tr("\(Int(s / 3600)) 小时前", "\(Int(s / 3600)) h ago") }
    return FeedFormat.day(String(iso.prefix(10)))
  }
}
