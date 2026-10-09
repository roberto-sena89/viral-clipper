# viral-clipper

Turn one long video into short vertical clips that are at least `--min`
seconds long (30 s by default), with burned captions, ready to post as
Shorts / Reels / TikTok.

The tool analyses the audio, transcribes the speech, scores every candidate
window on hook quality, speech density, loudness, boundary cleanliness and
length, then renders the best non-overlapping windows in 9:16.

Deeper docs live in [`docs/`](docs/README.md) — the module map and contracts
([`arquitetura.md`](docs/arquitetura.md)), the local panel
([`painel-web.md`](docs/painel-web.md)), the configuration surface
([`configuracao.md`](docs/configuracao.md)) and the curator prompt
([`curador.md`](docs/curador.md)).

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

| Feature                              | Install                                  | Without it                  |
| ------------------------------------ | ---------------------------------------- | --------------------------- |
| `--layout focus` (face-guided crop)  | `pip install "opencv-python-headless<5"` | falls back to a center crop |
| `--ranker llm` (semantic re-ranking) | nothing, it speaks HTTP                  | ranking stays heuristic     |

The `<5` pin on OpenCV is deliberate: version 5 removed `CascadeClassifier` and
stopped shipping the Haar XMLs, leaving only `FaceDetectorYN`, which needs an
ONNX model fetched at runtime. `python reframe_check.py` reports which backend
is actually usable.

## Tests

The suite runs on pytest from the same `.venv` the tool uses:

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

`pytest.ini` pins `testpaths = tests`, so collection never walks `_scratch/`
(the gitignored folder of one-off debug scripts) and a stale backup there
cannot abort the run. Verified on Python 3.13.14 with pytest 9.1.1.

1499 tests, offline and fast (no ffmpeg, no yt-dlp, no whisper model). The
rendering, download and ranker paths are exercised through injected fakes, so
the suite never needs a network.

A fake can still lie about the process boundary, so the render tests also run the
task through a pickle round trip before handing it to the pool — that is what
catches a parent reading its own unmodified copy instead of the worker's result.

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

Let the video decide how many clips it yields (the default):

```powershell
python -m viralclipper "URL"                  # -n auto
python -m viralclipper "URL" -n 5             # back to a fixed ceiling
python -m viralclipper "URL" --auto-margin 8  # stricter: fewer, better clips
```

In automatic mode the limit is not a number you calibrate but a floor relative
to the best clip of *that* video: every window within `--auto-margin` points of
the top one is kept. A podcast full of good moments yields dozens of clips, a
weak video yields a few, and nothing has to be re-tuned per channel.
`--auto-ceiling` is only a safety cap for a pathological video, not a target.

Add a semantic pass over the best candidates:

```powershell
$env:OPENAI_API_KEY = "sk-..."
python -m viralclipper "URL" --ranker llm --ranker-model gpt-4o-mini
```

Any OpenAI-compatible endpoint works, including a local one:

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
   fits `--min`..`--max` (plus `--max-grace` of tolerance) and scores it using
   `hooks.py` plus the audio signals.
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

