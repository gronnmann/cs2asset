"""Regenerate the public CLI command and parameter reference."""

from pathlib import Path

from typer.main import get_command

from cs2asset.cli import app


def generate():
    lines = [
        "# CLI reference",
        "",
        "Generated from the CLI with `uv run python scripts/document_cli.py`.",
        "",
        "Run commands as `cs2asset ...` or `uvx cs2asset ...`. Global options precede the command.",
        "",
    ]

    def visit(command, name):
        lines.extend([f"## `{name}`", "", command.help or "", ""])
        if command.params:
            lines.extend(
                [
                    "| Argument / option | Type | Default | Description |",
                    "| --- | --- | --- | --- |",
                ]
            )
            for param in command.params:
                names = " / ".join(param.opts + getattr(param, "secondary_opts", []))
                default = "required" if param.required else str(param.default)
                description = getattr(param, "help", None) or ""
                lines.append(f"| `{names}` | {param.type.name} | `{default}` | {description} |")
            lines.append("")
        if name == "cs2asset import":
            lines.extend(
                [
                    "`--exposure` adjusts sky brightness in stops (−32 to 32); `--yaw` rotates the panorama in degrees.",
                    "`--auto-exposure` applies an additional exposure reduction when needed to fit the half-float",
                    "maximum of 65,504, preserving highlight ratios. The report records the adjustment and effective",
                    "exposure. Small negative radiance values (down to −0.001) are clamped to zero; larger negative",
                    "or non-finite values cause an error. Resized HDR intermediates retain full-float precision.",
                    "",
                    "```powershell",
                    "uvx cs2asset import https://polyhaven.com/a/valley_of_desolation --auto-exposure",
                    "```",
                    "",
                ]
            )
        if hasattr(command, "commands"):
            for child_name, child in sorted(command.commands.items()):
                if child.hidden:
                    continue
                visit(child, f"{name} {child_name}")

    visit(get_command(app), "cs2asset")
    target = Path(__file__).resolve().parents[1] / "docs/cli-reference.md"
    target.parent.mkdir(exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    generate()
