"""Package for the viral-clipper pipeline.

The pipeline turns a long YouTube video into short vertical clips that are at
least ``min_duration`` seconds long. It is split into small modules so each
stage can be used on its own:

``config``            run configuration dataclass
``util``              process helpers, logging, time formatting
``download``          yt-dlp wrappers (metadata, audio, sections)
``audio``             audio extraction and energy/silence analysis
``transcribe``        faster-whisper transcription with word timestamps
``transcript_cache``  on-disk reuse of previous transcriptions
``hooks``             hook word/pattern detection used for virality scoring
``score``             builds candidate windows and ranks them
``render``            ffmpeg rendering (9:16, burned captions, loudness)
``report``            writes clips.json and clips.md
``cli``               command line interface
"""

__version__ = "1.0.0"
