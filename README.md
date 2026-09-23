# viral-clipper

Turn one long video into short vertical clips that are at least `--min`
seconds long (30 s by default), with burned captions, ready to post as
Shorts / Reels / TikTok.

The tool analyses the audio, transcribes the speech, scores every candidate
window on hook quality, speech density, loudness, boundary cleanliness and
length, then renders the best non-overlapping windows in 9:16.

## Requirements

- Python 3.11 or newer (verified on 3.13).
- `ffmpeg` and `ffprobe` available on `PATH`.
- Python packages: `numpy`, `faster-whisper`, `yt-dlp`.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

The first run downloads the whisper model (`small` by default) from Hugging
Face, so it needs network access once.

Two features are optional and degrade cleanly when absent:

| Feature | Install | Without it |
| --- | --- | --- |
| `--layout focus` (face-guided crop) | `pip install "opencv-python-headless<5"` | falls back to a center crop |
| `--ranker llm` (semantic re-ranking) | nothing, it speaks HTTP | ranking stays heuristic |

The `<5` pin on OpenCV is deliberate: version 5 removed `CascadeClassifier` and
stopped shipping the Haar XMLs, leaving only `FaceDetectorYN`, which needs an
ONNX model fetched at runtime. `python reframe_check.py` reports which backend
is actually usable.

## Quick start

```powershell
python -m viralclipper "https://www.youtube.com/watch?v=VIDEO_ID" -n 5 --min 30 --max 60
```

Plan only, no rendering:

```powershell
python -m viralclipper "URL" --plan-only
```

Audio-only run (skip transcription, faster but blind to hooks):

```powershell
python -m viralclipper "URL" --engine audio
```

Bring your own transcript (skip whisper entirely):

```powershell
python -m viralclipper "URL" --transcript legenda.srt --plan-only
```

`--transcript` accepts SRT, WebVTT, the pasted YouTube caption panel (`0:05`
per line) or plain prose. In the web UI the pasted text is normalized
automatically: minutes sorted, repeated lines collapsed, broken fragments
rejoined, speech aligned in a column next to its minute. The audio track is
still downloaded — the scorer
needs it for loudness and silence — but the model stage, the slowest one,
disappears. Every run also writes `output/viral_report.md`: per-clip editorial
analysis with retention / comments / shares / controversy scores and a blended
viralization potential. Disable it with `--no-viral-report`.

Portuguese speech with a bigger model:

```powershell
python -m viralclipper "URL" --language pt --model medium
```

Refuse clips that are only "the best of a bad video":

```powershell
python -m viralclipper "URL" --min-score 45
```

Add a semantic pass over the best candidates:

```powershell
$env:OPENAI_API_KEY = "sk-..."
python -m viralclipper "URL" --ranker llm --ranker-model gpt-4o-mini
```

Any OpenAI-compatible endpoint works, including a local one:

```powershell
python -m viralclipper "URL" --ranker llm `
  --ranker-base-url http://localhost:11434/v1 --ranker-model llama3.1 --ranker-no-key
