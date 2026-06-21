"""
Enhanced Workspace Subagent System — contextual, parallel, LLM-powered.

Architecture:
  MainAgent (Ynai Agent)
    │
    ├── calls SubagentPool with a task description
    │     │
    │     ├── SubagentInstance #1 (specific goal: read document A)
    │     ├── SubagentInstance #2 (specific goal: search files for topic X)
    │     └── SubagentInstance #3 (specific goal: scan judgment conclusions)
    │
    └── receives structured reports back from each subagent

Each subagent:
  - Has its own DeepSeek call (limited budget)
  - Has its own confined toolset (file operations only, no internet)
  - Runs its own mini ReAct loop (observe → decide → act → report)
  - Reports back to the main agent with narrative + structured data
  - Up to 3 concurrent instances
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime
from typing import Any, Callable, Coroutine

# ---------------------------------------------------------------------------
# Workspace Index — enhanced with file fingerprints
# ---------------------------------------------------------------------------

class FileFingerprint:
    """Lightweight fingerprint of a file for quick identification."""
    first_chars: str = ""       # First ~3000 chars
    last_chars: str = ""        # Last ~3000 chars
    has_toc: bool = False       # Whether the file appears to have a table of contents
    toc_lines: list[str] = []   # TOC lines if detected
    word_count: int = 0
    key_entities: list[str] = []  # Basic entity extraction (names, case refs)


class WorkspaceIndex:
    """In-memory index of workspace files with fingerprints for contextual reading."""

    def __init__(self, tree: list[dict] | None = None):
        self._files: dict[str, FileEntry] = {}
        self._directory_map: dict[str, list[str]] = {}
        if tree:
            self.build_index(tree)

    def build_index(self, tree: list[dict]) -> None:
        for entry in tree:
            self._walk(entry)

    def _walk(self, entry: dict, parent_path: str = "") -> None:
        name = entry.get("name", "")
        path = entry.get("path", "")
        kind = entry.get("kind", "")
        children = entry.get("children")
        content = entry.get("content", "") or ""
        size_label = entry.get("sizeLabel", "")

        if kind == "directory":
            self._directory_map[path] = []
            if children:
                for c in children:
                    self._walk(c, path)
        else:
            fe = FileEntry(
                name=name,
                path=path,
                kind=self._detect_kind(name),
                size_label=size_label,
                content=content,
            )
            fe.fingerprint = self._fingerprint(name, content)
            self._files[path] = fe
            parent = self._parent_dir(path)
            if parent not in self._directory_map:
                self._directory_map[parent] = []
            self._directory_map[parent].append(path)

    def _detect_kind(self, name: str) -> str:
        ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
        text_exts = {"txt", "md", "json", "py", "ts", "tsx", "js", "css", "html", "xml", "yaml", "yml", "csv", "ini", "cfg", "log"}
        if ext in text_exts:
            return "text"
        if ext in ("pdf",):
            return "pdf"
        if ext in ("docx", "doc"):
            return "docx"
        if ext in ("png", "jpg", "jpeg", "gif", "svg", "webp"):
            return "image"
        return "other"

    def _parent_dir(self, path: str) -> str:
        parts = path.replace("\\", "/").split("/")
        return "/".join(parts[:-1]) if len(parts) > 1 else ""

    def _fingerprint(self, name: str, content: str) -> FileFingerprint:
        fp = FileFingerprint()
        if content:
            fp.first_chars = content[:3000]
            fp.last_chars = content[-3000:]
            fp.word_count = len(content.split())
            # Detect possible TOC
            toc_candidates = re.findall(r'^(?:Table of Contents|Contents|\.{2,}|\d+\.\s+\w+.*?\.{2,}\s*\d+)', content, re.MULTILINE)
            if toc_candidates:
                fp.has_toc = True
                fp.toc_lines = [l.strip() for l in content.split('\n') if l.strip() and (l.strip()[0].isdigit() or '...' in l)][:20]
            # Extract potential entity references
            for pattern in [r'v\.\s+[\w\s]+', r'\[20\d{2}\] eKLR', r'Cause No\.?\s*\d+', r'Section\s+\d+', r'Cap\s+\d+']:
                matches = re.findall(pattern, content)
                fp.key_entities.extend(matches[:5])
        return fp

    def list_files(self, dir_path: str | None = None) -> list[FileEntry]:
        if dir_path is None:
            return list(self._files.values())
        paths = self._directory_map.get(dir_path, [])
        return [self._files[p] for p in paths if p in self._files]

    def search(self, query: str) -> list[FileEntry]:
        q = query.lower()
        results = []
        for fe in self._files.values():
            if q in fe.name.lower() or q in fe.path.lower():
                results.append(fe)
                continue
            if fe.content and q in fe.content.lower():
                results.append(fe)
        return results

    def search_content(self, query: str, path: str | None = None) -> list[dict]:
        """Search WITHIN file content. Returns snippets."""
        q = query.lower()
        results = []
        targets = [self._files[path]] if path and path in self._files else self._files.values()
        for fe in targets:
            if not fe.content:
                continue
            idx = 0
            snippet_count = 0
            while True:
                idx = fe.content.lower().find(q, idx)
                if idx == -1 or snippet_count >= 5:
                    break
                start = max(0, idx - 100)
                end = min(len(fe.content), idx + len(query) + 100)
                results.append({
                    "path": fe.path,
                    "name": fe.name,
                    "snippet": fe.content[start:end].strip(),
                    "position": idx,
                })
                snippet_count += 1
                idx = end
        return results

    def get_file(self, path: str) -> FileEntry | None:
        return self._files.get(path)

    def get_contextual_bites(self, path: str) -> dict:
        """Get contextual bites of a file: first/last N chars, metadata."""
        fe = self._files.get(path)
        if not fe:
            return {"error": f"File not found: {path}"}
        fp = fe.fingerprint
        return {
            "path": fe.path,
            "name": fe.name,
            "kind": fe.kind,
            "word_count": fp.word_count,
            "first_chars": fp.first_chars,
            "last_chars": fp.last_chars,
            "has_toc": fp.has_toc,
            "toc_lines": fp.toc_lines[:10] if fp.toc_lines else [],
            "key_entities": fp.key_entities[:10],
        }

    def summary(self) -> str:
        total = len(self._files)
        dirs = len(self._directory_map)
        lines = [f"Workspace Index: {total} files in {dirs} directories"]
        if total <= 30:
            lines.append("")
            for fe in sorted(self._files.values(), key=lambda x: x.path):
                fp = fe.fingerprint
                size = fp.word_count if fp.word_count > 0 else len(fe.content)
                entities = ", ".join(fp.key_entities[:3]) if fp.key_entities else ""
                lines.append(f"  {'📄' if fe.kind == 'text' else '📕'} {fe.path} ({size}w)")
                if entities:
                    lines.append(f"    ↳ {entities}")
        return "\n".join(lines)


class FileEntry:
    """A file entry with full content and fingerprint."""
    def __init__(self, name: str, path: str, kind: str = "text", size_label: str = "", content: str = ""):
        self.name = name
        self.path = path
        self.kind = kind
        self.size_label = size_label
        self.content = content
        self.fingerprint: FileFingerprint = FileFingerprint()


# ---------------------------------------------------------------------------
# Subagent Pool — manages up to 3 concurrent instances
# ---------------------------------------------------------------------------

class SubagentTask:
    """A task assigned to a subagent instance."""
    def __init__(self, goal: str, files: list[str] | None = None):
        self.goal = goal
        self.files = files or []
        self.result: dict | None = None
        self.status: str = "pending"  # pending | running | completed | failed
        self.narrative: str = ""
        self.log: list[str] = []


class SubagentInstance:
    """
    A single confined subagent with its own mini ReAct loop.
    Does NOT access the internet. Only uses workspace file operations.
    """
    MAX_TURNS = 5  # Very confined — just enough for a focused file operation

    def __init__(self, task: SubagentTask, index: WorkspaceIndex, llm_client: Any | None = None):
        self.task = task
        self.index = index
        self.llm = llm_client  # DeepSeekClient or None (for demo mode)
        self.turn_count = 0

    async def run(self) -> dict:
        """Execute the subagent's confined loop."""
        self.task.status = "running"
        self.task.log.append(f"[agent] Starting task: {self.task.goal[:100]}")

        # Step 1: Understand the goal and list relevant files
        relevant_files = self._find_relevant_files()
        self.task.log.append(f"[agent] Found {len(relevant_files)} relevant files")

        # Step 2: Read contextual bites of relevant files
        findings = []
        for f in relevant_files[:5]:  # Max 5 files per subagent task
            self.task.log.append(f"[agent] Reading contextual bites: {f.path}")
            bites = self.index.get_contextual_bites(f.path)
            if "error" not in bites:
                findings.append(bites)

        # Step 3: If LLM available, analyze; otherwise use heuristic
        if self.llm and self.task.goal.strip():
            analysis = await self._llm_analyze(findings)
        else:
            analysis = self._heuristic_analyze(findings)

        self.task.narrative = analysis.get("narrative", "")
        self.task.result = {
            "goal": self.task.goal,
            "files_examined": [f.get("path", "") for f in findings],
            "findings": findings[:5],
            "analysis": analysis.get("analysis", ""),
            "summary": analysis.get("summary", ""),
        }
        self.task.status = "completed"
        self.task.log.append("[agent] Task completed")
        return self._report()

    def _find_relevant_files(self) -> list[FileEntry]:
        """Use the task's goal and file names to find relevant files."""
        goal = self.task.goal.lower()
        all_files = self.index.list_files()

        # Score files by relevance (name match, path match, content hint)
        scored = []
        for fe in all_files:
            score = 0
            goal_words = set(goal.split())
            name_words = set(fe.name.lower().replace("_", " ").replace(".", " ").split())
            common = goal_words & name_words
            score += len(common) * 10
            if fe.kind == "text":
                score += 1  # Prefer text files as they're readable
            # Boost files in the specific directory mentioned in the goal
            for word in goal_words:
                if word in fe.path.lower():
                    score += 5
            scored.append((score, fe))

        scored.sort(key=lambda x: -x[0])
        return [fe for score, fe in scored if score > 0] or all_files[:5]

    def _heuristic_analyze(self, findings: list[dict]) -> dict:
        """Simple heuristic analysis when no LLM available."""
        lines = []
        for f in findings:
            entities = f.get("key_entities", [])
            entities_str = ", ".join(entities[:5]) if entities else "none detected"
            lines.append(f"- {f['name']}: {f.get('word_count', 0)} words, entities: {entities_str}")
            if f.get("toc_lines"):
                lines.append(f"  Content: {f['toc_lines'][0][:80]}")
            elif f.get("first_chars"):
                lines.append(f"  Opens with: {f['first_chars'][:120]}...")
        return {
            "narrative": f"Examined {len(findings)} file(s).\n" + "\n".join(lines),
            "analysis": "\n".join(lines),
            "summary": f"Found {len(findings)} relevant files matching the task.",
        }

    async def _llm_analyze(self, findings: list[dict]) -> dict:
        """Use the LLM to analyze findings."""
        prompt = f"""You are a workspace analysis subagent. Your goal:

{self.task.goal}

You have examined the following files. Analyze each one for relevance to the goal
and extract key information.

{json.dumps(findings, indent=2, default=str)}

Return your analysis as JSON with keys:
- "narrative": A concise narrative of what you found (1-3 sentences per file)
- "analysis": Detailed analysis text
- "summary": A one-line summary of findings
"""
        try:
            text = await self.llm.complete(prompt, max_tokens=2000)
            # Try to extract JSON from the response
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                return json.loads(match.group())
            return self._heuristic_analyze(findings)
        except Exception:
            return self._heuristic_analyze(findings)

    def _report(self) -> dict:
        """Produce the final report for the main agent."""
        return {
            "goal": self.task.goal,
            "status": self.task.status,
            "narrative": self.task.narrative,
            "files_examined": [f.get("path", "") for f in (self.task.result.get("findings", []) if self.task.result else [])],
            "summary": self.task.result.get("summary", "") if self.task.result else "",
            "log": self.task.log[-5:],  # Last 5 log entries
        }