| Option                         | Default      | Meaning                                                                                                |
| ------------------------------ | ------------ | ------------------------------------------------------------------------------------------------------ |
| `-n`, `--count`                | auto         | `auto` lets the video decide how many clips it yields; a number is a fixed ceiling                     |
| `--min` / `--max` / `--target` | 30 / 60 / 42 | duration limits in seconds                                                                             |
| `--auto-margin`                | 15           | auto mode: how many points below the video's best clip still counts as worth cutting                   |
| `--auto-ceiling`               | 200          | auto mode: safety cap, not a target                                                                    |
| `--max-grace`                  | 30           | seconds a clip may exceed `--max` to close its reasoning                                               |
| `--min-score`                  | 0            | absolute 0-100 gate; clips below it are dropped (0 = off)                                              |
| `--min-gap`                    | 6            | silence kept between two accepted clips                                                                |
| `--engine`                     | hybrid       | `hybrid`, `audio` or `transcript`                                                                      |
| `--model`                      | small        | faster-whisper checkpoint; steps down if it will not load                                              |
| `--language`                   | auto         | force a language, e.g. `pt`                                                                            |
| `--download-mode`              | sections     | `sections` downloads only the chosen ranges                                                            |
| `--metadata-language`          | `pt`         | language yt-dlp asks the site for; this is what keeps titles in pt-BR. Empty leaves the site's default |
| `--layout`                     | focus        | `focus`, `center`, `blur` or `fit`                                                                     |
| `--caption-style`              | karaoke      | `karaoke`, `block` or `none`                                                                           |
| `--caption-preset`             | karaoke      | one of the 21 presets below; `--caption-preset X` overrides `--caption-style`                          |
| `--font-size`                  | preset       | empty means "use the preset's size"; a value overrides only that                                       |
| `--caption-margin`             | 640          | caption bottom margin in px; 640 clears the TikTok UI zone                                             |
| `--highlight-color`            | `&H0000FFFF` | karaoke/headline color, ASS format `&HAABBGGRR`                                                        |
| `--headline`                   | auto         | burned opening headline; defaults to the clip's first words                                            |
| `--headline-seconds`           | 0 (off)      | how long the hook title stays at the top; 0 = captions only                                            |
| `--progress-bar`               | off          | draw the watched-progress bar at the top of the frame                                                  |
| `--sidecar-captions`           | off          | write an `.srt` instead of burning captions                                                            |
| `--jump-cut`                   | off          | remove silences inside the clip                                                                        |
| `--threads`                    | 2            | ffmpeg thread limit per encode                                                                         |
| `--workers`                    | 2            | clips rendered in parallel                                                                             |
| `--cache-dir`                  | auto         | transcript and rank cache directory                                                                    |
| `--plan-only`                  | off          | score and report without rendering                                                                     |
| `--template`                   | off          | a composition name (`split-card`) or a path to a `.toml`/`.yaml`                                       |
| `--variant-presets`            | off          | comma-separated presets; re-renders each clip once per preset                                          |
| `--variant-layouts`            | off          | comma-separated layouts; re-renders each clip once per layout                                          |
| `--list-templates`             | —            | print the built-in templates and exit                                                                  |
| `--describe-template`          | —            | print a template's resolved pixel bands and exit                                                       |
| `--keep-temp`                  | off          | keep the intermediate files                                                                            |

### Títulos em pt-BR

O YouTube localiza **tudo** o que devolve para o idioma do pedido. O mesmo
vídeo aparece como `LULA PERDEU CONTROLE do GOVERNO e MESSIAS e RODRIGUES estão
em GUERRA ABERTA` quando o pedido é em português e como `LULA HAS LOST CONTROL
OF THE GOVERNMENT and MESSIAS and RODRIGUES ARE IN AN OPEN WAR` quando não é —
e um vídeo de origem inglesa (`I Built A City To Save Kids From Illegal Labor`)
volta já traduzido (`Eu Construí Uma Cidade Pra Salvar Crianças do Trabalho
Infantil Ilegal`).


Como o título é o que o relatório, a listagem de arquivos e a página de scrap
mostram, o idioma é fixado uma vez e vale para toda chamada ao yt-dlp:
`--metadata-language pt` (o default) vira `--extractor-args youtube:lang=pt`
em cada invocação. Use `--metadata-language ""` (ou `metadata_language = ""`
no config) para deixar o padrão do site, e um `youtube:lang=...` passado em
`--ytdlp-arg` vence o default.

Uma armadilha embutida aqui: o yt-dlp **não** mescla dois `--extractor-args` do
mesmo extractor — o último substitui o primeiro. Passar o idioma como uma
segunda flag ao lado do `youtube:player_client=...` do usuário apagava o
idioma e os títulos voltavam em inglês, sem erro nenhum. Por isso a linguagem é
dobrada numa flag única, junto das outras chaves do usuário.

