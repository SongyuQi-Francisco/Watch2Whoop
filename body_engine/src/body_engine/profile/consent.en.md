# Watch2Whoop beta: consent to the use of health data

Version: 2026-10-22.1

> The first time you open the Today page, the app shows the key points and the full text of this consent form. Your health data is processed only after you tick "I have read and agree"; the system records the time and this version number. Signing on paper also works (the administrator records the date with `onboard.py --consent-at`). When the form is revised you are asked to confirm again.
> This is the English text of `consent.md`, clause by clause, with the same version number. This file and `docs/family-consent.en.md` must be identical (a test checks it); change both together with the Chinese text and update the version number.

I (the user of this account) agree that the administrator (the person who maintains the Watch2Whoop server) opens a Watch2Whoop test account for me and processes my health data as described below.

## 1. What data is collected
- **Health data from the watch / phone**: heart rate, heart rate variability, sleep, steps, active energy, workouts, weight, blood oxygen, respiratory rate and the like, synced from Apple Health by the Watch2Whoop app. Only what I allow the app to read in Health is synced, and I can turn it off in the iPhone settings at any time.
- **ECGs and beat-to-beat intervals**: the ECG waveforms I record with the watch's ECG app, with Apple's classification (for example sinus rhythm, signs of AFib) and average heart rate; the beat-to-beat intervals (the time of each beat) the watch records each time it measures heart rate variability. Also read from Health by the app, and only after I allow it.
- **What I fill in and log myself**: profile (name, birthday, sex, height, goal weight, time zone), food, workouts, journal, supplements, lab results, body events and the like.
- **Results the system calculates**: recovery score, sleep score, strain, reminders and the like.
- Not collected: location, contacts, photos (unless I upload them myself), data from other apps.

## 2. Before I agree
- Until I agree, data synced from my phone is only **stored, not processed** (not imported, not calculated, not shown to anyone).
- ECGs and beat-to-beat intervals are **not uploaded or stored** until I agree to this version (or a later one).
- It is fine not to agree: I tell the administrator, who then deletes my account and the stored data.

## 3. Where the data is stored
- On the administrator's own server: **a cloud server of the administrator's own**, addresses `body.example.com` and `sync.example.com`, with HTTPS encryption all the way.
- The database is backed up every night, encrypted and stored on the administrator's own computer.
- Not sold and not shared with advertisers or any third party; not used for any purpose other than "showing me my own health analysis".

## 4. Who can see it
- **Me**: with my own account (the login link / QR code is given only to me and works like a key).
- **Other family members**: **cannot see** my data by default. Only after I explicitly agree and the administrator sets up "sharing" can the named people see the named content; I can ask to cancel it at any time.
- **ECGs**: **not included in sharing** by default; even with sharing set up, someone can see my ECGs only if I separately allow it.
- **The administrator**: as the maintainer of the server, technically has access to the database. The administrator promises to access it only for maintenance, troubleshooting, backup and restore, and exporting / deleting at my request, and not to look at my data otherwise.
- **AI assistant**: **off** by default. If I connect an AI myself under Me → AI settings in the app (for example Claude or ChatGPT), **the part of my data needed for an answer** is sent to the AI company I chose when it answers a question, and is handled under that company's terms. Nothing is sent if I do not connect one. For ECGs only a summary such as the classification and heart rate is sent, **never the waveform**.
- **Backup AI (optional)**: if I have not connected my own AI, I can use the backup AI the administrator provides (DeepSeek, a Chinese company with servers in China; the administrator pays, with a monthly allowance per person). **The app asks me separately before the first use.** After I confirm, each question sends DeepSeek my message, basic profile (name, age, sex, height, time zone), long-term memory, and whatever of my own scores, sleep, workouts, food, weight, lab results, supplements, journal and reminders the answer needs; **photos and screenshots I send, and frames taken from videos, are sent as well**, and handled under DeepSeek's terms. ECGs and beat-to-beat heart data are not sent, and other family members' data is not read. Nothing is sent if I do not confirm, and I can withdraw at any time under AI settings.
- **Voice input**: when I hold to talk in Jarvis (or send a video with sound), the sound is turned into text on the administrator's server and **not sent to any third party**; it is deleted right after, no recording is kept, and the text goes to the AI only after I have confirmed or edited it.
- **Automatic feedback**: when the AI that helps me finds a problem in the system (a bug) or hears about a feature I want, it turns it into a feedback report for the administrator by itself and tells me its number. The report only says what went wrong in the product; the server **removes health values, names, e-mail addresses, phone numbers and links** before the administrator sees it (a daily e-mail digest). The administrator may turn the anonymised content into public development tasks, but never says who reported it. My original words stay only on the server. Under Me → Feedback I can view my reports, withdraw ones not handled yet, or switch to "Ask me first" / "Off".

## 5. My rights
- **View and correct**: I can edit my profile myself at any time under Me → Profile in the app; if other data is wrong, I can correct it myself or ask the administrator to.
- **Export**: I can ask for an export of all my data (spreadsheet files) at any time; the administrator gives it to me within 7 days.
- **Delete**: I can ask for my account and all my data to be deleted at any time. The live data is deleted immediately; old copies in backups disappear within **6 months** as the backups rotate (backups keep 7 daily, 4 weekly and 6 monthly copies).
- **Withdraw consent**: I can withdraw this consent at any time, with the same effect as asking for deletion.

## 6. Risks
- This is a test system built by a family, not a medical device; the scores and reminders are for reference only and do not replace a doctor's diagnosis.
- ECG and heart rhythm content **only shows Apple's results**; this system does not diagnose anything. Apple's ECG cannot detect a heart attack; if I feel unwell I should see a doctor promptly.
- The administrator does their best to keep it secure (HTTPS, per-user isolation, limits on failed logins, only the necessary endpoints open), but cannot guarantee that nothing ever goes wrong. If data is leaked, the administrator tells me as soon as possible.

---

I have read and understood the above and agree that my health data is processed this way (ticking "I have read and agree" in the app means I agree).
