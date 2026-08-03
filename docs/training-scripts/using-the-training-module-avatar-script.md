# Avatar script — "Using the Training Module" (TRN-CRS-00002)

Split into **24 clips of roughly 7 to 8 seconds each** (about 18 words per clip at a
normal 150 words-per-minute delivery), so no clip runs near the 10 second limit.

**Total runtime: about 3 minutes.**

Render them in order and stitch. Each clip is one or two complete sentences, so nothing
is cut mid-thought and a re-render of a single clip does not force a re-render of its
neighbours.

## Before you render

- **Keep framing, lighting and voice settings identical across all 24 clips.** They are
  stitched into one video and any drift shows at every join.
- **Do not paste the clip numbers or the word counts** — only the quoted line.
- `/training` is written as "slash training" so the avatar says it rather than spelling
  or skipping it.

## Coverage of the quiz

The course has three questions, one point each, pass mark 80% — so a learner needs
**3 out of 3**. Every answer is taught explicitly:

| Question | Taught in clips |
|---|---|
| After publishing, how do you fix a typo? → **Create a new draft version** | 19, 20, 21 |
| What does watch coverage measure? → **Seconds that played while the tab was visible** | 10, 11, 12 |
| Reordered procedure steps → **Material Change** | 22, 23, 24 |

Clips **10**, **20** and **24** each carry an answer on their own. If any clip is worth
re-recording for clarity, it is one of those three.

---

## THE CLIPS

**1** — 20 words
> This is a three minute tour of the training system. Where your courses live, and how to write one yourself.

**2** — 20 words
> Everything assigned to you is at slash training. It works on a phone, and you do not need Desk access.

**3** — 18 words
> So field crews and customer contacts open it exactly the way you do. There is nothing to install.

**4** — 18 words
> You are emailed when a course is assigned to you, and again as the due date gets close.

**5** — 20 words
> A course is a short list of lessons. Each lesson is a few blocks. Text, images, a PDF, or video.

**6** — 19 words
> Your place saves as you go. Close the tab, return on your phone, and you land where you stopped.

**7** — 17 words
> Required courses are assigned, have a due date, and go overdue. Optional ones sit in the library.

**8** — 19 words
> Sometimes Complete refuses. It always tells you exactly which gate is unmet, and by how much. Never just no.

**9** — 19 words
> There are four gates. Watch coverage, in video checkpoints, the end of lesson quiz, and a supervisor sign off.

**10** — 16 words
> Watch coverage measures one thing. Seconds of video that genuinely played while the tab was visible.

**11** — 17 words
> So skipping ahead credits you nothing. And leaving it running in a background tab credits nothing either.

**12** — 20 words
> What it cannot measure is attention. It measures time. That is exactly why we never report it on its own.

**13** — 16 words
> Your record always shows coverage, checkpoint accuracy and quiz score together. Never one of them alone.

**14** — 19 words
> A checkpoint interrupts the video just after the answer was explained. Get it wrong, it rewinds and asks again.

**15** — 19 words
> Run out of attempts and it explains, then lets you carry on. It costs you accuracy, not the course.

**16** — 20 words
> The quiz can usually be retaken and your best score counts. Fail it, and you are not shown the answers.

**17** — 19 words
> Now the authoring side. If you know how to do something well, you can write the course for it.

**18** — 18 words
> Open a Training Course and press Open Builder. Add lessons, drop in blocks, write a quiz. No code.

**19** — 18 words
> Here is what surprises everybody. You always edit a draft. Publishing turns that draft into the live version.

**20** — 21 words
> And leaves the course with no draft at all. So your next edit starts a fresh one. Press New Draft Version.

**21** — 18 words
> A published version can never be edited in place. Learners are reading it, right now, as it stands.

**22** — 20 words
> At publish you pick Minor Edit or Material Change. It is the only choice that touches people who already passed.

**23** — 16 words
> Minor Edit keeps every existing completion valid. Use it for a typo, or a clearer sentence.

**24** — 20 words
> Material Change supersedes every completion and raises a retake. If your change alters how the job is done, pick it.

---

## After you have the video

1. Upload it and register it as a **Training Video Asset**.
2. Attach it to a Video block on one of the lessons in `TRN-CRS-00002`.
3. Publish the draft (**Material Change** is the honest pick for a first publish, though
   with no completions yet either is harmless).
4. Assign it to yourself and take it at `/training`.

That run exercises the parts nothing has tested yet: signed-URL playback, HTTP range
requests, the two-clock watch sampler, and the coverage gate at 80%. Those are the last
unproven pieces of the module.

If you want to test checkpoints too, just after clip 11 is the natural place — ask "does
skipping ahead count towards coverage?" right after it, where the answer has just been
said out loud.
