// Scan the "setup QR code": returns the text as soon as a watch2whoop://setup?… is scanned.
import 'package:flutter/material.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

import '../config.dart';
import '../i18n.dart';

class ScanPage extends StatefulWidget {
  const ScanPage({super.key});

  @override
  State<ScanPage> createState() => _ScanPageState();
}

class _ScanPageState extends State<ScanPage> {
  final _controller = MobileScannerController(formats: const [BarcodeFormat.qrCode]);
  bool _done = false;
  String? _hint;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _onDetect(BarcodeCapture capture) {
    if (_done) return;
    for (final b in capture.barcodes) {
      final v = b.rawValue;
      if (v == null) continue;
      if (parseSetupText(v).isEmpty) {
        setState(() => _hint = tr('这不是 Watch2Whoop 的设置二维码', 'Not a Watch2Whoop setup code'));
        continue;
      }
      _done = true;
      Navigator.of(context).pop(v);
      return;
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        title: Text(tr('扫描设置二维码', 'Scan setup code')),
      ),
      body: Stack(
        children: [
          MobileScanner(
            controller: _controller,
            onDetect: _onDetect,
            errorBuilder: (context, error) => Center(
              child: Padding(
                padding: const EdgeInsets.all(32),
                child: Text(
                  error.errorCode == MobileScannerErrorCode.permissionDenied
                      ? tr('没有相机权限：到「设置」→ Watch2Whoop 打开相机，或者回去用「粘贴设置信息」。',
                          'No camera access. Allow it in Settings → Watch2Whoop, or paste the setup text instead.')
                      : tr('相机不可用，请回去用「粘贴设置信息」。', 'Camera unavailable — paste the setup text instead.'),
                  textAlign: TextAlign.center,
                  style: const TextStyle(color: Colors.white, height: 1.4),
                ),
              ),
            ),
          ),
          Align(
            alignment: Alignment.bottomCenter,
            child: SafeArea(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Text(
                  _hint ?? tr('对准家人电脑上的二维码', 'Point at the QR code on the setup screen'),
                  style: const TextStyle(color: Colors.white, fontSize: 15),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
