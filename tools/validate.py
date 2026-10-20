#!/usr/bin/env python3
"""Validate a dual Claude Code / Codex skills plugin before committing.

Checks, with the standard library only (PyYAML is used when available):

- every ``skills/<name>/SKILL.md`` has ``name`` (matching its folder, kebab-case,
  at most 64 characters) and ``description`` (at most 1,024 characters) in its
  frontmatter, and a body short enough to load cheaply;
- every skill has ``agents/openai.yaml`` for Codex, whose ``default_prompt``
  invokes the skill as ``$<name>``;
- relative Markdown links (and their ``#anchors``) resolve;
- the four manifests parse and agree on the plugin name, and the two plugin
  manifests agree on description, author, homepage, repository, license and
  keywords (Claude Code reads only ``.claude-plugin/plugin.json``, Codex only the
  root ``plugin.json``, so both exist and must not drift apart);
- a hooks file declared in ``.claude-plugin/plugin.json`` parses and its scripts
  compile and are executable;
- ``version`` in ``plugin.json`` was raised when anything under ``skills/`` or a
  manifest changed since ``--base`` (default ``HEAD``): Codex caches installs by
  version, so a forgotten bump leaves Codex users on the old copy;
- bundled Python and shell scripts compile;
- eval cases under ``evals/`` have a prompt and at least one grader;
- ``.agents/skills`` still points at ``../skills``;
- no tracked text file looks like it contains a credential.

Usage: ``python tools/validate.py [--base REF]`` from anywhere. Exit status 1 on errors.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
ALLOWED_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
SHARED_MANIFEST_FIELDS = ("description", "author", "homepage", "repository", "license", "keywords")
VERSIONED_PATHS = ("skills", "plugin.json", ".claude-plugin", ".agents/plugins")
SECRET_PATTERNS = {
    "AWS access key id": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "assigned secret": re.compile(
        r"(?i)\b(password|passwd|secret|token|api[_-]?key)\b\s*[:=]\s*['\"]?[A-Za-z0-9/+_\-]{12,}"
    ),
}
LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__"}

errors: list = []
warnings: list = []


def error(message: str) -> None:
    errors.append(message)


def warn(message: str) -> None:
    warnings.append(message)


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, universal_newlines=True, check=False)


def in_git_repo() -> bool:
    return git("rev-parse", "--is-inside-work-tree").stdout.strip() == "true"


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        error(f"{rel(path)}: {exc}")
        return None


def plugin_name() -> str:
    data = load_json(ROOT / "plugin.json") or {}
    return str(data.get("name", ""))


def load_yaml(text: str) -> dict:
    """Parse YAML with PyYAML if present, else a minimal ``key: value`` reader."""
    try:
        import yaml  # type: ignore[import-untyped]

        data = yaml.safe_load(text)
        return data if isinstance(data, dict) else {}
    except ImportError:
        data: dict = {}
        section = None
        for line in text.splitlines():
            match = re.match(r"^(\s*)([A-Za-z_-]+):\s*(.*)$", line)
            if not match:
                continue
            indent, key, value = match.groups()
            value = value.strip().strip("\"'")
            if not indent:
                section = key if not value else None
                data[key] = {} if not value else value
            elif section is not None and isinstance(data.get(section), dict):
                data[section][key] = value
        return data


def frontmatter(path: Path) -> tuple:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        error(f"{rel(path)}: missing YAML frontmatter")
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        error(f"{rel(path)}: unterminated frontmatter")
        return {}, text
    return load_yaml(text[4:end]), text[end + 4 :]


def check_skills() -> None:
    skills_dir = ROOT / "skills"
    skills = sorted(p for p in skills_dir.iterdir() if p.is_dir()) if skills_dir.is_dir() else []
    if not skills:
        error("skills/: no skills found")
    for skill in skills:
        skill_md = skill / "SKILL.md"
        if not skill_md.is_file():
            error(f"{rel(skill)}: missing SKILL.md")
            continue
        meta, body = frontmatter(skill_md)
        name = str(meta.get("name", ""))
        description = str(meta.get("description", ""))
        if name != skill.name:
            error(f"{rel(skill_md)}: name {name!r} must equal folder name {skill.name!r}")
        if not NAME_RE.match(name) or len(name) > 64:
            error(f"{rel(skill_md)}: name must be kebab-case and at most 64 characters")
        if not description:
            error(f"{rel(skill_md)}: description is required")
        elif len(description) > 1024:
            error(f"{rel(skill_md)}: description has {len(description)} characters (max 1,024)")
        elif len(description) > 700:
            warn(f"{rel(skill_md)}: description has {len(description)} characters; Codex lists every description within a small budget")
        for key in sorted(set(meta) - ALLOWED_KEYS):
            warn(f"{rel(skill_md)}: frontmatter key {key!r} is not part of the shared Agent Skills format")
        lines = body.count("\n")
        if lines > 500:
            error(f"{rel(skill_md)}: body has {lines} lines; move detail into references/")
        elif lines > 250:
            warn(f"{rel(skill_md)}: body has {lines} lines; consider moving detail into references/")

        openai = skill / "agents" / "openai.yaml"
        if not openai.is_file():
            error(f"{rel(skill)}: missing agents/openai.yaml (Codex metadata)")
        else:
            data = load_yaml(openai.read_text(encoding="utf-8"))
            interface = data.get("interface", {}) if isinstance(data.get("interface"), dict) else {}
            if not interface and "display_name" not in data:
                error(f"{rel(openai)}: missing interface block")
            prompt = str(interface.get("default_prompt", ""))
            if prompt and f"${name}" not in prompt:
                warn(f"{rel(openai)}: default_prompt should invoke ${name}")

        scripts = skill / "scripts"
        for script in sorted(scripts.glob("*")) if scripts.is_dir() else []:
            check_script(script)


def check_script(script: Path) -> None:
    if script.suffix == ".py":
        try:
            compile(script.read_text(encoding="utf-8"), str(script), "exec")
        except SyntaxError as exc:
            error(f"{rel(script)}:{exc.lineno}: {exc.msg}")
    elif script.suffix == ".sh":
        result = subprocess.run(["bash", "-n", str(script)], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, universal_newlines=True)
        if result.returncode:
            error(f"{rel(script)}: {result.stderr.strip()}")
        if not script.stat().st_mode & 0o111:
            warn(f"{rel(script)}: not executable")


def slug(heading: str) -> str:
    text = heading.strip().lower()
    text = re.sub(r"[`*~]", "", text)
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(path: Path) -> set:
    found: set = set()
    in_code = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_code = not in_code
        elif not in_code and line.startswith("#"):
            found.add(slug(line.lstrip("#")))
    return found


def markdown_files() -> list:
    found = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not (Path(dirpath) / d).is_symlink()]
        if "results" in Path(dirpath).parts and "evals" in Path(dirpath).parts:
            continue
        found.extend(Path(dirpath) / f for f in filenames if f.endswith(".md"))
    return sorted(found)


def check_links() -> None:
    for md in markdown_files():
        in_code = False
        for number, line in enumerate(md.read_text(encoding="utf-8").splitlines(), 1):
            if line.startswith("```"):
                in_code = not in_code
                continue
            if in_code:
                continue
            for target in LINK_RE.findall(line):
                if re.match(r"^[a-z]+:", target) or target.startswith("<"):
                    continue
                path_part, _, anchor = target.partition("#")
                dest = md if not path_part else (md.parent / path_part).resolve()
                if not dest.exists():
                    error(f"{rel(md)}:{number}: broken link {target}")
                elif anchor and dest.suffix == ".md" and anchor not in anchors(dest):
                    error(f"{rel(md)}:{number}: missing anchor #{anchor} in {dest.name}")


def check_manifests() -> None:
    plugin = plugin_name()
    if not plugin:
        error("plugin.json: missing 'name'")
    manifests = {
        "plugin.json": ("name",),
        ".claude-plugin/plugin.json": ("name",),
        ".claude-plugin/marketplace.json": ("name", "plugins"),
        ".agents/plugins/marketplace.json": ("name", "plugins"),
    }
    parsed = {}
    for path, keys in manifests.items():
        data = load_json(ROOT / path)
        if data is None:
            continue
        parsed[path] = data
        for key in keys:
            if key not in data:
                error(f"{path}: missing {key!r}")
        if data.get("name") != plugin:
            error(f"{path}: name should be {plugin!r}")
        for entry in data.get("plugins", []):
            if entry.get("name") != plugin:
                error(f"{path}: plugin entry name should be {plugin!r}")
    root, claude = parsed.get("plugin.json"), parsed.get(".claude-plugin/plugin.json")
    if root and claude:
        for field in SHARED_MANIFEST_FIELDS:
            if root.get(field) != claude.get(field):
                error(f"plugin.json and .claude-plugin/plugin.json disagree on {field!r}; keep them identical")
    if root is not None and not re.fullmatch(r"\d+\.\d+\.\d+", str(root.get("version", ""))):
        error("plugin.json: version must be semantic (x.y.z); Codex caches installs by version")
    if claude is not None and "version" in claude:
        warn(".claude-plugin/plugin.json: a version pins Claude Code users until it changes; omit it to track commits")
    if claude is not None and isinstance(claude.get("hooks"), str):
        check_hooks(ROOT / claude["hooks"])


def check_hooks(hooks_file: Path) -> None:
    """A hooks file declared in the Claude manifest must parse, and its scripts must run."""
    if not hooks_file.is_file():
        error(f".claude-plugin/plugin.json: hooks file {rel(hooks_file)} does not exist")
        return
    if load_json(hooks_file) is None:
        return
    for script in sorted(hooks_file.parent.glob("*")):
        if script.suffix in (".py", ".sh"):
            check_script(script)
            if not script.stat().st_mode & 0o111:
                error(f"{rel(script)}: hook script must be executable")


def version_tuple(text: str) -> tuple:
    return tuple(int(part) for part in text.split("."))


def check_version_bump(base: str) -> None:
    if not in_git_repo():
        warn("not a git repository: skipped the version-bump check")
        return
    if git("rev-parse", "--verify", "-q", base + "^{commit}").returncode != 0:
        if base != "HEAD":
            warn(f"base {base!r} is not a commit here: skipped the version-bump check")
        return
    changed = git("diff", "--name-only", base, "--", *VERSIONED_PATHS).stdout.split()
    changed += git("ls-files", "--others", "--exclude-standard", "--", *VERSIONED_PATHS).stdout.split()
    if not changed:
        return
    old_text = git("show", f"{base}:plugin.json")
    if old_text.returncode != 0:
        return  # plugin.json is new: nothing to compare against
    try:
        old = str(json.loads(old_text.stdout).get("version", ""))
        new = str(json.loads((ROOT / "plugin.json").read_text(encoding="utf-8")).get("version", ""))
        if version_tuple(new) > version_tuple(old):
            return
    except (ValueError, json.JSONDecodeError):
        return  # reported by check_manifests
    shown = ", ".join(sorted(set(changed))[:5]) + (", ..." if len(set(changed)) > 5 else "")
    error(f"plugin.json: version is still {old} but {shown} changed since {base}; "
          "raise it so Codex users get the update")


def check_evals() -> None:
    evals = ROOT / "evals"
    if not evals.is_dir():
        return
    cases = (p for p in evals.rglob("*")
             if p.is_dir() and ((p / "prompt.md").exists() or (p / "case.yaml").exists()))
    for case in sorted(cases):
        if "results" in case.relative_to(evals).parts:
            continue
        graders = list((case / "graders").glob("*.md")) if (case / "graders").is_dir() else []
        case_yaml = case / "case.yaml"
        yaml_text = case_yaml.read_text(encoding="utf-8") if case_yaml.exists() else ""
        if not graders and "graders:" not in yaml_text:
            error(f"{rel(case)}: eval case has no graders")
        match = re.search(r"scaffold_script:\s*['\"]?([^\s'\"]+)", yaml_text)
        if match:
            script = case / match.group(1)
            if not script.is_file():
                error(f"{rel(case_yaml)}: scaffold_script {match.group(1)} does not exist")
            else:
                check_script(script)


def check_symlink() -> None:
    link = ROOT / ".agents" / "skills"
    if not link.is_symlink() or Path(os.readlink(str(link))) != Path("../skills"):
        error(".agents/skills must be a symlink to ../skills")


def tracked_files() -> list:
    if in_git_repo():
        result = git("ls-files", "--cached", "--others", "--exclude-standard")
        return [ROOT / line for line in result.stdout.splitlines() if line]
    found = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not (Path(dirpath) / d).is_symlink()]
        found.extend(Path(dirpath) / f for f in filenames)
    return found


def check_secrets() -> None:
    for path in tracked_files():
        if path.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg", ".svg"} or not path.is_file():
            continue
        if path.name == ".env" or path.name.startswith(".env."):
            error(f"{rel(path)}: environment file must not be committed")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                error(f"{rel(path)}: possible {label}; remove it or rewrite the example")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate this plugin repository.")
    parser.add_argument("--base", default="HEAD",
                        help="git ref to compare against for the version-bump check (default: HEAD)")
    args = parser.parse_args(argv)
    check_skills()
    check_links()
    check_manifests()
    check_version_bump(args.base)
    check_evals()
    check_symlink()
    check_secrets()
    for message in warnings:
        print(f"warning: {message}")
    for message in errors:
        print(f"error: {message}")
    print(f"{len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
