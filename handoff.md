# AI Handoff

This file is for the next AI assistant taking over this project.

## Project

- Project name: Hextra / Windows Tweaker
- Local repo: `C:\Users\johnj\Desktop\Programs\SunoTweaker`
- GitHub remote: `https://github.com/v0mj/Windows-Tweaker-Hextra.git`
- Main launcher: `Hexa.py`
- Main app source: `hextra/legacy.py`

## User Intent

- The user wants Hextra to become a fully offline, local, open-source Windows tweaker.
- Do not add server dependencies back.
- Do not upload design mockups or local preview files unless the user explicitly asks.
- Keep changes practical and working, not just visual.
- The user prefers a non-AI-looking, polished style.

## Current State

- Hextra is already converted to offline/local mode.
- Server-side functionality was removed from the tracked source.
- The app can run without account/server access.
- GitHub remote is configured.
- The latest UI work is local and not pushed yet.

## Recent Local UI Change

The user selected UI concept 8, named “Hologram Stack”, and asked to build it exactly into the tweaker.

Implemented locally in:

- `hextra/legacy.py`

Important added/changed areas:

- Hologram theme constants near the top of `hextra/legacy.py`
- `HologramSidebar`
- `HologramRing`
- `HologramMetricPanel`
- `HologramHeroTitle`
- `HologramOverviewPage`
- `Dashboard` now uses `HologramSidebar` and `HologramOverviewPage`
- Old dashboard titlebar is hidden for this layout
- Snow overlay is disabled on the dashboard for this layout
- Visible corner resize grip is hidden so it matches the HTML concept better
- The Hologram sidebar has been expanded after user feedback so all original tweak categories are reachable again.
- Verified categories: `Network`, `GPU`, `CPU`, `RAM`, `Input`, `FPS Boost`, `Debloat`, `Privacy`, `Power`, `Cleanup`, `Visual`, `Services`, `Roblox`, `FiveM`, `Valorant`, `CS2`, `Minecraft`, `Fortnite`, `Apex`

Preview image generated here:

- `C:\Users\johnj\Desktop\Hextra-UI-Concepts\previews\hextra-hologram-installed.png`

## Validation Already Done

These checks passed after the Hologram UI change:

```powershell
python -m py_compile "C:\Users\johnj\Desktop\Programs\SunoTweaker\hextra\legacy.py"
$env:QT_QPA_PLATFORM='offscreen'; python "C:\Users\johnj\Desktop\Programs\SunoTweaker\Hexa.py" --smoke-test
git -C "C:\Users\johnj\Desktop\Programs\SunoTweaker" diff --check
```

The real Windows preview was also rendered successfully with normal Windows fonts.

After the sidebar fix, a targeted navigation check also passed:

```powershell
# Instantiates Dashboard and verifies every CATEGORY_ORDER item exists in sidebar + page stack.
```

## Current Git Status Notes

Expected tracked change:

- `hextra/legacy.py`

Known untracked files were already present / scratch-like:

- `hextra/fluent_ui.py`
- `scratch_find_classes.py`
- `scratch_find_lines.py`
- `scratch_icons.py`
- `scratch_icons_gui.py`
- `scratch_uninstaller.py`
- `test_out.txt`
- `trace.py`

Do not delete these unless the user explicitly confirms.

## Important Instructions For Next AI

- Do not push to GitHub unless the user clearly asks.
- Do not commit unless the user clearly asks.
- Before changing code, check `git status --short`.
- Keep edits focused; avoid rewriting the full app.
- Prefer testing with the specific smoke test first.
- If changing UI, generate a local screenshot/preview before final response.
- Do not reintroduce online auth, update servers, telemetry, or license checks.
- If cleanup is requested, ask before deleting untracked files.

## Suggested Next Steps

If the user wants to continue improving Hextra:

- Make the Hologram layout responsive for smaller windows.
- Convert more secondary pages to the same Hologram visual style.
- Replace old mixed UI styles gradually, page by page.
- Add a proper local settings page for offline-only behavior.
- Package the app into a clean release build.
- Add a short open-source README section explaining offline mode.

## Communication Context

- The user often mixes German and English.
- Keep replies direct and casual.
- Avoid sounding overly corporate.
- The user cares about aesthetics, screenshots, GitHub appearance, and whether things really work.
