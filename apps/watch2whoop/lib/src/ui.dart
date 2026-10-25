// A few shared widgets: grouped cards, buttons, hint rows. Style matches the Today page: restrained, few words, borderless cards.
import 'package:flutter/material.dart';

import 'account_api.dart';
import 'config.dart';
import 'i18n.dart';
import 'platform_features.dart';
import 'server_lock.dart';

export 'server_lock.dart' show redactServer, serverLocked;
export 'js_dialogs.dart'; // the Today page's alert / confirm / prompt (home_page.dart)

class Group extends StatelessWidget {
  const Group({super.key, this.title, required this.rows, this.footer});
  final String? title;
  final List<Widget> rows;
  final String? footer;

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    final items = <Widget>[];
    for (var i = 0; i < rows.length; i++) {
      if (i > 0) items.add(Divider(height: 1, indent: 16, color: muted.withValues(alpha: 0.15)));
      items.add(rows[i]);
    }
    return Padding(
      padding: const EdgeInsets.only(bottom: 22),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (title != null)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 6),
              child: Text(title!, style: TextStyle(fontSize: 13, color: muted, fontWeight: FontWeight.w500)),
            ),
          Card(clipBehavior: Clip.antiAlias, child: Column(children: items)),
          if (footer != null)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 6, 16, 0),
              child: Text(footer!, style: TextStyle(fontSize: 12.5, color: muted, height: 1.35)),
            ),
        ],
      ),
    );
  }
}

class Row2 extends StatelessWidget {
  const Row2(this.label, {super.key, this.value, this.onTap, this.danger = false, this.trailing});
  final String label;
  final String? value;
  final VoidCallback? onTap;
  final bool danger;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 13),
        child: Row(
          children: [
            Expanded(
              child: Text(label, style: TextStyle(fontSize: 16, color: danger ? cs.error : cs.onSurface)),
            ),
            if (value != null)
              Flexible(
                child: Text(
                  value!,
                  textAlign: TextAlign.right,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(fontSize: 15, color: cs.onSurfaceVariant, fontFeatures: const [FontFeature.tabularFigures()]),
                ),
              ),
            ?trailing,
            if (onTap != null && trailing == null && !danger)
              Padding(
                padding: const EdgeInsets.only(left: 6),
                child: Icon(Icons.chevron_right, size: 20, color: cs.onSurfaceVariant.withValues(alpha: 0.6)),
              ),
          ],
        ),
      ),
    );
  }
}

class BigButton extends StatelessWidget {
  const BigButton(this.label, {super.key, this.onPressed, this.busy = false, this.secondary = false});
  final String label;
  final VoidCallback? onPressed;
  final bool busy;
  final bool secondary;

  @override
  Widget build(BuildContext context) {
    final child = busy
        ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2.2))
        : Text(label, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600));
    final style = ButtonStyle(
      minimumSize: const WidgetStatePropertyAll(Size.fromHeight(50)),
      shape: WidgetStatePropertyAll(RoundedRectangleBorder(borderRadius: BorderRadius.circular(14))),
    );
    return secondary
        ? TextButton(onPressed: busy ? null : onPressed, style: style, child: child)
        : FilledButton(onPressed: busy ? null : onPressed, style: style, child: child);
  }
}

/// A line of small grey hint text (limits such as lock screen / swiping the app away).
class Hint extends StatelessWidget {
  const Hint(this.text, {super.key, this.icon = Icons.info_outline, this.color});
  final String text;
  final IconData icon;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    final c = color ?? Theme.of(context).colorScheme.onSurfaceVariant;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(padding: const EdgeInsets.only(top: 1.5), child: Icon(icon, size: 15, color: c)),
        const SizedBox(width: 6),
        Expanded(child: Text(text, style: TextStyle(fontSize: 13, color: c, height: 1.35))),
      ],
    );
  }
}

/// System limits of background sync, in one sentence.
String get limitsHint => tr(
  'iPhone 锁屏时不允许读取健康数据；从后台划掉 app 后不会自动同步，打开一次即可补上。',
  'iOS blocks Health reads while the phone is locked, and a force-quit app stops syncing until you open it again.',
);

/// Optional hint when the server is only on the private network (not needed for public https addresses).
String get vpnHint => isAndroid
    ? tr('如果你的服务器只在内网（例如家里的电脑），需要先在手机上连好 VPN。', 'If your server is only on a private network, connect your VPN first.')
    : tr('如果你的服务器只在内网（例如家里的电脑），需要先在 iPhone 上连好 VPN。', 'If your server is only on a private network, connect your VPN first.');

String urlError(String? code) => switch (code) {
  'empty' => tr('请填写地址', 'Required'),
  'https' => tr('必须用 https 地址', 'Must be an https address'),
  'scheme' => tr('只支持 https://', 'Only https:// is supported'),
  'extra' => tr('地址里不要带账号、? 或 #', 'No credentials, ? or # in the address'),
  'other_server' => tr('这个设置链接不是给这个 app 的', 'This setup link is for a different server'),
  _ => tr('地址格式不对', 'Invalid address'),
};

