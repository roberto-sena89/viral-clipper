"""Reporting: clips.json plus a human readable clips.md summary."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import util


@dataclass
class ClipRecord:
    """Everything known about one produced clip."""

    index: int
    start: float
    end: float
    duration: float
    score: float
    meets_minimum: bool
    file: str = ""
    hook_terms: list[str] = field(default_factory=list)
    text: str = ""
    components: dict[str, float] = field(default_factory=dict)
    width: int = 0
    height: int = 0
    # The on-screen hook burned into this clip, when one was. Empty for a run
    # with no headline and for one where the model wrote none, so the markdown
    # and the JSON stay readable for installs that never enable the feature.
    headline: str = ""
    # Ready-to-paste tags for the platform. Not burned into the video: TikTok
    # and Reels take them as post metadata, and a hashtag drawn on the frame is
    # a hashtag the platform ignores.
    hashtags: str = ""

    @property
    def start_label(self) -> str:
        return util.fmt_clock(self.start)

    @property
    def end_label(self) -> str:
        return util.fmt_clock(self.end)


@dataclass
class RunReport:
    """Metadata about the run as a whole."""

    url: str
    title: str
    video_id: str
    uploader: str
    source_duration: float
    language: str
    model: str
    engine: str
    min_duration: float
    max_duration: float
    # Tolerancia acima de ``max_duration`` que a execucao permitiu. Zero
    # quando o run nao deixou passar do teto, e nesse caso a linha do
    # relatorio nem cita o assunto.
    max_duration_grace: float = 0.0
    clips: list[ClipRecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload.pop("clips", None)
        payload["clips"] = [
            {**asdict(clip), "start_label": clip.start_label, "end_label": clip.end_label}
            for clip in self.clips
        ]
        return payload


def write_json(report: RunReport, destination: str | Path) -> Path:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def write_markdown(report: RunReport, destination: str | Path) -> Path:
    """Write a short summary with a table of clips and their scores."""
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    window_line = (
        f"- Janela permitida: {report.min_duration:.0f}s a {report.max_duration:.0f}s"
    )
    if report.max_duration_grace > 0:
        window_line += (
            f" (ate {report.max_duration + report.max_duration_grace:.0f}s"
            f" para fechar o contexto)"
        )
    lines: list[str] = [
        f"# Clips: {report.title}",
        "",
        f"- Fonte: {report.url}",
        f"- Canal: {report.uploader or 'desconhecido'}",
        f"- Duracao do video: {util.fmt_clock(report.source_duration)}",
        f"- Idioma detectado: {report.language} (modelo {report.model})",
        f"- Motor de analise: {report.engine}",
        window_line,
        f"- Clips gerados: {len(report.clips)}",
        "",
        "| # | Inicio | Fim | Duracao | Score | Ganchos | Arquivo |",
        "| - | ------ | --- | ------- | ----- | ------- | ------- |",
    ]
    for clip in report.clips:
        terms = ", ".join(clip.hook_terms) if clip.hook_terms else "-"
        name = Path(clip.file).name if clip.file else "(nao renderizado)"
        lines.append(
            f"| {clip.index} | {clip.start_label} | {clip.end_label} | "
            f"{clip.duration:.1f}s | {clip.score:.1f} | {terms} | {name} |"
        )
    lines.append("")
    for clip in report.clips:
        lines.append(f"## Clip {clip.index} - score {clip.score:.1f}")
        lines.append("")
        lines.append(f"- Trecho: {clip.start_label} ate {clip.end_label} ({clip.duration:.1f}s)")
        if clip.file:
            lines.append(f"- Arquivo: {clip.file}")
        if clip.headline:
            lines.append(f"- Titulo no video: {clip.headline}")
        if clip.hashtags:
            lines.append(f"- Hashtags: {clip.hashtags}")
        if clip.hook_terms:
            lines.append(f"- Ganchos: {', '.join(clip.hook_terms)}")
        if clip.text:
            lines.append("")
            lines.append("> " + clip.text.replace("\n", " "))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def format_table(report: RunReport) -> str:
    """Compact console table, ASCII only so any terminal renders it."""
    if not report.clips:
        return "Nenhum clip gerado."
    rows = [
        ("#", "inicio", "fim", "dur", "score", "arquivo"),
    ]
    for clip in report.clips:
        rows.append(
            (
                str(clip.index),
                clip.start_label,
                clip.end_label,
                f"{clip.duration:.1f}s",
                f"{clip.score:.1f}",
                Path(clip.file).name if clip.file else "-",
            )
        )
    widths = [max(len(row[col]) for row in rows) for col in range(len(rows[0]))]
    output: list[str] = []
    for position, row in enumerate(rows):
        output.append("  ".join(cell.ljust(widths[col]) for col, cell in enumerate(row)))
        if position == 0:
            output.append("  ".join("-" * width for width in widths))
    return "\n".join(output)