Os números viajam no mesmo payload, então a listagem localizada trunca as
contagens: o YouTube escreve `57 mi de visualizações` e o parser de sufixo do
yt-dlp conhece só `K`/`M`/`B`, ou seja, lê 57. Um vídeo de 57 milhões aparecia
como "57 views". Em modo perfil a mesma listagem é pedida **duas vezes** — uma
localizada, para os títulos, e uma em inglês, descartável, para os números — e
as contagens entram por `id`. Custa uma requisição; se ela falhar, a lista
continua funcionando, só com o número que o YouTube escreveu por extenso. Em
modo link nada disso acontece: os metadados do vídeo trazem a contagem exata.

A contagem é exibida no formato curto do pt-BR: `16`, `16k`, `16,5k`, `16mi`
(vírgula decimal e "mi" de milhão, não o "M" do inglês).

### Caption presets

One flag, one finished look. `--caption-preset NAME` picks the font, size,
color, box and karaoke highlight together; any manual flag (`--font-size`,
`--caption-margin`, `--highlight-color`, `--words-per-line`, `--uppercase` /
`--no-uppercase`) overrides only that piece.

37 presets ship. `--caption-preset` validates against the real list, so a typo
prints the choices instead of rendering something unexpected.

| Preset               | Look                                                   | When it wins                            |
| -------------------- | ------------------------------------------------------ | --------------------------------------- |
| `karaoke`            | white bold, thin dark outline, yellow word highlight   | the proven default for every channel    |
| `social`             | Arial Black, yellow pop, two words per line, safe zone | the TikTok/Reels default out of the box |
| `bold-box`           | white text on a semi-opaque dark box                   | light or busy footage                   |
| `minimal`            | sentence case, thin outline, no box                    | quiet, elegant channels                 |
| `neon`               | green neon highlight, deep shadow                      | dark, cinematic footage                 |
| `block-dark`         | opaque dark bar                                        | bright highlights right under the text  |
| `mono`               | monospaced, green highlight                            | tech and developer channels             |
| `fire`               | orange highlight                                       | energy and urgency                      |
| `magenta-pop`        | magenta highlight                                      | pop and irreverent                      |
| `cyan-pop`           | electric cyan highlight                                | modern and cool                         |
| `lime-hit`           | acid-lime highlight                                    | young, high-contrast                    |
| `blood`              | blood-red highlight, thick outline                     | drama and shock                         |
| `gold-box`           | gold highlight on dark box                             | authority and premium                   |
| `candy`              | white box, dark text, pink highlight                   | sweet and bright                        |
| `violet-vibe`        | violet highlight, deep shadow                          | creative and nocturnal                  |
| `ice-blue`           | ice-blue highlight on thin dark box                    | clean and technical                     |
| `sunset`             | warm orange highlight                                  | heat without shouting                   |
| `bubble`             | night-blue box, yellow highlight                       | talk and podcast                        |
| `ultra-impact`       | giant Impact, thick outline                            | maximum impact                          |
| `slim`               | narrow face, cyan highlight                            | compact and informative                 |
| `cobalt`             | dark box, cyan highlight                               | sharp corporate                         |
| `pop-box`            | yellow box, dark text, red highlight                   | maximally loud                          |
| `roboto-bold`        | Roboto, cyan highlight                                 | the YouTube/shorts standard             |
| `inter-bold`         | Inter, lime highlight                                  | top readability on small screens        |
| `poppins-bold`       | geometric Poppins, magenta highlight                   | friendly and round                      |
| `montserrat-bold`    | Montserrat, gold highlight                             | the TikTok look                         |
| `dm-sans`            | DM Sans, orange highlight                              | Swiss minimalism                        |
| `cabin-bold`         | open Cabin, light cyan highlight                       | friendly explainers                     |
| `verdana-bold`       | giant x-height Verdana, yellow highlight               | tiny screens and accessibility          |
| `trebuchet-bold`     | clean humanist Trebuchet, red highlight                | warm readability                        |
| `tahoma-bold`        | narrow Tahoma, cyan highlight                          | maximum words per line                  |
| `calibri-bold`       | modern Calibri, lime highlight                         | office and tutorials                    |
| `franklin-bold`      | condensed Franklin Gothic, fire highlight              | classic news density                    |
| `segoe-black`        | native Segoe UI, violet highlight                      | modern Windows feel                     |
| `helvetica-classic`  | the cinema yellow                                      | the classic film look                   |
| `merriweather-black` | editorial screen serif, gold highlight                 | slow-paced documentaries                |
| `arvo-bold`          | slab serif for the big screen, yellow highlight        | interviews and keynotes                 |