```

## How it works

1. `download.py` reads the metadata with `yt-dlp` and pulls only the audio
   track for analysis. Failures are translated into one actionable line
   ("This video is unavailable: ...", "Retry with `--cookies-from-browser`").
2. `audio.py` decodes it to mono 16 kHz PCM with `ffmpeg`, computes a per-frame
   loudness curve, derives an adaptive silence threshold and finds silences.
3. `transcribe.py` runs faster-whisper with word timestamps. Results are
   cached (see below), so re-running on the same audio with the same settings
   skips this stage entirely.
4. `score.py` groups words into sentence-like units, builds every unit run that
   fits `--min`..`--max` and scores it using `hooks.py` plus the audio signals.
5. `ranker.py` (optional) re-judges the best `--ranker-top-n` candidates with a
   language model and blends that verdict into the score.
6. `render.py` cuts each winning window, reframes it to 9:16, burns karaoke
   captions and normalizes loudness to `--lufs`.
7. `report.py` writes `clips.json` and `clips.md` next to the videos.

The minimum duration is a hard guarantee: if a jump cut would drop a clip below
`--min`, the jump cut is re-rendered without it.

## Scores are absolute

Every scoring signal maps to `[0, 1]` through a fixed, video-independent rule:
the hook score saturates with `1 - exp(-total)`, the ratios are ratios, the
flags are flags, and loudness is banded against fixed dB bounds.

That matters more than it sounds. An earlier version rescaled the signals
across the candidate windows of the *current* video, which made the final score
relative: the same window scored 0 among strong neighbours and 100 among weak
ones, so every video produced a "score 95" clip even when it contained nothing
worth cutting.

With absolute scores, `score` means the same thing in every video, which is
what makes `--min-score` a real quality gate and what makes the weight table in
`score.py` tunable at all. `tests/test_score.py::AbsoluteScoreTests` locks this
in: a window's score must not change when its neighbours do.

## Main options

| Option | Default | Meaning |
| --- | --- | --- |
| `-n`, `--count` | 5 | how many clips to produce |
| `--min` / `--max` / `--target` | 30 / 60 / 42 | duration limits in seconds |
| `--min-score` | 0 | absolute 0-100 gate; clips below it are dropped (0 = off) |
| `--min-gap` | 6 | silence kept between two accepted clips |
| `--engine` | hybrid | `hybrid`, `audio` or `transcript` |
| `--model` | small | faster-whisper checkpoint; steps down if it will not load |
| `--language` | auto | force a language, e.g. `pt` |
| `--download-mode` | sections | `sections` downloads only the chosen ranges |
| `--layout` | focus | `focus`, `center`, `blur` or `fit` |
| `--caption-style` | karaoke | `karaoke`, `block` or `none` |
| `--caption-preset` | karaoke | `karaoke`, `bold-box`, `minimal`, `neon`, `block-dark`, `mono` |
| `--font-size` | preset | empty means "use the preset's size"; a value overrides only that |
| `--caption-margin` | 640 | caption bottom margin in px; 640 clears the TikTok UI zone |
| `--highlight-color` | `&H0000FFFF` | karaoke/headline color, ASS format `&HAABBGGRR` |
| `--headline` | auto | burned opening headline; defaults to the clip's first words |
| `--headline-seconds` | 0 (off) | how long the hook title stays at the top; 0 = captions only |

### Caption presets

One flag, one finished look. `--caption-preset NAME` picks the font, size,
color, box and karaoke highlight together; any manual flag (`--font-size`,
`--caption-margin`, `--highlight-color`, `--words-per-line`, `--uppercase` /
`--no-uppercase`) overrides only that piece.

| Preset | Look | When it wins |
| --- | --- | --- |
| `karaoke` | white bold, thin dark outline, yellow word highlight | the proven default for every channel |
| `bold-box` | white text on a semi-opaque dark box | light or busy footage |
| `minimal` | sentence case, thin outline, no box | quiet, elegant channels |
| `neon` | green neon highlight, deep shadow | dark, cinematic footage |
| `block-dark` | opaque dark bar | bright highlights right under the text |
| `mono` | monospaced, green highlight | tech and developer channels |

Pick one and keep it — a fixed caption look is what makes a channel's clips
recognisable in the feed.
| `--progress-bar` | off | draw the watched-progress bar at the top of the frame |
| `--sidecar-captions` | off | write an `.srt` instead of burning captions |
| `--jump-cut` | off | remove silences inside the clip |
| `--threads` | 2 | ffmpeg thread limit per encode |
| `--workers` | 2 | clips rendered in parallel |
| `--cache-dir` | auto | transcript and rank cache directory |
| `--plan-only` | off | score and report without rendering |
| `--keep-temp` | off | keep the intermediate files |

Run `python -m viralclipper --help` for the full list.

### Why `--workers 2` and `--threads 2`

Every worker runs its own `libx264` encode, and `libx264` allocates its frame
buffers per thread. Defaulting to "one worker per core" on an 8-core machine
means eight simultaneous 1080x1920 encodes, which reliably runs an 8 GB box out
of memory — the `x264 [error]: malloc of size ... failed` message. Raise both
only if the machine has RAM to spare.

The render workers also inherit a capped BLAS/OpenMP thread pool
(`OPENBLAS_NUM_THREADS`, `OMP_NUM_THREADS`, `MKL_NUM_THREADS` set to `1`).
Without it each worker imports numpy and allocates scratch space sized from the
core count; on a memory-tight machine that allocation fails and the pool dies
with `BrokenProcessPool`, which the pipeline then recovers from by rendering
sequentially. Set these variables yourself to override the cap.

## Model loading and memory

Whisper checkpoints are loaded with a ladder fallback. If the configured model
cannot be loaded — the usual cause is `mkl_malloc: failed to allocate memory` on
a machine where the checkpoint does not fit — the loader retries with the next
smaller checkpoint (`large-v3` → `large-v2` → `large` → `medium` → `small` →
`base` → `tiny`) and reports which one it settled on. The transcript cache key
follows the checkpoint that actually loaded, so a `base` transcript is never
served to a run that asked for `small`.

`--engine hybrid` treats transcription as optional: if no checkpoint loads at
all, the run continues with audio-only scoring instead of aborting. You still
get clips, scored on loudness, speech density and boundaries, just without text
or captions. `--engine transcript` is the opposite contract — there,
transcription is the point, so a failure is fatal and reported as such.

On an 8 GB machine under load, `small` needs more contiguous memory than is
available. `base` loads in about a second and needs a fraction of the RAM; the
selection quality difference is real but smaller than losing the run.

## Throughput

Transcription is the bottleneck, so measure it before planning a batch:

```powershell
python bench_transcribe.py --wav analysis.wav --models small,base,tiny
python bench_transcribe.py --url "https://youtu.be/..." --models base,tiny
```

Each checkpoint runs in its own child process, because peak RSS only ever grows:
measuring several models in one process would report the first model's footprint
for all of them.

The number that matters is the **real-time factor** (RTF), audio seconds per wall
second. RTF below 1 means the machine cannot transcribe faster than playback, so
a batch will not fit in a day. Measured on this 8-core CPU with `int8`, on two
minutes of real speech:

| checkpoint | load | 120 s of audio | RTF | peak RSS |
| --- | --- | --- | --- | --- |
| `base` | 2.2 s | 226.2 s | 0.53 | 467 MB |
| `tiny` | 2.0 s | 126.4 s | 0.95 | 321 MB |

So `base` turns one hour of video into roughly 1 h 53 min of CPU, and a batch of
twenty 20-minute videos into about twelve hours. `tiny` is roughly twice as fast
and fits in 321 MB. **CPU transcription does not scale to a batch** — treat
`--engine audio` (which skips transcription entirely and still selects clips from
loudness and boundaries) or a hosted transcription API as the production path.

## Reframing

`--layout focus` samples nine frames from the clip, runs a face detector over
them and picks a single horizontal crop offset from the median face position.
The offset is static for the whole clip, which is deliberate: per-frame
tracking jitters, and a short clip usually holds one framing anyway. A
detection is only trusted when at least three of the nine frames agree, and the
crop is clamped so one bad detection cannot throw the subject out of frame.

The log line reports how many frames produced a detection, because
`centered at 50%` from nine confident hits and from three shaky ones look
identical in the output while only the first is actually tracking a subject:

```
[*] Face-guided crop centered at 54% of the frame width (faces in 6/9 sampled frames)
[*] No consistent face found in 1/9 sampled frames; using the center crop
```

The bundled backend is OpenCV's Haar cascade, which is fast and dependency-free
but only reliable on frontal faces. It is a poor fit for music videos, group
shots and profile angles — a subject that moves between scenes yields a median
near the frame center, which is a defensible answer and still a cropped face.
Install `opencv-python-headless<5` (see Requirements) or switch to
`--layout center` / `blur` / `fit` for that kind of footage. The detector is a
`FaceDetector` protocol, so a better backend can be dropped in without touching
the crop logic.

`center` is the plain geometric crop, `blur` puts the full frame over a blurred
background, and `fit` letterboxes it.

## Semantic ranker

The heuristic is a *recall* stage: regex hooks, speech density and loudness are
cheap enough to run over every candidate in a two-hour video, but they cannot
tell whether a window is a complete thought. `--ranker llm` is the *precision*
stage: the model scores each of the best `--ranker-top-n` windows on
self-containedness, hook strength, payoff, shareability and whether the clip
ends cleanly, and that verdict is blended into the score with
`--ranker-weight` (0.6 by default).

It is off by default, only ever called for the shortlist, and never fatal: a
transport or parsing failure leaves the heuristic scores untouched and logs a
warning. Verdicts are cached under `<cache-dir>/rank`, keyed by the window
text, the model and the prompt version, so re-running the same video while
tuning options costs nothing.

## Batch mode

The unit of work for a real production line is a batch, not a URL.

```powershell
python -m viralclipper --batch urls.txt -o output
python -m viralclipper --batch urls.txt -o output --retry-failed
```

`urls.txt` holds one URL per line; blank lines and `#` comments are ignored and
duplicates are dropped. State lives in a SQLite manifest
(`output/batch.sqlite3` by default, `--manifest` to move it):

