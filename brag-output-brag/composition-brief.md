# Hyperframes Composition Brief: SkillVector

## Objective
Create a short, cinematic launch-style brag video for SkillVector, the AI-powered career-intelligence platform.

## Output
- Composition directory: `brag-output-brag/composition/`
- Rendered video: `brag-output-brag/brag.mp4`
- Format: landscape — 1920x1080
- Duration: 19 seconds

## Source Material
- Project root: `d:/python/SkillVector/SkillVector`
- Primary files read: `frontend/app/(landing)/page.tsx`, `_components/Hero.tsx`, `StatsBar.tsx`, `Features.tsx`, `HowItWorks.tsx`, `Manifesto.tsx`, `FinalCTA.tsx`, `components/market/InsightCard.tsx`, `frontend/app/globals.css`, `layout.tsx`, `README.md`
- Product name: SkillVector
- Tagline / strongest claim: "AI-Powered Career Intelligence" / "From zero to job-ready" / "Built for career transformation — not just course recommendations"
- Key UI / visual moment to recreate: the Generate Path flow → 3-layer RAG pipeline firing → the week-by-week phase roadmap → the anti-cheat video player
- Copy that must appear verbatim:
  - "YOU ASKED TO LEARN PYTHON."
  - "AI-POWERED CAREER INTELLIGENCE"
  - "Generate Path"
  - Phase names: "Python Foundations", "OOP + Data Structures", "Real-World Projects"
  - "5 articles · 5 videos"
  - "L0 cache", "L1 pgvector", "L2 live web"
  - "SEEK-SKIP FLAGGED"
  - Tagline: "FROM ZERO TO JOB-READY. PLEASE DON'T SKIP THE VIDEO."

## Creative Direction
- Tone preset: cinematic
- Creative direction: "over-engineered distributed-systems trailer for a career app"
- Interpretation: Full-bleed cinematic scenes, big display type, dramatic reveals and swells; dry, specific copy; an epic trailer read of a learning-path generator where every over-engineered beat is a real feature.
- Angle: A career app that runs advisory locks, a 5-layer cache, and an anti-cheat video player to tell you what to study next. Play it dead-straight and let the absurd specificity carry the humor.
- Hook (0–3s): "YOU ASKED TO LEARN PYTHON." slams in; sub: "One line in. A distributed system out."
- Outro / punchline: "SKILLVECTOR / AI-POWERED CAREER INTELLIGENCE / From zero to job-ready. Please don't skip the video."
- Avoid:
  - Generic SaaS language ("streamline", "empower")
  - Abstract filler / unrelated motion graphics
  - Redesigning the product's own visual identity (use its real palette, Syne type, grid + gradient aesthetic)

## Visual Identity
- Background: `#F8F7F4`
- Surface: `#FFFFFF` · border: `#E8E4F0`
- Text primary: `#0F0E17` · secondary: `#3D3A4E` · muted: `#7C7891`
- Accent: `#4F3FF0` · accent light: `#EAE7FD` · sage: `#2EC4A0`
- Gradient: 135deg `#4F3FF0` → `#2EC4A0` (used on the wordmark + key headlines)
- Display font: Syne 800 · Body: DM Sans · Mono: JetBrains Mono (self-contained; avoid external font CDN if it risks non-render — fall back to system stack for the mono/meta labels if needed)
- Visual references from the project: subtle 60px grid backdrop with radial indigo/sage glows; floating mono stat chips ("⚡ 2× faster", "✦ 98% accuracy", "↗ +340% ROI"); uppercase display headlines; pill-shaped mono eyebrow tags ("PRODUCTION RAG PIPELINE").

## Storyboard
Use `brag-output-brag/brag-plan.md` as the creative contract.