Windows system faces render out of the box; Roboto, Inter, Poppins,
Montserrat, DM Sans, Cabin, Helvetica, Merriweather and Arvo are free on
Google Fonts — install them or libass falls back to a system face and the
preview in the UI will not match the burn.

Pick one and keep it — a fixed caption look is what makes a channel's clips
recognisable in the feed.

### Templates

A preset answers "how do the captions look". A **template** answers "what does
the whole frame look like". A template is a stack of horizontal **zones** whose
height fractions sum to 1.0:

| Zone kind  | Draws                                                             |
| ---------- | ----------------------------------------------------------------- |
| `video`    | the clip itself, cropped or fitted into its band                  |
| `frame`    | a still **extracted from the clip** (`frame_at` seconds in)       |
| `image`    | a still from your own file (`source`), for a logo or channel card |
| `solid`    | a flat colour band                                                |
| `text`     | a flat colour band with **words burned into it**                  |
| `captions` | the caption band (positioned by libass; carries fraction 0)       |

`image` is the one zone that may be left **unset**. A gallery template opens
before you have the asset (the Meme's identity bar, the X card's tweet print),
and a band with no file degrades to the clip rather than failing the render — the
same policy a file that no longer exists gets, and the reason the wizard can offer
a format without inventing a path for it. The band stays, and so does the zone:
drop your card in when you have it.

Every band is resolved to pixels **on the yuv420p chroma grid**: band offsets,
band heights and the inner rectangles all come out even, and the pixel of
rounding error is taken out of the margins instead of out of the picture. That is
a correctness rule, not cosmetics — the filters that place and crop pixels
(`overlay`, `crop`) snap to that half-resolution grid, so a rectangle that starts
or ends on an odd row paints one row of the wrong colour at *every* boundary of
the stack, and a band with rounded corners whose inner height is odd fails the
render outright (`alphamerge` refuses two frames of different sizes). An odd
canvas cannot be tiled by even bands; there the odd pixel lands in the last band,
which is the one with nothing below it to bleed into.

A `text` zone is a band of its own, not an overlay: it takes its fraction like
any other zone, and the video band shrinks to make room. The plate comes from
the zone's `color` and the words are burned by libass — the same stage that
burns the captions, so the fade, the wrap and the preset's font come for free.
The anchor is resolved against the band's *inner* rectangle (after the margins)
and written as an absolute `\pos` + `\an`, so what `--describe-template` prints
is where the words land:

```toml
[[zones]]
kind = "text"
fraction = 0.16
color = "#000000"
text = "POV: você usou o formato de meme e VIRALIZOU com 3x mais!"
text_size = 0.038          # share of the frame HEIGHT
text_color = "#ffffff"
text_align = "center"      # left | center | right
text_valign = "middle"     # top | middle | bottom
text_dx = 0.0              # nudge, share of the frame WIDTH
text_dy = 0.0              # nudge, share of the frame HEIGHT
```

Built-ins ship with the tool:

```bash
python -m viralclipper --list-templates
python -m viralclipper --describe-template split-card
```

```toml
name = "meu-canal"
caption_preset = "neon"

[[zones]]
kind = "video"
fraction = 0.62

[[zones]]
kind = "frame"
fraction = 0.38
margin_left = 0.03
margin_right = 0.03
corner_radius = 0.035
```

```bash
python -m viralclipper "URL" --template ./templates/meu-canal.toml
```

The `frame` zone is why a branded split layout needs no asset files: the still
is grabbed from the clip itself. If the grab or the image is missing the render
degrades to the video instead of failing.

Beyond the zones, the file carries a few template-level keys:

