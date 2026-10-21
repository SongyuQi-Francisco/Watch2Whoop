# Watch2Whoop beta: consent to the use of health data

Version: 2026-10-22.2

> The first time you open the Today page, the app shows the key points and the full text of this consent form. Your health data is processed only after you tick "I have read and agree"; the system records the time and this version number. Signing on paper also works (the administrator records the date with `onboard.py --consent-at`). When the form is revised you are asked to confirm again.
> This is the English text of `consent.md`, clause by clause, with the same version number. This file and `docs/family-consent.en.md` must be identical (a test checks it); change both together with the Chinese text and update the version number.

I (the user of this account) agree that the developer (the person who maintains the Watch2Whoop server) opens an account in this app for me and processes my health data as described below.

## 1. What data is collected

- **Health data from the watch / phone**: heart rate, heart rate variability, sleep, steps, active energy, workouts, weight, and daily metrics such as respiratory rate, blood oxygen and wrist temperature, synced from Apple Health by the app. Only what I allow the app to read in Health is synced, and I can turn it off in the iPhone settings at any time.
- **ECGs and beat-to-beat intervals**: the ECG waveforms I record with the watch's ECG app, with Apple's classification (for example sinus rhythm, signs of AFib) and average heart rate; the beat-to-beat intervals (the time of each beat) the watch records each time it measures heart rate variability. Also read from Health by the app, and only after I allow it.
- **What I enter and log myself**: my profile (name, date of birth, sex, height, goal weight, time zone), food, workouts, journal, supplements, lab results, body events, and shopping lists, what I have at home, the supermarkets I connected, product prices and purchase records.
- **What the system calculates**: recovery, sleep score, strain, reminders and so on.
- Not actively collected: location, contacts, photos, data from other apps.

## 2. Who can see it
- **Me**: with my own account (the login link / QR code is given only to me and works like a key).
- **Other family members**: cannot see my data by default. Only after I explicitly agree (by joining a circle in the app, or by asking the administrator to set up "sharing") can the named people see the named content; I can cancel it at any time.
- **Circle sharing (Friends page)**: I can create a circle myself or join someone else's circle with an invite; if I neither create nor join one, nothing is shared through circles. When I join, I choose myself what the other members of that circle can see: scores (recovery, sleep, strain and so on), workouts and nutrition adherence (only whether each day's calories / protein met my goal, not what I ate) are ticked by default; detailed food, weight, lab results, supplements, journal and long-term memory are not. Circle members can see my name, the content I ticked, my leaderboard scores (measured against my own baseline) and the cheers I send them; an AI they connect can also read the part I share with them. I can change what I share or leave a circle at any time, and after I leave they **immediately** stop seeing it. Someone who signs up with my personal invite code is invited to join the circles I created; whether they join and what they share is their own choice. **ECGs are never shared through circles.**
- **ECGs**: **not included in sharing** by default; even with sharing set up, someone can see my ECGs only if I separately allow it.
- **The administrator**: as the person who maintains the server, can technically access the database.
- **AI assistant**: **off** by default. If I connect an AI myself under Me → AI settings in the app (for example Claude or ChatGPT), **the part of my data needed for an answer** is sent to the AI company I chose when it answers a question, and is handled under that company's terms. Nothing is sent if I do not connect one. For ECGs only a summary such as the classification and heart rate is sent, **never the waveform**.
- **Backup AI (optional)**: if I have not connected my own AI, I can use the backup AI the administrator provides. **The app asks me separately before the first use.** After I confirm, each question sends DeepSeek my message, basic profile (name, age, sex, height, time zone), long-term memory, and whatever of my own scores, sleep, workouts, food, weight, daily metrics such as respiratory rate / blood oxygen / wrist temperature, lab results, supplements, journal, reminders, shopping lists, what I have at home, the supermarkets I connected, product prices and purchase records the answer needs; **photos and screenshots I send, and frames taken from videos, are sent as well**, and handled under DeepSeek's terms. ECGs and beat-to-beat heart data are not sent, and other family members' data is not read. Nothing is sent if I do not confirm, and I can withdraw at any time under AI settings.
- **Voice input**: when I hold to talk in Jarvis (or send a video with sound), the audio is turned into text on the administrator's server and **is not sent to any third party**; it is deleted right after, no recording is kept, and the text goes to the AI only after I confirm or edit it.
- **Automatic feedback**: when the AI helping me finds a problem in the system (a bug) or hears about a feature I want, it writes it up as a feedback report for the administrator and tells me the number. A report only says what went wrong in the product; the server **removes health values, names, e-mail addresses, phone numbers and links** before the administrator sees it (in a daily e-mail digest); the administrator may turn the cleaned-up content into public development tasks, but never says who reported it. The original wording stays on the server only. Under Me → Feedback I can see my reports, withdraw ones not handled yet, or switch to "ask me first" / "off".

## 3. My rights

- **See and correct**: I can change my profile at any time under Me → Profile in the app; if other data is wrong, I can correct it myself or ask the administrator to.
- **Delete**: I can ask at any time for my account and all my data to be deleted. Live data is deleted at once; old copies in backups disappear within **6 months** as the backups rotate (backups keep 7 daily, 4 weekly and 6 monthly copies).
- **Withdraw consent**: I can withdraw this consent at any time, which has the same effect as asking for deletion.

## 4. Risks

- This is a health management app, not a medical device; scores and reminders are for reference only and do not replace a doctor's diagnosis.
- ECG and heart rhythm content **only shows Apple's results**; this system does not diagnose anything. Apple's ECG cannot detect a heart attack; if I feel unwell I should see a doctor promptly.
- The administrator does their best to keep things secure (HTTPS, separation by user, limits on failed logins, only the necessary endpoints exposed), but cannot guarantee that nothing ever goes wrong. If data leaks, the administrator will tell me as soon as possible.

---

I have read and understood the above and agree to my health data being processed this way (ticking "I have read and agree" in the app counts as agreeing).
