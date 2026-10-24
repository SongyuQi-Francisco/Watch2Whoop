// Which native features this build has on the phone it runs on (#253). iOS is the reference app and has all of them.
// The first Android build is for Huawei phones on HarmonyOS 4.x and older (Android compatible, no Google services):
// sign-in, the Today page, the AI chat, hold-to-talk and photos work; phone health sync (how to read Huawei watch data is
// still being researched), push (no FCM) and Sign in with Apple are off and their screens degrade instead of failing.
// Everything Android-specific is gated on [isAndroid], so the iOS app behaves exactly as before.
import 'dart:io' show Platform;

import 'package:flutter/foundation.dart';

/// Whether this runs on Android; replaceable in tests (the test host is never Android).
@visibleForTesting
bool Function() androidDevice = () => !kIsWeb && Platform.isAndroid;

bool get isAndroid => androidDevice();

/// Phone health sync (Open Wearables SDK + RawSync, both HealthKit): iOS only for now.
bool get hasHealthSync => !isAndroid;

/// Push notifications (APNs): iOS only. Huawei phones without Google services have no FCM; HMS Push would come later.
bool get hasPush => !isAndroid;