```toml
name = "meme-pov"
caption_preset = "ultra-impact"
captions = false              # turn the burned captions OFF
headline_seconds = 3.0
headline_text = "VOCE USOU O FORMATO"   # empty = the clip's own opening

[[zones]]
kind = "text"
fraction = 0.16
band_dy = -0.02               # shifts this band up, frame fraction
```

`captions = false` is a field of its own rather than another preset, because
switching them off is not a look: it is `caption_style = "none"`, and
`caption_preset` keeps meaning the headline and the text bands, which are
different things from the caption. It is a **one-way key** — the template can
turn the captions off, never back on, because there is no value for "return to
whatever I had on the command line" and guessing `karaoke` would erase a
`--caption-style block` the user never asked to lose. The panel turns them back
on by omitting the key. Anything but `true`/`false` is refused, so
`captions = "sim"` is an error and not a `True`.

`band_dy` moves one band without dragging the others: `0` is the stacked
position, positive goes down, negative goes up, and the unit is a fraction of
the **frame** height (the page shows it as a percentage). What the band leaves
behind becomes template background, exactly like the margins already did.

**Variations.** One analysis pass, many deliverables — the whole point of
templating:

```bash
python -m viralclipper "URL" --template split-card \
  --variant-presets neon,fire,minimal --variant-layouts focus,blur
```


That is 3 x 2 = **6 renders per clip**, each named
`..._<stem>__neon-focus_000010.mp4`. Nothing is re-downloaded and nothing is
re-scored: the section download happens once per window and only the encode
repeats.

Templates are `.toml` files (see `templates/meme-pov.toml` and the
`### Templates` section above): write one by hand or by another tool and pass
it with `--template`. The engine reads it directly — there is no build step.

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

A degraded run is not a silent one. Two things happen:

1. **An allocation failure is retried once.** It is the one transcription
   failure worth retrying, because the cause is the machine's state at that
   instant (a concurrent build, a browser with fifty tabs), not the input. A
   corrupt wav or a missing checkpoint fails identically the second time, so
   those do not pay for a retry. The failure is classified by type
   (`TranscriptionOutOfMemory`, matched with `isinstance(exc, MemoryError)`),
   because the string markers alone did **not** match the real message —
   measured: `"Unable to allocate 241. MiB for an array with shape ..."` did not
   contain `"failed to allocate"`.
2. **The run exits 4**, not 0. Exit 0 made a caption-less run indistinguishable
   from a good one, and the practical cost was that a batch had no way to retry
   precisely the transient failure. Batch mode already treats `code != 0` as a
   failed, retryable job, so the 4 gets a retry for free.

| Exit code | Meaning                                                                                    |
| --------- | ------------------------------------------------------------------------------------------ |
| 0         | Success.                                                                                   |
| 1         | Runtime error (`ClipperError`); the message is in the log.                                 |
| 2         | Usage/argument error.                                                                      |
| 3         | A clip came out below the configured minimum duration.                                     |
| **4**     | **Degraded run: clips rendered with no burned captions, selection on audio energy alone.** |
| **5**     | **Output directory busy: another run holds it. Nothing broke — wait, then run again.**     |
| 130       | Interrupted by the user (`Ctrl-C`).                                                        |

Exit 4 is checked **before** exit 3: both can be true at once, and "degraded" is
the more actionable of the two — the short duration is a consequence of the
degraded selection, not an independent problem. It does **not** fire for
`--plan-only`, where nothing was rendered and so no caption was lost.

The scores are not subtly worse. Measured on the same video with the same
parameters: **68.3 / 68.1** with the transcript against **39.3 / 39.3** without
it, with `language` reading `nao transcrito` and every clip's `text` empty.

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

| checkpoint | load  | 120 s of audio | RTF  | peak RSS |
| ---------- | ----- | -------------- | ---- | -------- |
| `base`     | 2.2 s | 226.2 s        | 0.53 | 467 MB   |
| `tiny`     | 2.0 s | 126.4 s        | 0.95 | 321 MB   |

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

