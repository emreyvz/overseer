# YouTube live streams — beating "confirm you're not a bot"

YouTube now gates extraction behind a bot check. The reliable, safe fix is to give
the app your **YouTube cookies** (NOT your password). Cookies stay on this machine.

## Steps (2 minutes)

1. In your browser (already signed in to YouTube), install a cookies exporter:
   - Chrome / Edge: **"Get cookies.txt LOCALLY"** extension
   - Firefox: **"cookies.txt"** extension
2. Open **https://www.youtube.com** (make sure you're logged in).
3. Click the extension → **Export** → it downloads a `cookies.txt`.
4. Save/rename that file to **exactly**:

   ```
   config/youtube_cookies.txt
   ```
   (i.e. the `config/youtube_cookies.txt` file inside your project folder)

5. Restart the app (or just reconnect the YouTube source). Done — the resolver
   picks it up automatically.

## Notes
- No password is ever stored or sent anywhere. Only session cookies are read,
  locally, by yt-dlp.
- Alternatively set the env var `OVERSEER_YT_COOKIES` to any cookies.txt path.
- Cookies expire after a while; if YouTube blocks again, re-export.
- Without cookies the app still tries alternate player clients and your installed
  browser's cookies automatically, but a datacenter/VPN IP is often blocked
  regardless — the exported cookies.txt is the dependable path.
