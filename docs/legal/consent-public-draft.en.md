# Watch2Whoop: consent to the use of health data

Version: 2026-10-31.draft

> **Draft, not active.** The consent in force is `docs/family-consent.en.md` (identical to `body_engine/src/body_engine/profile/consent.en.md`). Activating this draft needs the maintainer's approval: then the text (without this note) is copied into those files with a final version number, together with the Chinese `.md`. What changes is listed in `docs/legal/README.md`.

I (the user of this account) agree that the operator of this service (named in the Privacy Policy) processes my health data as described below.

## 1. What data is collected

- **Health data from the watch / phone**: heart rate, heart rate variability, sleep, steps, active energy, workouts, weight, and metrics such as respiratory rate, blood oxygen, wrist temperature, blood pressure and blood glucose, synced from Apple Health by the app. Only what I allow the app to read in Health is synced, and I can turn it off in the iPhone settings at any time.
- **ECGs and beat-to-beat intervals**: the ECG waveforms I record with the watch's ECG app, with Apple's classification (for example sinus rhythm, signs of AFib) and average heart rate; the beat-to-beat intervals (the time of each beat) the watch records each time it measures heart rate variability. Also read from Health by the app, and only after I allow it.
- **What I enter and log myself**: my profile (name, date of birth, sex, height, goal weight, time zone), food, workouts, journal, supplements, lab results, body events, and shopping lists, what I have at home, the supermarkets I connected, product prices and purchase records.
- **What the system calculates**: recovery, sleep score, strain, reminders and so on.
- **Sign-in information**: my e-mail address, or the identifier Apple gives when I sign in with Apple; so that this app's access at Apple can be revoked when I delete my account, Apple's refresh token is kept, encrypted.
- Not actively collected: location, contacts, my photo library (only the photos I pick and send), data from other apps.

## 2. Who can see it

- **Me**: with my own account.
- **Other people**: cannot see my data by default. Only after I explicitly agree (by joining a circle in the app, or by granting sharing separately) can the named people see the named content; I can cancel it at any time.
- **Circle sharing (Friends page)**: I can create a circle myself or join someone else's circle with an invite; if I neither create nor join one, nothing is shared through circles. When I join, I choose myself what the other members of that circle can see: scores (recovery, sleep, strain and so on), workouts and nutrition adherence (only whether each day's calories / protein met my goal, not what I ate) are ticked by default; detailed food, weight, lab results, supplements, journal and long-term memory are not. Circle members can see my name, my picture, the content I ticked, my leaderboard scores (measured against my own baseline), and the posts and cheers I send; an AI they connect can also read the part I share with them. I can change what I share or leave a circle at any time, and after I leave they **immediately** stop seeing it. **ECGs are never shared through circles.**
- **ECGs**: **not included in sharing** by default; even with sharing set up, someone can see my ECGs only if I separately allow it.
- **The operator**: as the maintainer of the server, can technically access the database, and uses that access only to run the service, find problems and handle my requests.
- **AI assistant**: **off** by default. If I connect an AI myself under Me → AI settings in the app (for example Claude or ChatGPT), **the part of my data needed for an answer** is sent to the AI company I chose when it answers a question, and is handled under that company's terms. Nothing is sent if I do not connect one. For ECGs only a summary such as the classification and heart rate is sent, **never the waveform or beat-to-beat data**.
- **Backup AI (optional)**: if I have not connected my own AI, I can use the backup AI the operator provides. **The app asks me separately before the first use.** After I confirm, each question sends DeepSeek (servers in China) my message, basic profile (name, age, sex, height, time zone), long-term memory, and whatever of my own scores, sleep, workouts, food, weight, daily metrics such as respiratory rate / blood oxygen / wrist temperature, lab results, supplements, journal, reminders, shopping lists, what I have at home, the supermarkets I connected, product prices and purchase records the answer needs; **photos and screenshots I send, and frames taken from videos, are sent as well**, and handled under DeepSeek's terms. ECGs and beat-to-beat heart data are not sent, and other people's data is not read. Nothing is sent if I do not confirm, and I can withdraw at any time under AI settings.
- **Voice input**: when I hold to talk in Jarvis (or send a video with sound), the audio is turned into text on my iPhone or on this service's server and **is not sent to any third party**; it is deleted right after, no recording is kept, and the text goes to the AI only after I confirm or edit it.
- **Automatic feedback**: when the AI helping me finds a problem in the system (a bug) or hears about a feature I want, it writes it up as a feedback report for the operator and tells me the number. A report only says what went wrong in the product; the server **removes health values, names, e-mail addresses, phone numbers and links** before the operator sees it; the operator may turn the cleaned-up content into public development tasks, but never says who reported it. The original wording stays on the server only. Under Me → Feedback I can see my reports, withdraw ones not handled yet, or switch to "ask me first" / "off".
- Health data is **never used for advertising or marketing, never sold**, and not used to train AI models.

## 3. My rights

- **See and correct**: I can change my profile at any time under Me → Profile in the app; other data I can correct myself or ask the operator to.
- **Export**: I can ask for a copy of my data through the contact in the Privacy Policy.
- **Delete**: I can delete my account and all my data at any time under Me → Delete account in the app; if I signed in with Apple, this app's access at Apple is revoked too. Live data is deleted at once; old copies in backups disappear within **6 months** as the backups rotate.
- **Withdraw consent**: I can withdraw this consent at any time, which has the same effect as deleting my account.
- I must be at least the minimum age the Terms of Use require.

## 4. Risks

- This is a health management app, not a medical device; scores and reminders are for reference only and do not replace a doctor's diagnosis. The AI's answers are AI-generated and may be wrong.
- ECG and heart rhythm content **only shows Apple's results**; this system does not diagnose anything. Apple's ECG cannot detect a heart attack; if I feel unwell I should see a doctor promptly.
- The operator does their best to keep things secure (HTTPS, separation by user, encrypted keys, limits on failed logins, only the necessary endpoints exposed), but cannot guarantee that nothing ever goes wrong. If data leaks, the operator will tell me as soon as possible.
- Where the data is stored and any transfers abroad are described in the Privacy Policy.

---

I have read and understood the above and agree to my health data being processed this way (tapping "Agree" in the app counts as agreeing).
