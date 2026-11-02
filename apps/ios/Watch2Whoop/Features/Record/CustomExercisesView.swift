import SwiftUI

/// 动作库 → 我的动作 (issue #189, custom-exercises.js): the user's own exercises, what the shared catalog does not know. Mostly
/// the user's AI adds them; here they are listed, changed, deleted and added by hand. Nobody else sees them. A variant of a
/// restricted exercise carries that restriction on the server, whatever is entered here.
struct CustomExercisesView: View {
  let nav: TrainingNav
  @State private var data: CustomExerciseList?
  @State private var failed: String?
  @State private var deleting: CustomExercise?

  var body: some View {
    TrainingPage(nav: nav, title: tr("我的动作", "My exercises")) {
      if let d = data {
        VStack(alignment: .leading, spacing: 14) {
          if d.exercises.isEmpty {
            EmptyNote(text: tr("还没有自己的动作；肌肉图上没对上动作库的动作，可以让 Jarvis 补进来", "No exercises of your own yet; Jarvis can add the ones the muscle map could not match"))
          } else {
            SheetRows {
              ForEach(Array(d.exercises.enumerated()), id: \.element.id) { i, x in
                if i > 0 { RowDivider(inset: 0) }
                row(x, d)
              }
            }
          }
          NavigationLink { CustomExerciseFormView(nav: nav, exercise: nil, options: d, saved: reload) } label: {
            Text(tr("添加动作", "Add exercise")).font(Typo.bodyStrong).foregroundColor(Palette.bg)
              .frame(maxWidth: .infinity, minHeight: 48)
              .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Palette.text))
          }
          .buttonStyle(PressScale())
        }
      } else if let failed {
        RetryNote(text: failed, retry: reload)
      } else {
        EmptyNote(text: "…")
      }
    }
    .task { await load() }
    .confirmationDialog(deleteTitle, isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
                        titleVisibility: .visible, presenting: deleting) { x in
      Button(tr("删除", "Delete"), role: .destructive) { remove(x) }
      Button(tr("取消", "Cancel"), role: .cancel) {}
    }
  }

  private var deleteTitle: String {
    let name = deleting?.name ?? ""
    return tr("删掉「\(name)」？记录里这个名字的组会重新算作没对上动作库。", "Delete “\(name)”? Sets logged under this name will be unmatched again.")
  }

  private func row(_ x: CustomExercise, _ d: CustomExerciseList) -> some View {
    HStack(spacing: 8) {
      VStack(alignment: .leading, spacing: 3) {
        HStack(spacing: 6) {
          Text(x.name).font(Typo.body).foregroundColor(Palette.text).lineLimit(2).multilineTextAlignment(.leading)
          if x.fromAI { Badge(label: "AI") }
        }
        if !x.subtitle.isEmpty { Text(x.subtitle).font(Typo.caption).foregroundColor(Palette.text2).fixedSize(horizontal: false, vertical: true) }
      }
      Spacer(minLength: 4)
      NavigationLink { CustomExerciseFormView(nav: nav, exercise: x, options: d, saved: reload) } label: { smallButton(tr("改", "Edit"), tone: .text) }
        .buttonStyle(PressScale())
      Button { deleting = x } label: { smallButton(tr("删", "Delete"), tone: .bad) }
        .buttonStyle(PressScale())
    }
    .padding(.vertical, 11)
  }

  private func smallButton(_ title: String, tone: Tone) -> some View {
    Text(title).font(.system(size: 14, weight: .medium)).foregroundColor(tone.color)
      .padding(.horizontal, 12).frame(minHeight: 36)
      .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Palette.surface2))
  }

  private func reload() { Task { await load() } }

  private func load() async {
    failed = nil
    do {
      data = try await nav.store.customExercises()
    } catch {
      if data == nil { failed = TrainingStore.message(error) } else { nav.say(Toast(TrainingStore.message(error))) }
    }
  }

  private func remove(_ x: CustomExercise) {
    Task {
      if let error = await nav.store.deleteCustom(x.id) {
        nav.say(Toast(error))
        return
      }
      nav.say(Toast(tr("已删除", "Deleted")))
      await load()
    }
  }
}

