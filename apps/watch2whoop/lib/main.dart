// Watch2Whoop iOS app: one app that both syncs Apple Health data in the background (Open Wearables SDK) and shows / logs it (embedded Today page).
// Watch2Whoop: background Apple Health sync (Open Wearables SDK) + the Body Engine today page, in one app.
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_localizations/flutter_localizations.dart';

import 'src/app_state.dart';
import 'src/health_sync.dart';
import 'src/i18n.dart';
import 'src/pages/home_page.dart';
import 'src/pages/onboarding_page.dart';
import 'src/raw_sync.dart';
import 'src/setup_flow.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await loadLangOverride(); // profile language saved from the Today page (null = system language)
  await AppState.instance.load();
  try {
    // Also runs when woken by HealthKit in the background: restore the SDK session and the SDK continues syncing on its own.
    await HealthSync.instance.start(AppState.instance.config);
  } catch (e) {
    debugPrint('HealthSync.start failed: $e');
  }
  await RawSync.instance.load(); // last upload of beat-to-beat HRV / ECG, for the sync page
  SetupLinks.instance.start(); // watch2whoop://setup?… passed in when the app is opened by scanning the setup QR code with the camera
  langOverride.addListener(_rebuildAll);
  runApp(const Watch2WhoopApp());
}

/// tr() reads the language without a BuildContext, so a language change marks every element for rebuild (state is kept,
/// the WebView is not reloaded); the root ListenableBuilder also switches MaterialApp.locale.
void _rebuildAll() {
  void mark(Element e) {
    e.markNeedsBuild();
    e.visitChildren(mark);
  }

  WidgetsBinding.instance.rootElement?.visitChildren(mark);
}

class Watch2WhoopApp extends StatelessWidget {
  const Watch2WhoopApp({super.key});

  ThemeData _theme(Brightness b) {
    final dark = b == Brightness.dark;
    final scheme = ColorScheme.fromSeed(
      seedColor: const Color(0xFF34C759),
      brightness: b,
      surface: dark ? Colors.black : const Color(0xFFF2F2F7),
    );
    return ThemeData(
      colorScheme: scheme,
      scaffoldBackgroundColor: dark ? Colors.black : const Color(0xFFF2F2F7),
      appBarTheme: AppBarTheme(
        backgroundColor: dark ? Colors.black : const Color(0xFFF2F2F7),
        surfaceTintColor: Colors.transparent,
        systemOverlayStyle: dark ? SystemUiOverlayStyle.light : SystemUiOverlayStyle.dark,
      ),
      cardTheme: CardThemeData(
        color: dark ? const Color(0xFF1C1C1E) : Colors.white,
        elevation: 0,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      ),
      useMaterial3: true,
    );
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: Listenable.merge([AppState.instance, langOverride]),
      builder: (context, _) {
        final config = AppState.instance.config;
        return MaterialApp(
          title: 'Watch2Whoop',
          debugShowCheckedModeBanner: false,
          theme: _theme(Brightness.light),
          darkTheme: _theme(Brightness.dark),
          home: config.onboardingDone ? const HomePage() : const OnboardingPage(),
          builder: (context, child) => child ?? const SizedBox.shrink(),
          locale: isZh ? const Locale('zh') : const Locale('en'),
          supportedLocales: const [Locale('zh'), Locale('en')],
          localizationsDelegates: GlobalMaterialLocalizations.delegates,
        );
      },
    );
  }
}