String connectError(Object e) {
  final code = redactServer(e is Exception ? e.toString() : '$e');
  if (code.contains('unsupported')) return watchDataPending;
  if (code.contains('invalid_code')) return tr('邀请码无效或已用过 / 过期，请重新生成', 'Invitation code is invalid, used or expired');
  if (code.contains('network')) return tr('连不上同步服务器：检查网络和地址（服务器在内网时要先连 VPN）', 'Cannot reach the sync server: check the network and address (VPN if it is private)');
  if (code.contains('server')) return tr('服务器出错：$code', 'Server error: $code');
  if (code.contains('sign_in')) {
    return tr('同步没连上（手机没接受新的同步登录），请再点一次「连接同步」', 'Sync did not connect (the phone did not take the new sign-in); tap Connect sync again');
  }
  return tr('连接失败：$code', 'Connection failed: $code');
}

/// Android (#253): shown where iOS has the Health sync (sync page, onboarding); reading Huawei watch data comes later.
String get watchDataPending => tr('华为手表数据接入开发中', 'Huawei watch data support is in development');

/// Android: what works meanwhile.
String get watchDataPendingHint => tr('这台手机暂时不上传健康数据；今天页、记录、和 AI 对话、拍照记餐都能正常用。',
    'This phone does not upload health data yet; the Today page, logging, the AI chat and meal photos all work.');

/// Generic messages for account API errors (callers override the context-specific ones).
String accountError(AccountApiException e) => switch (e.code) {
  'network' => tr('连不上服务器：检查网络和地址', 'Cannot reach the server: check the network and address'),
  'rate_limited' => e.retryAfter != null
      ? tr('太频繁了，请 ${e.retryAfter!.inSeconds} 秒后再试', 'Too many attempts — try again in ${e.retryAfter!.inSeconds}s')
      : tr('太频繁了，请稍后再试', 'Too many attempts — try again later'),
  'invalid' => tr('登录已失效，请重新登录', 'Signed out — please sign in again'),
  'pending' => tr('账号还没激活', 'The account is not activated yet'),
  'provisioning' => tr('同步账号还在准备中，请稍后再试', 'The sync account is still being prepared — try again shortly'),
  'unavailable' => tr('服务器暂时不支持这个操作', 'The server does not support this right now'),
  'bad_request' => tr('填写的内容格式不对', 'Invalid input'),
  _ => tr('服务器出错（${e.status ?? e.code}）', 'Server error (${e.status ?? e.code})'),
};

String ago(DateTime? t) {
  if (t == null) return '—';
  final d = DateTime.now().difference(t);
  if (d.inSeconds < 60) return tr('刚刚', 'just now');
  if (d.inMinutes < 60) return tr('${d.inMinutes} 分钟前', '${d.inMinutes} min ago');
  if (d.inHours < 24) return tr('${d.inHours} 小时前', '${d.inHours} h ago');
  final l = t.toLocal();
  String two(int v) => v.toString().padLeft(2, '0');
  return '${l.month}/${l.day} ${two(l.hour)}:${two(l.minute)}';
}

/// When showing an address, strip the scheme and truncate the middle if too long.
String shortUrl(String url) {
  final s = url.replaceFirst(RegExp(r'^https?://'), '');
  return s.length <= 34 ? s : '${s.substring(0, 16)}…${s.substring(s.length - 16)}';
}

/// 中文 / English switch for the top right of the first-run screens (same look as the Today page login switch).
/// Sets the language override (saved; i18n.dart): the root rebuilds every native screen at once, and the Today page
/// receives it with the first load and saves it to the profile after login.
class LangSwitch extends StatelessWidget {
  const LangSwitch({super.key});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    Widget option(String lang, String label) {
      final on = currentLang == lang;
      return Semantics(
        selected: on,
        button: true,
        child: TextButton(
          onPressed: () => setLangOverride(lang),
          style: TextButton.styleFrom(
            minimumSize: const Size(0, 32),
            padding: const EdgeInsets.symmetric(horizontal: 8),
            tapTargetSize: MaterialTapTargetSize.shrinkWrap,
            foregroundColor: on ? cs.onSurface : cs.onSurfaceVariant,
            backgroundColor: on ? cs.onSurface.withValues(alpha: 0.08) : null,
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
          ),
          child: Text(label, style: TextStyle(fontSize: 13, fontWeight: on ? FontWeight.w600 : FontWeight.w400)),
        ),
      );
    }

    return ValueListenableBuilder<String?>(
      valueListenable: langOverride,
      builder: (context, _, _) => Semantics(
        label: '${kLangLabel.$2} / ${kLangLabel.$1}',
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            option('zh', kLangNames.$1),
            Text('/', style: TextStyle(fontSize: 13, color: cs.onSurfaceVariant)),
            option('en', kLangNames.$2),
          ],
        ),
      ),
    );
  }
}

/// Address input field, normalized on blur.
class UrlField extends StatelessWidget {
  const UrlField({super.key, required this.controller, required this.label, required this.hint, required this.kind, this.error});
  final TextEditingController controller;
  final String label;
  final String hint;
  final UrlKind kind;
  final String? error;

  @override
  Widget build(BuildContext context) {
    return TextField(
      controller: controller,
      keyboardType: TextInputType.url,
      autocorrect: false,
      enableSuggestions: false,
      textInputAction: TextInputAction.next,
      decoration: InputDecoration(
        labelText: label,
        hintText: hint,
        errorText: error,
        errorMaxLines: 2,
        border: const OutlineInputBorder(borderRadius: BorderRadius.all(Radius.circular(12))),
      ),
    );
  }
}