/// Add or change one: names, aliases, primary / secondary muscles, equipment. Muscle and equipment names come from the
/// server, in the app's language.
struct CustomExerciseFormView: View {
  let nav: TrainingNav
  let exercise: CustomExercise?
  let options: CustomExerciseList
  let saved: () -> Void
  @Environment(\.dismiss) private var dismiss
  @State private var nameZh: String
  @State private var nameEn: String
  @State private var aliases: String
  @State private var primary: [String]
  @State private var secondary: [String]
  @State private var equipment: [String]
  @State private var message: String?
  @State private var busy = false

  init(nav: TrainingNav, exercise: CustomExercise?, options: CustomExerciseList, saved: @escaping () -> Void) {
    self.nav = nav
    self.exercise = exercise
    self.options = options
    self.saved = saved
    _nameZh = State(initialValue: exercise?.nameZh ?? "")
    _nameEn = State(initialValue: exercise?.nameEn ?? "")
    _aliases = State(initialValue: (exercise?.aliases ?? []).joined(separator: ", "))
    _primary = State(initialValue: exercise?.primary ?? [])
    _secondary = State(initialValue: exercise?.secondary ?? [])
    _equipment = State(initialValue: exercise?.equipment ?? [])
  }

  var body: some View {
    TrainingPage(nav: nav, title: exercise == nil ? tr("添加动作", "Add exercise") : tr("改动作", "Edit exercise")) {
      VStack(alignment: .leading, spacing: 14) {
        FormField(label: tr("中文名", "Chinese name"), text: limited($nameZh), background: Palette.inSheet)
        FormField(label: tr("英文名", "English name"), text: limited($nameEn), background: Palette.inSheet)
        FormField(label: tr("别名（逗号分隔）", "Aliases (comma separated)"), text: $aliases, background: Palette.inSheet)
        chips(title: tr("主要肌群", "Primary muscles"), note: tr("最多 \(CustomExerciseForm.maxPrimary) 个", "at most \(CustomExerciseForm.maxPrimary)"), items: options.muscles, selection: $primary)
        chips(title: tr("次要肌群", "Secondary muscles"), note: nil, items: options.muscles, selection: $secondary)
        chips(title: tr("器械", "Equipment"), note: nil, items: options.equipment, selection: $equipment)
        if let message { Text(message).font(Typo.caption).foregroundColor(Palette.bad).fixedSize(horizontal: false, vertical: true) }
        PrimaryButton(title: tr("保存", "Save"), busy: busy) { save() }
      }
    }
  }

  /// The server's limit (`custom.MAX_NAME`).
  private func limited(_ b: Binding<String>) -> Binding<String> {
    Binding(get: { b.wrappedValue }, set: { b.wrappedValue = String($0.prefix(80)) })
  }

  private func chips(title: String, note: String?, items: [NamedKey], selection: Binding<[String]>) -> some View {
    VStack(alignment: .leading, spacing: 8) {
      HStack(spacing: 6) {
        Text(title).font(Typo.label).foregroundColor(Palette.text2)
        if let note { Text(note).font(Typo.label).foregroundColor(Palette.text3) }
      }
      WrapLayout(spacing: 6) {
        ForEach(items) { o in
          ToggleChip(title: o.name, on: selection.wrappedValue.contains(o.key)) {
            if let i = selection.wrappedValue.firstIndex(of: o.key) { selection.wrappedValue.remove(at: i) } else { selection.wrappedValue.append(o.key) }
          }
        }
      }
    }
  }

  private func save() {
    let req = CustomExerciseForm.body(nameZh: nameZh, nameEn: nameEn, aliases: aliases, primary: primary, secondary: secondary, equipment: equipment)
    guard let body = req.body else {
      message = req.error
      return
    }
    busy = true
    message = nil
    Task {
      let error = await nav.store.saveCustom(id: exercise?.id, body: body)
      busy = false
      if let error {
        message = error
      } else {
        nav.say(Toast(tr("已保存", "Saved")))
        saved()
        dismiss()
      }
    }
  }
}
