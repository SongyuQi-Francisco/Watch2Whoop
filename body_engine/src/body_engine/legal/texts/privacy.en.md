# {{app_name}} Privacy Policy

Version: 2026-10-31

This policy explains what information {{app_name}} (the iOS app and the server it connects to, "the service") collects, how it is used, where it is stored, who processes it and what your rights are. The service is operated by **{{entity}}** ("we").

{{app_name}} is open-source software and anyone can run their own server. If you connect to a server someone else runs, that operator is responsible for your data and their published policy applies.

## 1. What we collect

**Account**

- When you sign in with e-mail: your e-mail address.
- When you sign in with Apple: the user identifier Apple gives us, and the e-mail address (possibly Apple's private relay address) and name you choose to share; so that we can revoke this app's access when you delete your account, we keep Apple's refresh token, encrypted.
- The name of each signed-in device and its sign-in token (one per device, each can be signed out on its own).

**Health data (Apple Health / HealthKit)**

Read only after you allow it in Health, and only the types you allow; you can turn it off in the iPhone settings at any time. It may include: heart rate, resting heart rate, walking heart rate average, heart rate variability and beat-to-beat intervals (the time of each beat), ECGs (waveform, Apple's classification and average heart rate), sleep, workouts, steps, distance, flights climbed, active and resting energy, exercise and stand time, respiratory rate, blood oxygen, wrist temperature, body temperature, VO₂ max, weight, body fat, height, blood pressure, blood glucose, water, mindful minutes, time in daylight, walking and running metrics, environmental and headphone audio levels.

**What you log yourself**

Your profile (name, date of birth, sex, height, time zone, goals), food, workouts, weight, water, journal, supplements, lab results, body events, sleep goals, recipes, shopping lists and purchase records.

**What is calculated**

Recovery, sleep score, strain, body age, stress, trends, reminders and so on, calculated on your iPhone or on the server from the data above.

**Circles (friends)**

Your name and picture, the posts, comments and cheers you send, and the data you choose to share with the other members when you join a circle.

**AI (Jarvis)**

Your conversations with the AI (text, photos you send and frames taken from videos, text transcribed from your voice), the long-term memory the AI keeps for you, and your AI settings (an API key of your own is stored encrypted; only its last 4 characters are shown).

**Device and sync information**

App version, iOS version, device model and sync status (for example the last sync time, whether Low Power Mode is on), used to keep sync reliable and to find problems.

**Usage**

When you use the app and which features and pages you use (not the content you log and not your health values), used to understand whether features work well and to improve the product.

**Feedback**

Problems and feature requests that you or the AI report.

**We do not collect**: precise location, contacts, your photo library (only the photos you pick and send), the advertising identifier (IDFA). We do not track you across apps or websites; the app has no ads and no third-party analytics SDK.

## 2. How we use it

- To provide the service: sync, scores and trends, your records, circles, reminders and notifications, and AI answers to your questions.
- To keep the service reliable and secure: finding sync and server problems, preventing abuse (for example limiting failed sign-ins).
- To understand how features are used and improve the product (usage only, never the content you log or your health values).
- To answer your feedback and requests.

**Health data is never used for advertising or marketing, never sold and never given to data brokers.** We do not use your data to train AI models, and we do not use it for research.

## 3. Where it is stored and how it is protected

- The server is located in: **{{region}}**.
- Your iPhone keeps a local cache for offline viewing and on-phone calculation; it is excluded from iCloud and device backups and is deleted when you sign out.
- Everything travels over HTTPS; each person's data is separated by account, and others see only what you explicitly share with them; your own AI keys and Apple refresh tokens are stored encrypted.
- No system is perfectly secure. If a breach affects your data, we will tell you as soon as possible and report it as the applicable law requires.

## 4. Who processes your data

- **The AI you choose**: AI is off by default. Once you connect your own AI under Me → AI settings (for example Claude, ChatGPT, Gemini or another compatible service), **the part of your data an answer needs** is sent to that company, which handles it under its own terms.
- **Backup AI (DeepSeek)**: if you have not connected your own AI, you can choose the backup AI we provide. **You are asked separately before the first use.** After you agree, each question sends DeepSeek (a company with servers in China): your message, name / age / sex / height / time zone, long-term memory, and, when the answer needs them, your scores, sleep, workouts, food, weight, daily metrics such as respiratory rate / blood oxygen / wrist temperature, lab results, supplements, journal, reminders, shopping lists, what you have at home, product prices and purchase records, and the photos and video frames you send. You can withdraw at any time.
- **ECGs and beat-to-beat intervals**: ECG waveforms and beat-to-beat data are never sent to any AI. An AI you connect yourself can read at most a summary such as the ECG classification and average heart rate; the backup AI reads none of it.
- **Voice**: when you hold to talk, your voice is turned into text on your iPhone (Apple's on-device recognition) or on our server; it is not sent to any third party and no recording is kept.
- **Circle members**: see only what you ticked when you joined a circle; you can change it or leave at any time. ECGs are never shared through circles.
- **Apple**: Sign in with Apple and push notifications (APNs).
- **E-mail delivery service**: only to deliver sign-in codes; it receives only your e-mail address and the code.
- **Hosting provider**: provides the machines and network the server runs on.
- **Legal requirements**: only when the law requires it, and only as far as it requires.

Apart from the above, we do not give your data to anyone.

## 5. How long we keep it

- While your account exists.
- When you delete your account, the live data on the server is deleted at once; old copies in backups disappear within **6 months** as the backups rotate.
- Voice is deleted as soon as it has been turned into text; sign-in codes are cleared within a day; accounts that are never activated are deleted after 7 days.

## 6. Your rights

- **See and correct**: everything is visible in the app; change it under Me → Profile and elsewhere.
- **Export**: ask through the contact below and we will send you a machine-readable copy within a reasonable time (normally 30 days).
- **Delete**: delete your account and all its data at any time under Me → Delete account; if you signed in with Apple, we also revoke this app's access at Apple.
- **Withdraw consent**: withdrawing the data consent has the same effect as deleting the account; the backup AI consent can be withdrawn on its own under Me → AI settings; Health access is switched off in the iPhone settings.
- The law where you live may give you more rights (for example rectification, restriction, complaining to a supervisory authority); use the contact below.

## 7. Children

The service is not meant for anyone under **{{min_age}}**. If you are younger, please do not sign up; we delete such accounts when we find them.

## 8. International transfers

Your data is stored on servers in {{region}}. When you use the backup AI, the content listed in section 4 goes to DeepSeek in China; when you use an AI of your choice, it is handled where that company is.

## 9. Changes

This policy has a version number. Important changes are shown in the app; changes that need your consent are presented for you to confirm again.

## 10. Contact

{{contact}}