The shortlist is judged `--ranker-concurrency` windows at a time (6 by default).
The calls are independent of each other and each keeps its own cache key, so
running them at once changes only how long the stage takes, not what it
decides. This is the dial that matters for wall-clock time: with 24 windows and
one call per window, a serial loop pays 24 network latencies back to back.
Measured with a stub provider at 0.5 s per call, 24 serial calls cost 12.44 s of
clock for 12.00 s of pure waiting — the stage is essentially all latency, so it
scales linearly with whatever the model costs. `--ranker-concurrency 1`
restores the old strictly serial behaviour, and it is the setting to reach for
if your provider answers 429 under a burst.

## One run per output directory

Two processes writing to the same `output/` **cannot** coexist, and the failure
is silent rather than loud, for two reasons that compound:

- **The published clip name is deterministic** — `render_task.py` builds
  `{video_id}_{position:02d}_{stem}{variant}_{start:06d}.mp4`, with no run id in
  it. Two runs of the same video with the same options write the **same**
  `clips.json`, `clips.md`, `viral_report.md` and every `.mp4`.
- **`work_path()` is `output_dir/_work` for every run**, with no id either, so
  both processes also write the same `source_audio.webm` and `analysis.wav`.

Measured, launching two runs in parallel: one analysed **1127 s of a 793.5 s
video**, because it reused the audio the other process had just written. It
passed every guard downstream. And because publishing is an atomic rename, not
even a truncated file is left behind to denounce the collision — only the wrong
result.

So each `output_dir` is guarded by an exclusive OS lock, held from before the
first byte is written until the run ends. A second run **refuses** (exit 5)
instead of waiting: whoever called knows what to do with the message, while a
`sleep` until release is a hang with no explanation.


```
$ python -m viralclipper https://youtu.be/...
! Outra execucao ja esta usando output (provavelmente pid 1234). Duas execucoes
  no mesmo diretorio de saida escrevem os MESMOS arquivos ...
```

Why an OS lock and **not** a pid file: the normal way to stop a run here is to
kill it (`taskkill /T /F`, so no orphan `ffmpeg` is left). A pid-file lock goes
stale after **every** cancel, and the next run then dies on a ghost lock — worse
than having no lock at all. An OS lock is released by the kernel when the
process dies, kill included. The lock file is never deleted, deliberately:
removing it opens a classic inode race where two later runs each lock a
different file and both think they own the directory.

Two details that are load-bearing, both measured rather than assumed:

- **Every process locks the same byte offset.** `msvcrt.locking` locks from the
  file's *current position*; without pinning it, two processes lock different
  bytes and **both succeed** — a lock that does not lock. Measured: offsets 5
  and 10 held simultaneously, both granted.
- **That offset is high (4096), not 0.** A Windows byte-range lock also blocks
  *reads* of that range by other processes, so locking byte 0 would stop the
  loser from reading the holder's pid — the one thing that makes the message
  useful.

Batch mode locks the **parent** directory (that is where `batch.sqlite3` and
`batch.json` live) and each URL still gets its own `output_dir` and its own
lock. Because the batch is sequential, one lock on the parent is enough.

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
- `Failed to decrypt with DPAPI` on Chrome or Edge: that browser is on 127+ and
  seals each cookie with App-Bound Encryption, which yt-dlp cannot open. Export
  a `cookies.txt` with a browser extension and pass
  `--ytdlp-arg --cookies --ytdlp-arg C:\path\cookies.txt`. The web panel has a
  field for the same path. Note that **no export carries `sessionid`** (it is
  `HttpOnly`): for Instagram profiles that one value has to be pasted by hand —
  see *`sessionid`: the one cookie an export cannot carry*.
- `... não tem o cookie 'sessionid'` from `--profile`: the jar is missing the
  HttpOnly cookie, which is the normal case for any export. Pass
  `--ig-session <valor>` (or fill the field on the scrap page); the message
  prints the DevTools path to get it.
- Instagram fails with `Unable to extract data` while everything else works:
  check that `curl_cffi` is installed (`pip install "curl_cffi>=0.7"`).
  Instagram rejects requests whose TLS fingerprint is not a browser's and
  answers with a bare HTTP 400 otherwise. Note that **profile** URLs are
  currently broken inside yt-dlp itself (`InstagramUserIE._WORKING = False`);
  a direct post/reel/video link works.