class SubagentPool:
    """
    Manages up to 3 concurrent subagent instances.
    The main agent calls this with a task description, and the pool
    dispatches it to an available subagent.
    """

    def __init__(self, index: WorkspaceIndex, llm_client: Any | None = None):
        self.index = index
        self.llm = llm_client
        self._tasks: list[SubagentTask] = []
        self._max_concurrent = 3

    async def dispatch(self, goal: str, files: list[str] | None = None) -> dict:
        """
        Dispatch a task to the subagent pool.
        Returns the report once the subagent completes.
        """
        task = SubagentTask(goal=goal, files=files)

        # Wait if we already have 3 concurrent tasks
        while len(self._tasks) >= self._max_concurrent:
            # Clean up completed tasks
            self._tasks = [t for t in self._tasks if t.status == "running"]
            if len(self._tasks) >= self._max_concurrent:
                await asyncio.sleep(0.5)

        self._tasks.append(task)
        instance = SubagentInstance(task, self.index, self.llm)
        try:
            return await instance.run()
        except Exception as e:
            task.status = "failed"
            return {"goal": goal, "status": "failed", "error": str(e), "narrative": f"Failed: {e}"}

    def get_pool_status(self) -> list[dict]:
        """Get status of all subagent tasks."""
        return [
            {
                "goal": t.goal[:80],
                "status": t.status,
                "files_examined": len(t.files),
            }
            for t in self._tasks[-10:]  # Last 10 tasks
        ]


