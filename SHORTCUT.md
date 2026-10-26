# iOS Shortcut: copy new TikToks to Google Drive

When you leave TikTok, this automation copies videos added to your camera roll in the
last 15 minutes to the Drive folder `tiktok-inbox`. It names each file after the moment
the video was saved, e.g. `tt-20261026T161500+0100.mov`, which is the stamp the
pipeline matches on.

## Before you start

1. **TikTok** saves your posted videos to the camera roll (setting described in the README).
2. **Google Drive app** is installed and signed in to the same Google account as the
   pipeline. In the **Files** app, go to **Browse → ⋯ → Edit** and make sure
   **Google Drive** is switched on.
3. The folder **`tiktok-inbox`** exists at the top level of My Drive. `python -m crosspost.auth google`
   creates it.

## Build the automation

Open **Shortcuts → Automation** and tap **+** (or **New Automation**).

1. Choose **App**.
   - **App**: tap **Choose** and pick **TikTok**.
   - Tick **Is Closed**, untick **Is Opened**.
   - Select **Run Immediately**. On older iOS versions, turn **Ask Before Running**
     **off** and confirm **Don't Ask**. Without this, the automation waits for a tap
     you'll never see.
   - Turn **Notify When Run** off if you don't want a banner each time.
   - Tap **Next**, then **New Blank Automation** (on older iOS, **Add Action**).

2. Add **Find Photos** (Photos category).
   - Tap **Add Filter** and set **Media Type** · **is** · **Video**.
   - Tap **Add Filter** again and set **Date Added** · **is in the last** · **15** · **minutes**.
     If your iOS has no *Date Added* filter, use **Creation Date**. If minutes aren't
     offered, use **1 hour**; re-copies are harmless.
   - **Sort by**: Date Added (or Creation Date), **Latest First**. Leave **Limit** off.

3. Add **Repeat with Each** and set its input to **Photos** (the output of Find Photos).
   Put the next three actions **inside** the repeat block:

   a. **Format Date**
      - Date: tap the field and choose **Repeat Item**, then tap the variable and pick
        **Creation Date**.
      - Date Format: **Custom**, with format string `yyyyMMdd'T'HHmmssZ` (copy it exactly,
        including the single quotes around the T).

   b. **Set Name**
      - Input: **Repeat Item**.
      - Name: type `tt-` then insert the **Formatted Date** variable, giving
        `tt-Formatted Date`.
      - Leave **Don't Include File Extension** off, so `.mov`/`.mp4` is kept.

   c. **Save File**
      - Input: **Renamed Item**.
      - Turn **Ask Where to Save** **off**.
      - **Destination**: tap it, then **Google Drive → tiktok-inbox**.
      - Turn **Overwrite If File Exists** **on**. Because the name is fixed per video,
        closing TikTok twice in 15 minutes overwrites the copy instead of duplicating it.

4. **End Repeat** is added automatically. Tap **Done**.

The finished automation looks like this:

```
When TikTok is closed  (Run Immediately)
  Find Photos where Media Type is Video and Date Added is in the last 15 minutes
  Repeat with each item in Photos
    Format Date: Repeat Item › Creation Date  →  yyyyMMdd'T'HHmmssZ
    Set Name of Repeat Item to  tt-<Formatted Date>
    Save Renamed Item to  Google Drive/tiktok-inbox  (overwrite on, don't ask)
  End Repeat
```

## Test it

1. Open the automation and tap **▶︎** (or long-press → **Run**) once by hand. The first
   run asks for permission to access **Photos** and **Google Drive**. Tap **Always
   Allow**. An automation can't show these prompts, so it silently fails until you've
   done this manual run.
2. Save any short video to Photos, open and close TikTok, and check
   `tiktok-inbox` in Drive after a minute.
3. For an end-to-end test, run `python -m crosspost.run --dry-run` and look for a
   `matched` line after your next TikTok post.

## Notes

- Copying the whole recent camera roll is intentional and harmless. The pipeline only
  posts a file that matches a TikTok post by time *and* duration. Anything else in the
  inbox is ignored. Delete strays whenever you like.
- iOS needs the phone unlocked with some network. Large videos upload in the background
  via the Drive app, and the job retries matching for up to 24 hours.
- If you post from TikTok and immediately lock the phone, the "Is Closed" trigger fires
  the next time you leave TikTok.
