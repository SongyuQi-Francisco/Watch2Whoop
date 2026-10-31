# 法律文本 / Legal texts

面向公众的隐私政策和使用条款（App Store 上架用），中英文各一份；服务器把它们渲染成公开网页。
Privacy policy and terms of use for the public (App Store), in Chinese and English, served by the server as public pages.

| 文件 / File | 网页 / Page | 状态 / Status |
|---|---|---|
| `privacy.md`, `privacy.en.md` | `/legal/privacy?lang=zh\|en` | 生效 / in force |
| `terms.md`, `terms.en.md` | `/legal/terms?lang=zh\|en` | 生效 / in force |
| `consent-public-draft.md`, `.en.md` | — | **草案，未启用** / draft, not active |

## 占位符 / Placeholders

公开仓库不写运营者的名字、邮箱、域名。文本里的占位符在请求时从服务器设置填入（`body_engine/src/body_engine/legal/pages.py`），没设置时显示中性的默认文字。
The public repository names no operator; placeholders are filled from server settings at request time, with neutral defaults.

| 占位符 | 设置 / Setting | 默认 / Default |
|---|---|---|
| `{{app_name}}` | `BODY_LEGAL_APP_NAME` | Watch2Whoop（上架名定了再改 / change once the store name is decided） |
| `{{entity}}` | `BODY_LEGAL_ENTITY` | 本服务器的运营者 / the operator of this server |
| `{{contact}}` | `BODY_LEGAL_CONTACT` | 未填写 / not set |
| `{{region}}` | `BODY_DATA_REGION` | 运营者选择的地区 / the operator's region |
| `{{governing_law}}` | `BODY_LEGAL_GOVERNING_LAW` | 运营者所在地的法律 / the operator's law |
| `{{min_age}}` | `BODY_LEGAL_MIN_AGE` | 16 |

每个文字设置都可以另加 `_EN` 版本（例如 `BODY_LEGAL_ENTITY_EN`），没有就中英文用同一个值。
Each text setting may have an `_EN` variant; otherwise both languages use the same value.

## 改文本 / Editing

1. 改 `docs/legal/*.md`，中英文一起改，`版本：` / `Version:` 一起更新。
2. 复制到 `body_engine/src/body_engine/legal/texts/`（Docker 镜像只打包 `body_engine/`；`tests/test_legal.py` 检查两边一字不差）。
3. 隐私政策第 1 节变了，同时检查 `apps/ios/Watch2Whoop/PrivacyInfo.xcprivacy` 和 App Store Connect 的隐私标签。

## 同意书草案 / Consent draft

`consent-public-draft.md` 是面向公众的同意书草案，**没有启用**：生效的同意书仍是 `docs/family-consent.md`（= `profile/consent.md`），改同意书要负责人批准。和生效版（2026-10-22.2）相比：

- 标题去掉「测试版」，「管理员 / 开发者」改成「运营者」，「其他家人」改成「其他人」；去掉线下签字和 `onboard.py`。
- 第 1 节：健康数据加上血压、血糖；加「登录信息」（邮箱 / Apple 标识，加密保存的 Apple 刷新令牌）；不收集里加「相册（只有自己选出来发送的照片）」。
- 第 2 节：圈子成员还能看到头像和动态；运营者「只在维护服务、排查问题和处理我的请求时使用」；托底 AI 写明 DeepSeek 服务器在中国；语音可能在 iPhone 本机转文字；新增「不用于广告和营销、不出售、不训练 AI 模型」。
- 第 3 节：删除改成在 app「我 → 删除账号」自己删，并撤销 Apple 授权；新增「导出」；新增最低年龄（见使用条款）。
- 第 4 节：「不是医疗器械」；AI 回答可能出错；安全措施加「加密保存密钥」；存放地区和跨境见隐私政策。

启用步骤（负责人批准后）：正文（去掉开头的草案说明）复制到 `docs/family-consent.md` 和 `profile/consent.md`（英文同样两份），版本号改成正式的日期版本，`profile/consent.py` 的 `SUMMARY` / `SUMMARY_EN` 按新文本改；已同意旧版的人会被请求重新确认。
To activate (after the maintainer approves): copy the text without the draft note into the four consent files, set a final
version, update `SUMMARY` / `SUMMARY_EN` in `profile/consent.py`; people who agreed to the old version are asked to reconfirm.