- A download fails with a generic extractor error and the log shows
  `Proxy map: {'https': 'http://127.0.0.1:...'}`: an ambient `HTTP_PROXY` was
  inherited from the host process. The tool strips those by default; set
  `VIRALCLIPPER_KEEP_PROXY=1` to pass them through, or point at a proxy
  explicitly with `--ytdlp-arg --proxy --ytdlp-arg <url>`.
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
- `GET /scrap` — the scrap page (`web/scrap.html`): expand a link or an account
  feed into a list of downloadable videos, pick one, and hand it to the
  clipper. `POST /scrap` runs the search; `GET /scrap/thumb?i=N` serves the
  thumbnail of the N-th row of the last search. The thumbnail is **proxied**
  rather than hot-linked, because Instagram and YouTube serve from CDNs whose
  signed URLs expire and whose responses the page's CSP would block anyway
  (`img-src 'self'`).

Two notes on the scrap page, both learned the hard way:

- On **Chrome/Edge 127+** the cookie picker cannot work at all: the browser
  seals every cookie with App-Bound Encryption (`v20`) and yt-dlp cannot read
  them. Export the cookies to a `cookies.txt` and point the *"Ou um arquivo
  cookies.txt"* field at it. That field takes precedence over the picker.
- **Instagram profiles are listed by this tool, not by yt-dlp.** The upstream
  extractor is disabled (`InstagramUserIE._WORKING = False`, its `sharedData`
  parser no longer matches the page), so the panel and `--profile` reproduce the
  `POST /api/graphql` the site's own React app makes — see
  `viralclipper/ig_profile.py`. Three consequences:
  - It needs a **logged-in session**. The GraphQL endpoint answers an anonymous
    visitor with the HTML shell instead of JSON, so the jar must carry
    `sessionid`. That cookie is `HttpOnly`, which means no browser export can
    ever contain it — see the `sessionid` block below.
  - It needs **`curl_cffi`**. The body can be byte-for-byte what the browser
    sends and Instagram still answers `{"__ar":1,"error":1357054}`, because it
    fingerprints the TLS handshake and refuses a Python client before reading
    the request. `pip install "curl_cffi>=0.7"` — the same package, and the same
    wall, as the download path.
  - `doc_id` and `fb_dtsg` are build artefacts Instagram rotates with each
    bundle. When they go stale the call is refused; the error message names both
    possibilities rather than reporting an empty feed. `doc_id` is re-read from
    the bundle (the `PolarisProfilePostsQuery_instagramRelayOperation` module
    exports it) and `fb_dtsg` comes from the profile page itself.
  The media itself is still downloaded by yt-dlp from the single post/reel URL,
  which works. Profile mode also works for YouTube and TikTok.

#### `sessionid`: the one cookie an export cannot carry

`sessionid` is `HttpOnly`, so every exporter that reads cookies through the
page's JavaScript — extensions included — omits it, and the jar arrives
"complete" but unauthenticated. Get the value by hand once:


DevTools (`F12`) → *Application* → *Cookies* → `https://www.instagram.com` →
the `sessionid` row → copy the **Value** column.

Then either paste it into the *"sessionid do Instagram"* field on the scrap
page, or pass it on the command line:

```bash
python -m viralclipper --profile @somebody --cookies cookies.txt \
  --ig-session '12345678%3AAbCdEf%3A12%3AAY...'
```

Either way the value is written into `--cookies`, so it is pasted **once**: the
listing, the archive and the yt-dlp download all read the same jar afterwards.
Both the bare value and a whole `Cookie:` header are accepted — the paste is
normalised before it is stored, so a value DevTools shows percent-decoded still
goes on the wire in the encoded form the header needs.

### O catálogo do perfil: `reels/` e `posts/`

`--profile` percorre o feed inteiro e grava cada item na pasta do tipo dele:

```
output/
  reels/   DYpr3atCXr8 - Comenta "VIRAL" que te mando....mp4
  posts/   DZ8unfZtbTA - LINK NA BIO ....mp4
```