Scene summary:
1. Hook — 3.0s — "YOU ASKED TO LEARN PYTHON." headline slam + quiet sub-line over grid/glow canvas.
2. The Pipeline Fires — 4.2s — simulated app: role field types "Python Developer", "Generate Path" click, L0/L1/L2 layer cards snapping in, embeddings counter 0→1024-D.
3. The Roadmap — 4.2s — week-by-week path card with 3 phases revealing sequentially and a "5 articles · 5 videos" resource chip.
4. The Integrity System — 3.2s — anti-cheat video player: heartbeat dot, "legitimate watch time 34%", then SEEK-SKIP FLAGGED card.
5. Outro — 4.4s — SKILLVECTOR logo slam on the gradient, tagline, final dry punchline.
Total: 19.0s

## Audio
- Audio role: cinematic support with restrained dry accents
- Audio arc: low intro bed → swell with the hook → builds through pipeline and roadmap reveals → brief tension + dry comedy on the cheat flag → bell-and-swell logo payoff → fade under the punchline
- Music: `assets/music/happy-beats-business-moves-vol-12-by-ende-dot-app.mp3` (bed volume ~0.30; never above 0.5)
- Music treatment: gentle fade-in during scene 1; small swell under the hook; fuller swell under the logo; music fades by ~18.5s so the final bell rings
- Music cue guidance: bundled preset at `assets/music/cues/happy-beats-business-moves-vol-12-by-ende-dot-app.music-cues.json` (~110 BPM; beats ~0.55s apart). Strong cues in the 0–25s window: 8.74 / 10.93 / 13.11 / 17.47 / 18.56. Use 1–3 locks max: roadmap first-phase reveal near ~8.74; logo slam near ~17.47. Ignore cues that hurt readability.
- Audio-reactive treatment: subtle — use music RMS/bass to make the off-white background glow and the gradient headline presence breathe. No waveform or equalizer visuals.
- Audio-coupled moments:
  - Hook headline slam — short cinematic impact/announcement
  - Role field typing — per-character key ticks (randomized keyboard set)
  - Generate Path button — single click
  - L0/L1/L2 layer cards — three short card/reveal hits snapping to beats
  - Embeddings counter 0→1024-D — light count-up ticks
  - 3 phase rows — three card-slide hits on consecutive beats
  - "5 articles · 5 videos" chip — one final pop
  - Heartbeat dot — soft pulse ticks
  - SEEK-SKIP FLAGGED — one dry glitch/crack accent
  - Logo slam — impact bell + music swell; ring over the fade
- SFX selection guidance: prefer low/medium high-frequency-risk sounds for repeated and polished moments (impactSoft_medium family, interface clicks, casino card-slides). Reserve a single heavier bell for the hook and the logo. Keep 1 high-frequency glitch only for the cheat flag.
- SFX analysis guidance: `assets` SFX chosen from the bundled `/brag` library informed by `sfx-analysis.md`.
- Exact SFX choice: choose filenames, timestamps, density and volume after the visual animation timing is set.
- Audio files: music already copied to `assets/music/`; copy any selected SFX into `assets/sfx/`.

## Hyperframes Instructions
Load the Hyperframes domain skills — `hyperframes-core`, `hyperframes-animation`, `hyperframes-creative`, `hyperframes-keyframes`, `hyperframes-cli`. /brag is its own workflow: do not enter the hyperframes entry-point intent interview and do not route into its generic promo / launch-video workflow. Prefer native Hyperframes conventions.

Requirements:
- Show real product UI (role input → Generate Path → roadmap → anti-cheat player) — the flow is the centerpiece, not the landing page.
- Keep all text readable in the final render (headline ~0.9–1.6s holds; sub-lines per reading floor).
- Total duration 19s (15–25s ok).
- Include the music + SFX layer.
- Treat cue metadata as optional hints; readability and story win.
- Beat-lock at most 1–3 strong cues (roadmap ~8.74s, logo ~17.47s); snap sequential phase cards to consecutive beats; typed text with randomized keypress ticks.
- Run `hyperframes check` before render — that is the single gate.