- **A failure costs one job.** One dead video does not stop the other forty.
- **Re-running resumes.** Finished URLs are skipped, and a job stuck in
  `running` because the process was killed is recovered on the next run.
- **`--retry-failed`** puts the failures back in the queue.
- Each URL writes to `output/<video_id>/`, and `output/batch.json` exports the
  whole manifest for inspection.

## Transcript cache

Transcription is the slowest stage and the same video is often processed more
than once while tuning the selection options. The cache stores one JSON entry
per transcription under `<work-dir>/cache/transcripts` (override with
`--cache-dir`, disable with `--no-transcript-cache`).

The key covers the source id, the whisper model, language, beam size, VAD flag,
device, compute type and a digest of the analysed audio, so any change to those
settings produces a fresh transcription instead of a stale hit. Entries are
plain JSON, inspectable and safe to delete at any time.

```powershell
python -m viralclipper "URL" --cache-dir ./cache
python -m viralclipper "URL" --no-transcript-cache
```

When the engine falls back to `audio`, no cache entry is read or written,
because no transcription happens.

## Output

```
output/
  <video_id>_01_<slug>_<start>.mp4   # the clip
  clips.json                         # machine readable report
  clips.md                           # human readable summary
```

## Configuration file

`--config config.yaml` (or `.toml`) sets defaults for any option; CLI flags
still win.

