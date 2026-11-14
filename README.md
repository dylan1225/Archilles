# Margin

A math whiteboard with live, hint-first feedback from Gemini. The app is in [`whiteboard/`](whiteboard/).

Students can draw with a stylus, touch, or mouse, or type their working. After a 1.6-second pause, Gemini reads the board and checks each step, flagging the first clear mistake. The board includes an eraser, colors, stroke undo/redo, PNG export, practice problems, and browser autosave.

```sh
cd whiteboard
npm ci
cp .env.example .dev.vars
# Set GEMINI_API_KEY in .dev.vars (never commit this file).
npm run dev
```

Use Node.js 22.13+ (22 LTS recommended). The provided key is configured as a hosted runtime secret; local development needs its own `.dev.vars` file. Optional `GEMINI_MODEL` defaults to `gemini-3.5-flash-lite`, which was verified on incorrect and corrected sample boards.

```sh
npx tsc --noEmit
node --experimental-strip-types --test tests/review.test.ts
npm run build
```

Uses the existing [Signature Pad](https://github.com/szimek/signature_pad) drawing library and [Gemini image understanding](https://ai.google.dev/gemini-api/docs/image-understanding), with a server-side API proxy. No extra OCR service is required. Image snapshots and the problem are sent to Gemini only when a check runs; work is stored locally in the student's browser. Disable live feedback for manual checks. AI feedback is fallible and may need a clearer handwritten step.
