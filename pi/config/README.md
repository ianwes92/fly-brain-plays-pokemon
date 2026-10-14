# Configuration (~/.config/flybrain/)

Everything lives in `~/.config/flybrain/`. Secret files are mode **0600** and never
committed. Files not listed here are runtime state the code creates itself (runs,
leaderboard, events, samples, title memo, ...).

## site.json — who runs this stream (not secret)

Copy `site.example.json` and edit. Each key can also be set as an environment
variable `FLYBRAIN_<KEY>`, e.g. `FLYBRAIN_TWITCH_CHANNEL`.

| key | meaning |
|---|---|
| twitch_channel | your Twitch channel login |
| twitch_broadcaster_id | your numeric Twitch user id |
| twitch_admin_ids | user ids allowed to use `!menu` (default: the broadcaster) |
| twitch_ext_client_id | the client id of your Twitch panel extension (optional) |
| youtube_handle | your YouTube @handle, without the @ (optional) |
| discord_invite | e.g. `discord.gg/xxxx` (optional) |

## Secrets — how each is created

| file | keys | how it is made |
|---|---|---|
| twitch.json | nick, channel, oauth, refresh_token, client_id, announce | Register a Twitch app (Client Type: Public), then `./flybrain.py twitch-login --client-id <id> --channel <channel>` and approve the code at twitch.tv/activate. Renewed automatically; if it is ever revoked, `play/twitch_relogin.py` posts a fresh approve link to Discord #fly-log. Set `"announce": true` to let the fly talk in chat. |
| youtube.json | client_id, client_secret, refresh_token, access_token, expires_at, logged_in_at, testing | A Google Cloud OAuth client (YouTube Data API v3), then `play/youtube_promo.py login`. Optional. |
| instagram.json | token, set_at, refreshed_at, expires_at, ig_user_id | A long-lived Instagram Graph token, stored with `play/ig_story.py set-token TOKEN` (then clear your shell history). Refreshed automatically. Optional. |
| threads.json | token, set_at, refreshed_at, expires_at, user_id, username, enabled | A Threads long-lived token: `play/threads_post.py set-token` (stdin), then `enable`. Refreshed weekly. Optional. |
| discord.json | webhooks.stream-status, webhooks.fly-progress, webhooks.fly-log, broadcaster_id, watcher_role_id, relay, events | Discord channel webhooks (Server Settings → Integrations → Webhooks). `fly-log` gets every alert, so set at least that one. |
| twitch_ext.json | secret | The base64 extension secret from the Twitch developer console: `play/ext_publisher.py set-secret` (stdin). Optional. |
| panel.json | token | Created automatically the first time the relay serves a `/panel/*` request (32 random bytes, urlsafe). Copy the `token` value to the OBS PC as `obs-pc/panel_token.txt`. The `/panel/*` endpoints answer only LAN addresses with `Authorization: Bearer <token>`. |

## Settings (not secret)

| file | keys |
|---|---|
| learning.json | recover_always (bool), tau_recover_s (seconds; how fast learned changes fade) |
| relay.json | match_audio (bool) |
