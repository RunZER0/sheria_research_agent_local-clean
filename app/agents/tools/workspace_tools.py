"""
Workspace tools — bridge between main ReAct agent and WorkspaceSubagent pool.
"""

from __future__ import annotations

from typing import Any

from app.agents.workspace_subagent import WorkspaceIndex, SubagentPool


async def handle_workspace_list_files(
    pool: SubagentPool,
    params: dict[str, Any],
) -> str:
    dir_path = params.get("dir_path")
    files = pool.index.list_files(dir_path)
    if not files:
        return "The workspace is empty or the directory does not exist."
    lines = [f"Found {len(files)} file(s):"]
    for f in sorted(files, key=lambda x: x.path):
        lines.append(f"  {f.path} ({f.kind}, {f.fingerprint.word_count if f.fingerprint else '?'}w)")
    return "\n".join(lines)


async def handle_workspace_search_files(
    pool: SubagentPool,
    params: dict[str, Any],
) -> str:
    query = params.get("query", "")
    if not query:
        return "Please provide a search query."
    results = pool.index.search(query)
    if not results:
        return f"No files found matching '{query}'."
    lines = [f"Search results for '{query}':"]
    for r in sorted(results, key=lambda x: x.path):
        lines.append(f"  {r.path} ({r.kind})")
    return "\n".join(lines)


async def handle_workspace_read_file(
    pool: SubagentPool,
    params: dict[str, Any],
) -> str:
    """Read a file contextually — first/last chars, content, or search within."""
    path = params.get("path", "")
    mode = params.get("mode", "full")  # "contextual" | "full" | "search"

    if not path:
        return "Please provide a file path."

    fe = pool.index.get_file(path)
    if not fe:
        return f"File not found: {path}"

    # Try to load content from disk if not already in the index
    if not fe.content:
        import os
        resolved = None
        # Compute project root from __file__: .../app/agents/tools/workspace_tools.py
        # Need to go up 4 dirs: tools -> agents -> app -> project_root
        _base = os.path.abspath(__file__)
        for _ in range(4):
            _base = os.path.dirname(_base)
        project_root = _base  # C:\Users\...\sheria_research_agent_local
        
        # Try candidate locations in priority order
        candidates = [
            path,                                                     # as-is
            os.path.join(os.getcwd(), path),                          # relative to CWD
            os.path.join(os.getcwd(), "..", path),                    # relative to CWD's parent
            os.path.join(project_root, path),                         # relative to project root
            os.path.join(project_root, "..", path),                   # one above project root
        ]
        for loc in candidates:
            loc = os.path.normpath(loc)
            if os.path.isfile(loc):
                try:
                    with open(loc, "r", encoding="utf-8", errors="replace") as fh:
                        fe.content = fh.read()
                    break
                except Exception:
                    pass

    if mode == "contextual":
        bites = pool.index.get_contextual_bites(path)
        if "error" in bites:
            return bites["error"]
        lines = [
            f"File: {bites['name']} ({bites['kind']}, {bites['word_count']} words)",
        ]
        if bites.get("has_toc") and bites.get("toc_lines"):
            lines.append("\nContent structure:")
            for l in bites["toc_lines"][:10]:
                lines.append(f"  • {l}")
        else:
            lines.append(f"\nFirst 200 characters:\n{bites['first_chars'][:200]}...")
            lines.append(f"\nLast 200 characters:\n{bites['last_chars'][:200]}...")
        if bites.get("key_entities"):
            lines.append(f"\nKey references: {', '.join(bites['key_entities'][:8])}")
        return "\n".join(lines)

    # Search within file content
    search = params.get("search", "")
    if search:
        results = pool.index.search_content(search, path)
        if not results:
            return f"No matches for '{search}' in {path}"
        lines = [f"Found {len(results)} match(es) for '{search}' in {path}:"]
        for r in results[:5]:
            lines.append(f"  ...{r['snippet']}...")
        return "\n".join(lines)

    # Full content
    if fe.content:
        max_chars = 10_000
        content = fe.content[:max_chars]
        if len(fe.content) > max_chars:
            content += f"\n\n... (truncated, file is {len(fe.content)} chars)"
        return f"File: {fe.name}\n\n```\n{content}\n```"
    return f"File {fe.name} has no readable content. The path '{path}' may be incorrect."


async def handle_workspace_index_summary(
    pool: SubagentPool,
    params: dict[str, Any],
) -> str:
    return pool.index.summary()


async def handle_workspace_delegate(
    pool: SubagentPool,
    params: dict[str, Any],
) -> str:
    """
    Delegate a focused task to a workspace subagent.
    The subagent will independently examine files and report back.
    """
    goal = params.get("goal", "")
    files = params.get("files")
    if not goal:
        return "Please provide a goal for the subagent."

    report = await pool.dispatch(goal=goal, files=files)
    lines = [
        f"Subagent Report: {report['goal'][:80]}",
        f"Status: {report['status']}",
        f"Files examined: {', '.join(report.get('files_examined', [])[:5]) or 'none'}",
        "",
        report.get("narrative", "No narrative produced."),
    ]
    return "\n".join(lines)