# ---------------------------------------------------------------------------
# Workspace Watcher — polls a directory and maintains a persistent index
# ---------------------------------------------------------------------------

_TEXT_EXTS = {"txt", "md", "json", "py", "js", "ts", "tsx", "jsx", "css", "html",
              "xml", "yaml", "yml", "csv", "ini", "cfg", "log", "sh", "bat",
              "ps1", "sql", "rb", "go", "java", "c", "cpp", "h", "hpp", "rs",
              "toml", "lock", "env", "gitignore", "editorconfig", "yaml"}


class WorkspaceWatcher:
    """
    Maintains a persistent WorkspaceIndex by scanning a directory on disk.

    Usage:
        watcher = WorkspaceWatcher("./test_folder")
        watcher.sync()                              # initial scan
        pool = watcher.pool                         # SubagentPool for the agent
        # in a background task:
        while True:
            await asyncio.sleep(3600)
            watcher.sync()
    """

    def __init__(self, root_path: str) -> None:
        self.root_path = os.path.normpath(root_path)
        self.index: WorkspaceIndex = WorkspaceIndex()
        self.pool: SubagentPool = SubagentPool(index=self.index)
        self._last_scan: str | None = None
        self._scan_count: int = 0

    def sync(self) -> None:
        """Scan the directory and rebuild the workspace index."""
        self.index = WorkspaceIndex()
        if not os.path.isdir(self.root_path):
            return

        tree = []
        for dirpath, dirnames, filenames in os.walk(self.root_path):
            rel_dir = os.path.relpath(dirpath, self.root_path).replace("\\", "/")
            # Sort consistently
            dirnames.sort()
            filenames.sort()
            for fname in filenames:
                full_path = os.path.join(dirpath, fname)
                rel_path = os.path.join(rel_dir, fname).replace("\\", "/") if rel_dir != "." else fname
                if os.path.isfile(full_path):
                    entry = self._build_file_entry(fname, rel_path, full_path)
                    tree.append(entry)
        self.index.build_index(tree)
        self.pool = SubagentPool(index=self.index)
        self._scan_count += 1
        self._last_scan = datetime.now().isoformat()

    def _build_file_entry(self, name: str, rel_path: str, full_path: str) -> dict:
        """Build a workspace tree entry for a single file."""
        ext = name.split(".")[-1].lower() if "." in name else ""
        is_text = ext in _TEXT_EXTS

        content = ""
        if is_text:
            try:
                stat = os.stat(full_path)
                # Only inline files up to 500KB to avoid memory issues
                if stat.st_size <= 500 * 1024:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                else:
                    # For larger files, read first 2000 chars as preview
                    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read(2000) + "\n\n[File truncated — " + self._format_size(stat.st_size) + " total]"
            except Exception:
                content = ""

        return {
            "name": name,
            "path": rel_path,
            "kind": "text" if is_text else ext,
            "content": content,
        }

    @staticmethod
    def _format_size(bytes_: int) -> str:
        if bytes_ < 1024:
            return f"{bytes_} B"
        if bytes_ < 1_048_576:
            return f"{bytes_ / 1024:.1f} KB"
        return f"{bytes_ / 1_048_576:.1f} MB"

    def summary(self) -> str:
        """Human-readable summary of what the watcher knows."""
        if not self.index:
            return f"Workspace: {self.root_path} (not scanned yet)"
        files = self.index.list_files()
        text_count = sum(1 for f in files if f.kind == "text")
        size_labels = [f.size_label for f in files if f.size_label]
        return (
            f"Workspace: {self.root_path} | "
            f"{len(files)} file(s), {text_count} text | "
            f"last scan: {self._last_scan or 'never'} | "
            f"scan #{self._scan_count}"
        )