```powershell
python -m viralclipper "URL" --config config.example.yaml
```

In TOML, a `[table]` header applies to every key that follows it, so a
`[whisper]` block placed mid-file silently swallows the rest of the settings.
The shipped example uses dotted keys (`whisper.model = "small"`) to avoid that
trap, and `tests/test_config_file.py` asserts both examples load with zero
warnings.

## Troubleshooting

- `Required binary 'ffmpeg' was not found on PATH.` Install ffmpeg and reopen
  the terminal.
- `This video is unavailable: ...` or an age gate: pass
  `--cookies-from-browser chrome` (or `firefox`, `edge`, ...).
- `x264 [error]: malloc of size ... failed`: lower `--workers` and `--threads`.
- `Could not load 'small' (mkl_malloc: failed to allocate memory); trying
  'base' instead`: expected on a memory-tight machine. The run continues on a
  smaller checkpoint; close other programs if you want the full size one.
- `Transcription unavailable (...); scoring on audio alone`: no checkpoint fit.
  Clips are still produced, but without text or captions. See the model loading
  section.
- `BrokenProcessPool`: a render worker died. The pipeline retries the clips
  sequentially, so this costs time rather than output. Lower `--workers`.
- `OpenCV is installed but ships no Haar cascade`: you have OpenCV 5. Install
  the 4.x line: `pip install "opencv-python-headless<5"`.
- `No window reached --min-score N`: the video genuinely has no clip above the
  floor. Lower `--min-score` or raise `--count`. If the transcript was lost
  (see the model loading section), audio-only scoring caps every window at
  ~47 — a gate above that is warned about as unreachable the moment
  transcription fails, and a gate just below it gets a headroom note.
- Clips shorter than expected: lower `--min` or omit `--jump-cut`.

## Web UI

An optional local interface that drives the same pipeline from a browser.
It needs nothing extra — stdlib only — and every option maps onto a CLI flag.

```powershell
web_viral_clipper.cmd           # atalho Windows (abre http://127.0.0.1:7755)
python web/server.py            # equivalente multiplataforma
```

The server serves `web/index.html` and exposes:

- `POST /run` — runs one URL. Body: `{"options": {...}, "plan_only": bool}`.
  Options use the same names as the config file. Returns the clips and the log
  lines, or `{"error": ...}`.
- `GET /status` — current queue and generated clips.
- `GET /clips/<relpath>` — serves a rendered clip from `output/`.

It listens on `127.0.0.1` only, so it is not reachable from outside the
machine. The UI degrades gracefully when the server is off: it shows the
equivalent CLI command in the log box instead of erroring.

## Tests

```powershell
python -m unittest discover -s tests -t .
```

331 tests, offline and fast (no ffmpeg, no yt-dlp, no whisper model). The
rendering, download and ranker paths are exercised through injected fakes, so
the suite never needs a network.

A fake can still lie about the process boundary, so the render tests also run the
task through a pickle round trip before handing it to the pool — that is what
catches a parent reading its own unmodified copy instead of the worker's result.

Four scripts go one level deeper against real binaries:

```powershell
python smoke_test.py --threads 2     # analysis, selection, render, report
python reframe_check.py              # frame extraction + the real detector
python cache_e2e_test.py --model tiny  # transcript cache round trip
python bench_transcribe.py --wav a.wav # throughput per checkpoint
```

`smoke_test.py` builds its own fixture with ffmpeg, fakes the transcript and
exercises every stage without network access. It prints `SMOKE OK` when
analysis, window selection, rendering (burned and sidecar captions, jump cut)
and reporting all succeeded.
