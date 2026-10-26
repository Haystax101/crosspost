---
title: Privacy Policy
---

# Privacy Policy: crosspost

_Last updated: 26 October 2026_

**crosspost** is a personal tool run by George Hastings for his own social media
accounts. It is not offered to the public, and nobody else can sign in to it.

## What it accesses

When I connect my own accounts, the tool accesses:

- **TikTok** (scopes `user.info.basic`, `video.list`): my basic profile and the list of my
  videos (ID, creation time, caption, title, duration, share link).
- **Instagram** (`instagram_business_basic`, `instagram_business_content_publish`): my
  account ID, my existing posts' captions and links, and permission to publish Reels to
  my account.
- **Google** (YouTube upload and Drive): permission to upload videos to my YouTube
  channel, and access to my Google Drive folders where my own videos are stored.

## What it does with it

It copies videos I have already posted on TikTok to my own Instagram and YouTube
accounts, with the same caption. That is all.

## Storage and sharing

- Access tokens and a record of which videos have been processed are stored
  **encrypted** in my own Google Drive. The encryption key is kept in GitHub's encrypted
  secrets.
- Video files pass through my Google Drive and a short-lived GitHub Actions runner, and
  are deleted from the runner when each run ends.
- No data is sold, shared with third parties, used for advertising, or analysed. The only
  services involved are TikTok, Instagram (Meta), Google, and GitHub, as described above.

## Deleting data

I can revoke access at any time from TikTok, Instagram, and Google account settings, and
delete the stored state file from Drive. To request deletion of any data relating to you,
contact me via [georgehastings.me](https://georgehastings.me). In practice the tool only
ever holds my own data.

## Contact

Via [georgehastings.me](https://georgehastings.me).
