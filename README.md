# AEC Radar

Finds new public repositories that look like CAD/BIM tools (SketchUp, Revit, AutoCAD, IFC, browser CAD, vibe-coded clones) on GitHub, GitLab and Codeberg, and publishes them as an Atom feed.

**Feed URL:** https://blender3darchitect.github.io/aec-radar/feed.xml

## Setup (about 5 minutes)

1. Create a **public** repo named `aec-radar` under `blender3darchitect` (public keeps Actions and Pages free).
2. Upload these files, keeping the folder structure:
   ```
   .github/workflows/aec-radar.yml
   .gitignore
   config.json
   radar.py
   README.md
   ```
3. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
4. **Settings → Actions → General → Workflow permissions: Read and write permissions.** Save.
5. **Actions tab → AEC Radar → Run workflow.** The first run takes about a minute.
6. Open the feed URL above to check it, then add it to Feedbin.

After that it runs on its own every 6 hours.

## How it decides what to show

Each new repo gets a score. Only repos scoring **5 or more**, and showing at least some CAD/BIM relevance, reach the feed (change `min_score` in `config.json`).

| Signal | Points |
|---|---|
| Has a homepage / live demo | +3 |
| Mentions a brand or format (SketchUp, Revit, IFC, DXF…) | +2 |
| Any other CAD/BIM/architecture wording | +1 |
| Topics like `cad`, `bim`, `ifc`, `threejs` | +2 |
| Clone wording (SketchUp-style, push/pull, clean-room, alternative…) `[CLONE]` | +2 |
| Runs in the browser (WebGL, WASM, three.js…) `[WEB]` | +1 |
| AI signs (README wording, `CLAUDE.md`, `AGENTS.md`, `.cursorrules`…) | +1 |
| Pushed in the last 48 h | +1 |
| 5+ stars | +1 |
| Rising (created in last 30 days, 25+ stars) | +2 |
| No description | −2 |
| Name looks like homework/tutorial/test | −3 |
| Pirated-software lure ("pre-activated", "crack", "setup guide"…) | never shown |

Feed titles carry the score and tags, for example:
`★10 [DEMO][AEC][CLONE][RISING] igsova/SimpleCAD: Direct modeling for FreeCAD, in the spirit of SketchUp…`

Useful Feedbin Actions on this feed: notify on `[CLONE]`, star `[AI]` or `[DEMO]`, or star `[RISING]` for the "worth a video" list.

The first run publishes a backlog (about 100 items from the last 3 days plus 30 days of rising repos). After that, expect a handful of new items per run.

Repos below the threshold are re-checked on later runs while they're still within the 3-day window, so a project that adds a demo link or description a day later still gets picked up.

## Tuning

Everything lives in `config.json`:
- `github.queries`: one search per line (each is one API call; keep it under ~25).
- `window_days`: how far back "new" goes.
- `scoring.*_terms`: words that add or remove points.
- `gitlab.enabled` / `codeberg.enabled`: turn those sources off if they're noisy.

To reset and rebuild the feed from scratch, delete the `state/` folder and run the workflow.

## Notes

- GitHub search only sees a repo's name, description, topics and README. A project with an empty description and a cryptic name won't show up until it gets one.
- GitHub pauses scheduled workflows in repos with no activity for 60 days. The bot's state commits should count as activity, but if the feed ever stops updating, check the Actions tab and re-enable the workflow.
