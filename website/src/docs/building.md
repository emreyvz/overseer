---
title: Building the Desktop App
navTitle: Building
order: 6
intro: "How to build the Overseer desktop installer yourself, on Windows, macOS or Linux. This is separate from just running the app, which needs no build at all."
---

You do not need this page to use Overseer. `overseer.cmd` / `./overseer.sh` runs the whole thing from source, and Windows and Linux have one-click installers on the [releases page](https://github.com/emreyvz/overseer/releases/latest). Build it yourself when you want your own installer: on macOS, where no download is published, or when you are packaging a modified version.

## What you need

| Tool | Version | Why |
|---|---|---|
| [Node.js](https://nodejs.org) | 20 or newer | Builds the interface and packages the Electron shell |
| [uv](https://github.com/astral-sh/uv) | latest | Python toolchain, bundled into the installer |
| Git | any | The packaging step exports the backend with `git archive` |

Everything else is fetched by the build. You can only build for the operating system you are on: Electron packaging is not cross-platform, so a Windows installer has to be built on Windows, a `.dmg` on a Mac, and an `.AppImage` on Linux.

## The three steps

The build always follows the same shape, whatever the platform. From the repository root:

```bash
# 1. Interface
cd web
npm ci
npm run build

# 2. Backend + the uv binary, staged into the resources the installer ships
cd ..
mkdir -p web/resources/backend web/resources/bin
git archive --format=tar HEAD | tar -x -C web/resources/backend
rm -rf web/resources/backend/website web/resources/backend/demo \
       web/resources/backend/.github web/resources/backend/web/src \
       web/resources/backend/web/node_modules web/resources/backend/web/dist
mkdir -p web/resources/backend/web
cp -r web/dist web/resources/backend/web/dist
cp "$(command -v uv)" web/resources/bin/

# 3. Package
cd web
npx electron-builder
```

On Windows use PowerShell for the same sequence, or run those middle commands from Git Bash, which ships with Git for Windows and understands them as written.

The installer lands in `web/release/`. It is deliberately thin, a few hundred megabytes rather than several gigabytes, because PyTorch is not bundled: the app installs the runtime that matches your machine, CUDA or CPU or Apple Silicon, on its first launch.

<div class="callout"><div class="c-title">Quick local build</div><p>If you only want to try the packaged app on your own machine and do not care about a clean installer, <code>cd web &amp;&amp; npm run pack</code> builds the interface and packages in one step. It skips the staging above, so the result runs from your working tree rather than standing alone.</p></div>

## Windows

Produces `Overseer-<version>-Setup.exe`, an NSIS installer that installs per user and lets you choose the directory. No certificate is involved, so SmartScreen shows a "Windows protected your PC" notice on a fresh download; *More info* then *Run anyway* proceeds. A locally built installer that never travelled through a browser is not flagged.

```powershell
cd web
npx electron-builder --win
```

## Linux

Produces both an `.AppImage`, which runs anywhere once you `chmod +x` it, and a `.deb` for Debian and Ubuntu.

```bash
cd web
npx electron-builder --linux
```

The `.deb` needs `linux.maintainer` set in `web/package.json`; it is already there, and removing it makes the Debian package fail to build.

## macOS

No macOS installer is published, on purpose. An un-notarized download is blocked by Gatekeeper with *"'Overseer' is damaged and can't be opened"*, which reads like a corrupt file rather than the policy block it is, and notarization needs a paid Apple Developer ID. Building on your own Mac avoids the problem entirely: an app you built never gets the quarantine flag, so it opens normally.

```bash
cd web
npx electron-builder --mac                 # your own architecture
npx electron-builder --mac --arm64 --x64   # both, from an Apple Silicon Mac
```

This gives you a `.dmg` and a `.zip` in `web/release/`. The build is ad-hoc signed with the hardened runtime switched off, which is what lets the app run its bundled Python; both settings live in `build.mac` in `web/package.json` and should stay as they are unless you are signing with a real Developer ID.

<div class="callout warn"><div class="c-title">If you copy the app to another Mac</div><p>Moving the built app to a second machine over the network or a download re-introduces quarantine and the same false "damaged" message. Clear it once with <code>xattr -cr /Applications/Overseer.app</code>. Building on the machine you run it on needs no such step.</p></div>

Running from source on a Mac is unaffected by any of this and stays the simplest path:

```bash
chmod +x overseer.sh
./overseer.sh
```

## Release builds

Tagged releases are built by `.github/workflows/release.yml`, which runs the same three steps on a Windows and an Ubuntu runner, then attaches the installers to the GitHub Release as a draft. A version pushed as `v0.12.15` is stamped into the app automatically, so `web/package.json` does not have to be bumped in the same commit.

```bash
git tag v0.12.15
git push origin v0.12.15
```

<div class="callout"><div class="c-title">Next</div><p>See the <a href="{{ '/docs/project-structure/' | url }}">Project Structure</a> for what each of those directories holds.</p></div>