A pasta sai do `product_type` do listing, não do download. Um item de foto ou
carrossel de fotos é pulado **sem chamada de rede** — o listing já sabe que não
há vídeo, e perguntar ao yt-dlp custaria uma ida e volta por foto para ouvir
"no video in this post".

**Retomada pelo nome do arquivo.** O shortcode lidera o nome, então "já baixei"
é um `exists()`, não um manifesto: re-rodar o mesmo `--profile` baixa só o que
falta, e renomear a legenda à mão não faz o item voltar. Cada falha custa aquele
item, nunca a rodada.

**`.fragments/`** é onde o yt-dlp guarda os pedaços enquanto baixa. Fica fora da
pasta do arquivo de propósito: um pedaço se chama
`<nome>.<id-do-formato>.<ext>` e termina em `.mp4` igual ao resultado, então um
download interrompido entre o download e a mesclagem seria lido como "já existe"
— e o item ficaria pulado para sempre segurando meio download.

### Seleção em lote

Cada resultado tem um checkbox, e a barra acima da lista tem **Selecionar
todos** (que vira *Limpar seleção* quando tudo está marcado) e **Baixar
selecionados**, com um contador do tipo `3 de 20 selecionado(s)`. O botão de
baixar nasce desabilitado: sem seleção não há o que baixar, e o botão diz isso
antes do clique.

`POST /scrap/download` recebe `{items: [{url, id, title}], collection,
cookies_file}` — a lista que já está na tela, uma URL por item, e não um
catálogo. É por isso que funciona igual em YouTube, TikTok e Instagram, sem
depender de sessão; o `/scrap/archive` continua sendo o caminho do catálogo do
Instagram. Marcar para baixar é estado separado de **Usar** (que escolhe *um*
vídeo para cortar), e o card mostra os dois com cores diferentes.

O destino é `output/downloads/<título da lista>/` — uma pasta por busca, para
duas buscas não misturarem arquivos. Cada arquivo é nomeado
`<id> - <título>.mp4`: o id vem primeiro porque é o que uma segunda execução
reconhece, o título depois para a pasta ser legível. Um arquivo já em disco é
**pulado**, então repetir o download tenta só o que faltou — a mesma promessa do
arquivamento, com a mesma resumibilidade vinda do sistema de arquivos. Uma
falha custa aquele item, nunca o lote: o resumo volta com `baixados`, `ja tinha`
e `falhas`, e a lista de erros item por item.

It listens on `127.0.0.1` only, so it is not reachable from outside the
machine. The UI degrades gracefully when the server is off: it shows the
equivalent CLI command in the log box instead of erroring.

If port 7755 is taken, pass `--port`:

```powershell
python web/server.py --port 7756
```

## Checks against real binaries

Six scripts go one level deeper against real binaries:

```powershell
python smoke_test.py --threads 2     # analysis, selection, render, report
python reframe_check.py              # frame extraction + the real detector
python band_parity_check.py          # zone geometry, one rendered frame per layout
python cache_e2e_test.py --model tiny  # transcript cache round trip
python bench_transcribe.py --wav a.wav # throughput per checkpoint
python ig_session_check.py           # --ig-session -> jar -> Cookie header
```

`smoke_test.py` builds its own fixture with ffmpeg, fakes the transcript and
exercises every stage without network access. It prints `SMOKE OK` when
analysis, window selection, rendering (burned and sidecar captions, jump cut)
and reporting all succeeded.

`band_parity_check.py` builds the real composition graph for a handful of
layouts, renders one frame with four distinguishable colours (background, text
plate, clip, still) and compares it with the band plan row by row and column by
column. It exits non-zero on a single wrong pixel, so it is the check to run
after touching `plan_bands`.

`ig_session_check.py` runs `run_profile_mode` against a fake Instagram with
`urlopen` patched out, and asserts the pasted session reaches the `Cookie`
header of both the profile GET and the GraphQL POST, that the jar gains exactly
one `#HttpOnly_` line with every other line untouched, and that a second run
needs no paste at all. It prints one `[ok ]`/`[FALHA]` line per check and exits
non-zero on any failure.
