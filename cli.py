#!/usr/bin/env python3
"""CLI entry point for the divine-sense-agent."""
from __future__ import annotations

# ─── Force UTF-8 stdout/stderr on Windows ───────────────────────────────
# Windows console defaults to legacy codepages (cp950/big5 or cp437),
# which mis-decodes UTF-8 bytes from Qdrant / Ollama / OpenAI responses
# as mojibake. Reconfigure to UTF-8 before any other import.
import sys as _sys
try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    _sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass

# Load .env before any project-level imports.
import os as _os
from pathlib import Path as _Path

_env_path = _Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                _os.environ.setdefault(_k.strip(), _v.strip())

import argparse
import json
import sys
import time
from pathlib import Path

from src.credential_store import mask_password, mask_username


def _get_project_root() -> str:
    """Get project root (source or PyInstaller frozen build)."""
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).parent)
    return str(Path(__file__).resolve().parent)


def _setup_path():
    """Add project root to path only in source (non-frozen) mode."""
    if not getattr(sys, "frozen", False):
        sys.path.insert(0, _get_project_root())


def main():
    """Main CLI entry point."""
    _setup_path()

    parser = argparse.ArgumentParser(
        description="divine-sense-agent: Qdrant-backed RAG for local docs",
        add_help=False,
    )
    # v0.8.0+ MCP tool-calling controls. Defined on the global
    # parser AND injected into every subparser via ``parents=``
    # (so they work either before or after the command name).
    parser.add_argument(
        "--mcp-config", type=str, default="config/mcp.json",
        help="Path to MCP server config JSON (default: config/mcp.json). "
             "Pass an empty string to disable remote servers.",
    )
    parser.add_argument(
        "--no-tools", action="store_true",
        help="Disable tool-calling (force legacy one-shot RAG path).",
    )
    parser.add_argument(
        "-h", "--help", action="store_true",
        help="Show this help message and exit.",
    )

    # Same flags re-bound on each subparser via parents
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--mcp-config", type=str, default="config/mcp.json",
        help="Path to MCP server config JSON (default: config/mcp.json). "
             "Pass an empty string to disable remote servers.",
    )
    common.add_argument(
        "--no-tools", action="store_true",
        help="Disable tool-calling (force legacy one-shot RAG path).",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ingest
    ingest_parser = subparsers.add_parser(
        "ingest", parents=[common],
        help="Ingest files into Qdrant",
    )
    ingest_parser.add_argument("path", type=str, help="File or directory to ingest")
    ingest_parser.add_argument("--recursive", "-r", action="store_true",
                               help="Scan directories recursively")

    # search — accept multiple tokens so CJK args with spaces work
    search_parser = subparsers.add_parser(
        "search", parents=[common],
        help="Search ingested documents",
    )
    search_parser.add_argument("query", nargs="+",
                               help="Search query (multiple tokens joined with spaces)")
    search_parser.add_argument("--top-k", type=int, default=5, help="Number of results")
    search_parser.add_argument("--filename", type=str, help="Filter by filename")

    # ask — same multi-token handling as search
    ask_parser = subparsers.add_parser(
        "ask", parents=[common], help="Ask a question using LLM",
    )
    ask_parser.add_argument("question", nargs="+",
                            help="Your question (multiple tokens joined with spaces)")
    ask_parser.add_argument("--top-k", type=int, default=5,
                            help="Number of context documents")
    ask_parser.add_argument("--stream", action="store_true", help="Stream the response")
    ask_parser.add_argument(
        "--web-search", action="store_true",
        help="Augment local Qdrant context with Ollama Web Search results",
    )
    ask_parser.add_argument(
        "--web-max-results", type=int, default=5,
        help="Max web search hits to append (1-10, default 5)",
    )
    ask_parser.add_argument(
        "--lang", default=None,
        choices=["zh-TW", "zh-CN", "en", "ja"],
        help=(
            "Force LLM response language (v0.8.5+). "
            "Default (None) lets the LLM pick. Use zh-TW / zh-CN / en / ja "
            "to align with the user's UI locale."
        ),
    )

    # stats
    subparsers.add_parser(
        "stats", parents=[common],
        help="Show collection statistics",
    )

    # chat — interactive REPL with persistent session history
    chat_parser = subparsers.add_parser(
        "chat", parents=[common],
        help="Interactive chat REPL (auto-saves session history)",
    )
    chat_parser.add_argument("--session", "-s", type=str,
                             help="Resume an existing session ID")
    chat_parser.add_argument("--top-k", type=int, default=5,
                             help="Number of context documents")
    chat_parser.add_argument(
        "--web-search", action="store_true",
        help="Augment Qdrant context with Ollama Web Search (toggle /web per turn)",
    )
    chat_parser.add_argument(
        "--lang", default=None,
        choices=["zh-TW", "zh-CN", "en", "ja"],
        help=(
            "Force LLM response language (v0.8.5+). "
            "Default (None) lets the LLM pick; can also be flipped "
            "mid-session with the /lang command."
        ),
    )
    chat_parser.add_argument(
        "--web-max-results", type=int, default=5,
        help="Max web search hits to append (1-10, default 5)",
    )
    # sessions — list / show / delete
    sessions_parser = subparsers.add_parser(
        "sessions", parents=[common], help="Manage saved chat sessions",
    )
    sessions_sub = sessions_parser.add_subparsers(dest="sessions_cmd")
    sessions_sub.add_parser(
        "list", parents=[common], help="List all saved sessions",
    )
    sessions_show = sessions_sub.add_parser(
        "show", parents=[common],
        help="Show a session's messages",
    )
    sessions_show.add_argument("session_id", type=str, help="Session ID to display")
    sessions_del = sessions_sub.add_parser(
        "delete", parents=[common], help="Delete a session",
    )
    sessions_del.add_argument("session_id", type=str, help="Session ID to delete")
    # v0.9.103.5 — rename a session's title
    sessions_ren = sessions_sub.add_parser(
        "rename", parents=[common], help="Rename a session's title",
    )
    sessions_ren.add_argument("session_id", type=str, help="Session ID to rename")
    sessions_ren.add_argument(
        "title", type=str, nargs="+",
        help="New title for the session (words join with spaces)",
    )

    # ── schedules (v0.8.0+) — run prompts on cron / interval / one-shot ──
    schedules_parser = subparsers.add_parser(
        "schedules", parents=[common],
        help="Manage scheduled prompts (cron / interval / one-shot). "
             "Uses full tool surface (web_search, mcp_server, ...).",
    )
    schedules_sub = schedules_parser.add_subparsers(dest="schedules_cmd")
    schedules_sub.add_parser(
        "list", parents=[common], help="List all schedules",
    )
    schedules_show = schedules_sub.add_parser(
        "show", parents=[common], help="Show a single schedule",
    )
    schedules_show.add_argument("schedule_id", type=str)
    schedules_add = schedules_sub.add_parser(
        "add", parents=[common], help="Add a new schedule",
    )
    schedules_add.add_argument(
        "--name", required=True, type=str, help="Human-readable label",
    )
    schedules_add.add_argument(
        "--prompt", required=True, type=str,
        help="The prompt to send to the Agent on every fire",
    )
    schedules_add.add_argument(
        "--type", choices=["once_at", "cron", "interval_seconds", "interval"],
        default="interval_seconds",
        help="Schedule type (default: interval_seconds). "
             "'interval' is an alias for 'interval_seconds'.",
    )
    schedules_add.add_argument(
        "--once-at", dest="once_at", type=str, default=None,
        help="ISO-8601 datetime for once_at type (e.g. 2026-12-31T23:59:00)",
    )
    schedules_add.add_argument(
        "--cron", type=str, default=None,
        help="5-field cron expression for cron type (e.g. '0 9 * * *')",
    )
    schedules_add.add_argument(
        "--interval", type=int, default=None,
        help="Interval in seconds for interval_seconds type",
    )
    schedules_add.add_argument(
        "--web-search", dest="web_search", action="store_true",
        help="Pass use_web_search=True to chat_turn on every fire",
    )
    schedules_add.add_argument(
        "--disable-tools", dest="no_tools", action="store_true",
        help="Disable tool calling on every fire (legacy one-shot path)",
    )
    schedules_add.add_argument(
        "--web-max-results", type=int, default=5,
        help="Override web_max_results for this schedule",
    )
    schedules_add.add_argument(
        "--disabled", action="store_true",
        help="Create the schedule in disabled state",
    )
    # v0.8.7+: lifecycle caps. Both default to None (unlimited /
    # no expiry) so existing CLI invocations keep working.
    schedules_add.add_argument(
        "--max-fires", dest="max_fires", type=int, default=None,
        help=(
            "v0.8.7+: cap total fire count. Schedule auto-disables "
            "with status='exhausted' when fire_count reaches this."
        ),
    )
    schedules_add.add_argument(
        "--expires-at", dest="expires_at", type=str, default=None,
        help=(
            "v0.8.7+: ISO-8601 timestamp after which the schedule "
            "stops firing. status='expired' once now >= expires_at. "
            "Accepts the Z suffix."
        ),
    )
    # v0.9.111.0 — optional fire-time chat-model pin.
    schedules_add.add_argument(
        "--llm", dest="llm", type=str, default=None,
        help=(
            "v0.9.111.0: optional fire-time chat-model pin "
            "'provider:model' (openrouter | ollama; catalog-validated "
            "at add time, fail-closed). Omit = the default provider "
            "chain."
        ),
    )
    schedules_rm = schedules_sub.add_parser(
        "remove", parents=[common], help="Delete a schedule and its history",
    )
    schedules_rm.add_argument("schedule_id", type=str)
    schedules_enable = schedules_sub.add_parser(
        "enable", parents=[common], help="Enable a disabled schedule",
    )
    schedules_enable.add_argument("schedule_id", type=str)
    schedules_disable = schedules_sub.add_parser(
        "disable", parents=[common], help="Disable a schedule (keep config)",
    )
    schedules_disable.add_argument("schedule_id", type=str)
    schedules_run = schedules_sub.add_parser(
        "run-now", parents=[common],
        help="Fire a schedule immediately (ignore schedule timing)",
    )
    schedules_run.add_argument("schedule_id", type=str)
    schedules_history = schedules_sub.add_parser(
        "history", parents=[common],
        help="Show the last N fire records for a schedule",
    )
    schedules_history.add_argument("schedule_id", type=str)
    schedules_history.add_argument(
        "--limit", type=int, default=20,
        help="Max records to display (default 20)",
    )
    schedules_daemon = schedules_sub.add_parser(
        "daemon", parents=[common],
        help="Run the scheduler daemon (foreground; Ctrl-C to stop)",
    )

    # ── creds (v0.9.39.0+) — central account/credential store ──
    creds_parser = subparsers.add_parser(
        "creds", parents=[common],
        help="Manage the central credential store (account sets used "
             "by ONVIF devices and other services).",
    )
    creds_sub = creds_parser.add_subparsers(dest="creds_cmd")
    creds_list = creds_sub.add_parser(
        "list", parents=[common],
        help="List credential sets (masked output)",
    )
    creds_list.add_argument("--service", default="", type=str,
                            help="Filter by service tag (e.g. onvif)")
    creds_show = creds_sub.add_parser(
        "show", parents=[common], help="Show one credential set (masked)",
    )
    creds_show.add_argument("name", type=str)
    creds_add = creds_sub.add_parser(
        "add", parents=[common], help="Create / replace a credential set",
    )
    creds_add.add_argument("--name", required=True, type=str,
                           help="Unique credential-set name")
    creds_add.add_argument("--username", default="", type=str,
                           help="Account name (default empty)")
    creds_add.add_argument(
        "--password-env", dest="password_env", default="", type=str,
        help="Env var holding the password (RECOMMENDED — the store "
             "never holds the secret)",
    )
    creds_add.add_argument(
        "--password", default="", type=str,
        help="Plain password (stored base64-OBSCURED, not encrypted)",
    )
    creds_add.add_argument("--service", default="generic", type=str,
                           help="Service tag (e.g. onvif / generic)")
    creds_add.add_argument("--notes", default="", type=str)
    creds_add.add_argument("--overwrite", action="store_true",
                           help="Replace an existing set with this name")
    creds_rm = creds_sub.add_parser(
        "remove", parents=[common], help="Delete a credential set",
    )
    creds_rm.add_argument("name", type=str)
    creds_test = creds_sub.add_parser(
        "test", parents=[common],
        help="Resolve a credential set (masked) — shows which source "
             "would be used and whether the env var is set",
    )
    creds_test.add_argument("name", type=str)

    # ── todo (v0.8.8+) — multi-level plan / execute todo list ──
    todo_parser = subparsers.add_parser(
        "todo", parents=[common],
        help="Manage multi-level todo lists (plan / execute / track).",
    )
    todo_sub = todo_parser.add_subparsers(dest="todo_cmd")
    todo_list_p = todo_sub.add_parser(
        "list", parents=[common],
        help="List root tasks (or a subtree with --parent).",
    )
    todo_list_p.add_argument(
        "--parent", type=str, default=None,
        help="Show children of this task_id (omit for root).",
    )
    todo_list_p.add_argument(
        "--status", type=str, default=None,
        help="Filter by status (pending/in_progress/...).",
    )
    todo_list_p.add_argument(
        "--include-descendants", action="store_true",
        help="Recursively include all descendants.",
    )
    todo_show = todo_sub.add_parser(
        "show", parents=[common], help="Show a single task",
    )
    todo_show.add_argument("task_id", type=str)
    todo_add = todo_sub.add_parser(
        "add", parents=[common], help="Create a new task",
    )
    todo_add.add_argument("--title", required=True, type=str)
    todo_add.add_argument(
        "--parent", type=str, default=None,
        help="Parent task_id (omit for root task).",
    )
    todo_add.add_argument("--description", type=str, default="")
    todo_add.add_argument(
        "--priority", choices=["high", "medium", "low"],
        default="medium",
    )
    todo_sub.add_parser(
        "tree", parents=[common],
        help="Render the full tree as DFS pre-order.",
    )
    todo_progress = todo_sub.add_parser(
        "progress", parents=[common], help="Show progress summary",
    )
    todo_progress.add_argument(
        "--task", type=str, default=None,
        help="Scope to a subtree root (omit for global).",
    )
    todo_complete = todo_sub.add_parser(
        "complete", parents=[common], help="Mark a task completed",
    )
    todo_complete.add_argument("task_id", type=str)
    todo_complete.add_argument("--result", type=str, default="")
    todo_delete = todo_sub.add_parser(
        "delete", parents=[common], help="Delete a task (cascade by default)",
    )
    todo_delete.add_argument("task_id", type=str)
    todo_delete.add_argument(
        "--no-cascade", action="store_true",
        help="Keep descendants as orphans instead of removing them.",
    )

    # ── skills (v0.9.116.0+) — SKILL.md file-library management ──
    skills_parser = subparsers.add_parser(
        "skills", parents=[common],
        help="Skill FILE library: import / list / show / search / "
             "remove / use Claude-style SKILL.md bundles.",
    )
    skills_sub = skills_parser.add_subparsers(dest="skills_cmd")
    skills_sub.add_parser(
        "list", parents=[common],
        help="List every skill in the library (name/desc/files).",
    )
    skills_show = skills_sub.add_parser(
        "show", parents=[common],
        help="Show one skill: frontmatter + SKILL.md text + files.",
    )
    skills_show.add_argument("name", type=str)
    skills_search_p = skills_sub.add_parser(
        "search", parents=[common],
        help="Keyword-search name + description + body.",
    )
    skills_search_p.add_argument("query", type=str)
    skills_search_p.add_argument("--limit", type=int, default=10)
    skills_add = skills_sub.add_parser(
        "add", parents=[common],
        help="Import a SKILL.md folder / single file / URL (github "
             "blob auto→raw) into the library.",
    )
    skills_add.add_argument("source", type=str)
    skills_add.add_argument(
        "--name", type=str, default=None,
        help="Override the skill name (default = frontmatter name).",
    )
    skills_add.add_argument(
        "--overwrite", action="store_true",
        help="Replace an existing skill of the same name.",
    )
    skills_add.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip the confirm prompt.",
    )
    skills_rm = skills_sub.add_parser(
        "remove", parents=[common],
        help="Delete one skill folder from the library (irreversible).",
    )
    skills_rm.add_argument("name", type=str)
    skills_rm.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip the confirm prompt.",
    )
    skills_use = skills_sub.add_parser(
        "use", parents=[common],
        help="Print the skill body + usage note (the 引用 path — "
             "pasteable into background-task / stage prompts).",
    )
    skills_use.add_argument("name", type=str)
    skills_use.add_argument("--max-chars", type=int, default=None)

    # ── rules (v0.9.124.0+) — agent-maintained RULE BOOK ──
    rules_parser = subparsers.add_parser(
        "rules", parents=[common],
        help="Agent RULE BOOK: list / show the revisable rule file "
             "(rules_add/revise/rollback — revise requires evidence).",
    )
    rules_sub = rules_parser.add_subparsers(dest="rules_cmd")
    rules_sub.add_parser(
        "list", parents=[common],
        help="List every rule (id/title/evidence/updated).",
    )
    rules_show = rules_sub.add_parser(
        "show", parents=[common],
        help="Show the raw rulebook file (capped view).",
    )
    rules_show.add_argument("--full", action="store_true")
    rules_add = rules_sub.add_parser(
        "add", parents=[common],
        help="Add one rule (--title --text [--evidence]).",
    )
    rules_add.add_argument("--title", type=str, required=True)
    rules_add.add_argument("--text", type=str, required=True)
    rules_add.add_argument("--evidence", type=str, default=None)
    rules_add.add_argument("--yes", "-y", action="store_true")
    rules_revise = rules_sub.add_parser(
        "revise", parents=[common],
        help="Revise one rule --rule-id --text --evidence (required).",
    )
    rules_revise.add_argument("--rule-id", type=str, required=True)
    rules_revise.add_argument("--text", type=str, required=True)
    rules_revise.add_argument("--evidence", type=str, required=True)
    rules_revise.add_argument("--yes", "-y", action="store_true")
    rules_roll = rules_sub.add_parser(
        "rollback", parents=[common],
        help="Restore the rulebook from the Nth-previous snapshot.",
    )
    rules_roll.add_argument("--steps", type=int, default=1)
    rules_roll.add_argument("--yes", "-y", action="store_true")
    rules_hist = rules_sub.add_parser(
        "history", parents=[common],
        help="Snapshot ring + the latest diff.",
    )
    rules_hist.add_argument("--limit", type=int, default=10)

    # delete-collection / recreate-collection
    subparsers.add_parser(
        "delete-collection", parents=[common],
        help="Drop the entire collection (all ingested data lost)",
    )
    subparsers.add_parser(
        "recreate-collection", parents=[common],
        help="Drop and recreate the collection (e.g. to apply new HNSW params). "
             "All data is lost; re-run ingest afterwards.",
    )

    # ── backfill-sparse — hybrid-search migration (v0.9.87.0+) ──
    sp = subparsers.add_parser(
        "backfill-sparse", parents=[common],
        help="Add the bm25 sparse vector to every existing point "
             "(hybrid-search migration; requires the collection "
             "schema to carry the sparse field — recreate with "
             "QDRANT_HYBRID=on first).",
    )
    sp.add_argument("--batch", type=int, default=100)

    # ── audit-backends / reembed-hash — pollution control (v0.9.112.0+) ──
    ab = subparsers.add_parser(
        "audit-backends", parents=[common],
        help="Read-only scan: count + list points whose vector came "
             "from the hash FALLBACK backend (exact heuristic; works "
             "for points tagged AND untagged).",
    )
    ab.add_argument("--batch", type=int, default=256)
    rh = subparsers.add_parser(
        "reembed-hash", parents=[common],
        help="Re-embed every hash-fallback vector with the CURRENT "
             "backend (caches bypassed; points that re-embed to hash "
             "again are untouched; run audit-backends first to see "
             "the blast radius).",
    )
    rh.add_argument("--limit", type=int, default=None)

    # ── backfill-headings — STAIR retro-fill (v0.9.113.0+) ──
    bh = subparsers.add_parser(
        "backfill-headings", parents=[common],
        help="Fill the heading_path payload on existing points from "
             "their own text (metadata-only via set_payload; vectors "
             "and ids untouched, no re-embed).",
    )
    bh.add_argument("--limit", type=int, default=None)
    bh.add_argument("--force", action="store_true",
                    help="Also recompute points that already carry a "
                         "non-empty heading_path.")
    bh.add_argument("--collection", default=None,
                    help="Override QDRANT_COLLECTION for this run.")

    # ── keep-auth — Google Keep OAuth consent (v0.9.88.0+) ──
    kp = subparsers.add_parser(
        "keep-auth", parents=[common],
        help="One-time Google Keep OAuth consent (writes token.json; "
             "KEEP_MODE=live reads/writes real Google Keep notes).",
    )
    kp.add_argument(
        "--scopes", choices=["combined", "calendar", "keep"],
        default="combined",
        help="combined = keep+calendar (default, one consent covers "
             "both); calendar = calendar only (fallback when the Keep "
             "scope is rejected with Error 400: invalid_scope); "
             "keep = keep only (diagnose which scope Google rejects).",
    )

    # ── reload — .env hot reload + service restart (v0.9.42.0+) ──
    reload_p = subparsers.add_parser(
        "reload", parents=[common],
        help="Re-read .env + restart daemon services + reconnect MCP "
             "fleet (no process restart needed)",
    )
    reload_p.add_argument(
        "--no-services", action="store_true",
        help="Reload config only; skip the daemon-service restarts.",
    )

    # ── remove-files — delete indexed docs by basename (v0.7.0+) ──
    # One-or-many filenames. Use case-sensitive exact match against
    # the ``filename`` payload field. To see what to pass, run
    # ``divine-sense-agent.bat stats`` first.
    remove_files_p = subparsers.add_parser(
        "remove-files", parents=[common],
        help="Remove one or more indexed documents by filename (basename)",
    )
    remove_files_p.add_argument(
        "filenames", nargs="+",
        help="One or more filenames to delete from the index "
             "(e.g. report.pdf notes.txt).",
    )
    remove_files_p.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip the per-file confirmation prompt",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == "ingest":
        _handle_ingest(args)
    elif args.command == "search":
        _handle_search(args)
    elif args.command == "ask":
        _handle_ask(args)
    elif args.command == "stats":
        _handle_stats()
    elif args.command == "chat":
        _handle_chat(args)
    elif args.command == "sessions":
        _handle_sessions(args)
    elif args.command == "skills":
        _handle_skills(args)
    elif args.command == "rules":
        _handle_rules(args)
    elif args.command == "schedules":
        _handle_schedules(args)
    elif args.command == "creds":
        _handle_creds(args)
    elif args.command == "reload":
        _handle_reload(args)
    elif args.command in ("delete-collection", "recreate-collection"):
        _handle_collection_op(args.command)
    elif args.command == "backfill-sparse":
        _handle_backfill_sparse(args)
    elif args.command == "audit-backends":
        _handle_audit_backends(args)
    elif args.command == "reembed-hash":
        _handle_reembed_hash(args)
    elif args.command == "backfill-headings":
        _handle_backfill_headings(args)
    elif args.command == "keep-auth":
        _handle_keep_auth(args)
    elif args.command == "remove-files":
        _handle_remove_files(args)


def _handle_reload(args):
    """v0.9.42.0+ — .env hot reload + daemon-service restart + MCP
    fleet reconnect, without restarting the process."""
    from src.agent.agent import Agent

    agent = Agent(mcp_manager=None)
    agent._mcp_manager = agent.mcp_manager  # build once
    out = agent.reload_runtime(
        restart_services=not getattr(args, "no_services", False),
    )
    if not out.get("ok"):
        print(f"[錯誤] reload 失敗：{out.get('error')}")
        return
    changed = out.get("changed") or []
    print(f"[OK] 已重新載入 .env — 變更 {len(changed)} 項")
    for k in changed:
        print(f"  · {k}")
    for name, status in (out.get("services") or {}).items():
        print(f"  服務 {name}: {status}")
    try:
        mcp = agent.mcp_manager.reconnect_remote_servers()
        print(f"  MCP 重連: rebuilt={mcp.get('rebuilt')} "
              f"failed={mcp.get('failed') or '{}'}")
    except Exception as e:
        print(f"  MCP 重連失敗: {e!r}")


def _handle_creds(args):
    """v0.9.39.0+ — central credential-set management (masked)."""
    from src.credential_store import CredentialStore

    store = CredentialStore()
    cmd = getattr(args, "creds_cmd", None) or "list"
    if cmd == "list":
        entries = store.list(service=args.service or None, masked=True)
        if not entries:
            print("(no credential sets — add one with "
                  "`creds add --name <name> --password-env <VAR>`)")
            return
        print(f"credential sets ({len(entries)}):")
        for e in entries:
            print(f"  - {e['name']}  [{e['credential_source']}] "
                  f"service={e['service']} user={e['username_masked']} "
                  f"cred={e['credential_masked']}")
        return
    if cmd == "show":
        entry = store.get(args.name)
        if not entry:
            print(f"[error] credential set '{args.name}' not found")
            return
        print(json.dumps(store.mask_entry(entry),
                         ensure_ascii=False, indent=2))
        return
    if cmd == "add":
        out = store.add(
            name=args.name,
            username=args.username,
            password=args.password,
            password_env=args.password_env,
            service=args.service,
            notes=args.notes,
            overwrite=args.overwrite,
        )
        if out.get("ok"):
            print(f"saved credential set '{args.name}' "
                  f"(source={out['source']}, {out['path']})")
        else:
            print(f"[error] {out.get('error')}")
        return
    if cmd == "remove":
        if store.remove(args.name):
            print(f"removed credential set '{args.name}'")
        else:
            print(f"[error] credential set '{args.name}' not found")
        return
    if cmd == "test":
        resolved = store.resolve(args.name)
        if resolved.get("error"):
            print(f"[error] {resolved['error']}")
            return
        print(f"resolve OK: source={resolved['source']} "
              f"env_var={resolved.get('env_var')} "
              f"user={mask_username(resolved.get('username', ''))} "
              f"password={mask_password(resolved.get('password', ''))}")
        return
    print(f"[error] unknown creds sub-command: {cmd}")


def _handle_ingest(args):
    from src.ingest.pipeline import IngestPipeline

    pipeline = IngestPipeline()
    path = Path(args.path)
    if not path.exists():
        # v0.9.87.1 — a typo'd path used to crash with a raw
        # FileNotFoundError traceback from rglob; fail friendly.
        print(f"[錯誤] 路徑不存在：{path.resolve()}")
        print("       請確認檔案或資料夾是否存在（例：ingest .\\documents）")
        return
    if path.is_dir():
        results = pipeline.ingest_directory(str(path))
        total = sum(results.values())
        print(f"\nIngested {len(results)} files, {total} chunks total.")
    else:
        count = pipeline.ingest_file(str(path))
        print(f"\nIngested {path.name}: {count} chunks.")


def _handle_search(args):
    from src.vector_store.retriever import Retriever

    retriever = Retriever()
    query = " ".join(args.query)
    results = retriever.search(
        query=query,
        top_k=args.top_k,
        filename_filter=args.filename,
    )
    if not results:
        print("No results found.")
        return

    print(f"\nSearch Results for: '{query}'")
    print(f"Found {len(results)} results:\n")
    for i, r in enumerate(results, 1):
        print(f"[{i}] Score: {r['score']:.4f}")
        print(f"    Source: {r.get('filename', 'unknown')}")
        text = r.get("text", "")
        print(f"    Content: {text[:200]}...\n")


def _handle_ask(args):
    from src.agent.agent import Agent
    from src.ui.md_render import render_markdown

    question = " ".join(args.question)
    agent = _make_agent(getattr(args, "mcp_config", None),
                        disable_tools=getattr(args, "no_tools", False))
    _print_tool_banner(agent)
    use_web = getattr(args, "web_search", False)
    web_max = getattr(args, "web_max_results", 5)
    use_tools = not getattr(args, "no_tools", False)
    lang = getattr(args, "lang", None)
    if args.stream:
        # Qdrant Agent doesn't have ask_stream() yet (tracked in README TODO).
        # Fall back to render_markdown() on the full answer so the --stream
        # flag still produces a usable (non-streamed) Markdown reply.
        print("[warn] --stream not yet supported on Qdrant Agent; "
              "falling back to non-streaming.", file=sys.stderr)
        answer = agent.ask(
            question, top_k=args.top_k,
            use_web_search=use_web, web_max_results=web_max,
            use_tools=use_tools, response_language=lang,
        )
        print(f"\nQ: {question}\n")
        print("A:")
        print(render_markdown(answer))
        print()
    else:
        answer = agent.ask(
            question, top_k=args.top_k,
            use_web_search=use_web, web_max_results=web_max,
            use_tools=use_tools, response_language=lang,
        )
        print(f"\nQ: {question}\n")
        print("A:")
        print(render_markdown(answer))
        print()


def _handle_stats():
    from src.vector_store.qdrant_client import QdrantStore
    from src.ingest.embedder import Embedder
    import json

    stats = QdrantStore().get_stats()
    print(json.dumps(stats, indent=2))

    # v0.3.9: also dump the active Embedder backend so the user can
    # see at a glance whether ``ingest`` and ``search`` / ``chat``
    # will produce vectors of the same dimension. If not, print a
    # clear warning and remediation steps.
    try:
        embed = Embedder()
        info = embed.backend_info()
    except Exception as e:
        print(f"\n[warn] Embedder backend_info() failed: {e}", file=__import__("sys").stderr)
        return
    print("\n--- Embedder backend ---")
    for k, v in (info.items() if isinstance(info, dict) else vars(info).items()):
        print(f"  {k}: {v}")
    native_dim = info.get("native_dim") if isinstance(info, dict) else getattr(info, "native_dim", None)
    qdrant_dim = info.get("collection_dim") if isinstance(info, dict) else getattr(info, "qdrant_dim", None)
    if native_dim and qdrant_dim and native_dim != qdrant_dim:
        print(
            "\n[!] DIMENSION MISMATCH\n"
            f"    Qdrant collection is {qdrant_dim}-dim but the active embedder\n"
            f"    produces {native_dim}-dim vectors. Search / chat will FAIL until\n"
            f"    this is resolved. Pick one of:\n"
            f"      (a) switch to a {qdrant_dim}-dim model (e.g. qwen3-embedding:8b on\n"
            f"          Ollama Cloud) and re-ingest the whole collection, OR\n"
            f"      (b) drop the collection (``cli.py delete-collection``) and re-ingest\n"
            f"          with the current {native_dim}-dim backend\n"
            f"    See README \"Embedding backend consistency\" section for details."
        )
    elif native_dim and qdrant_dim:
        print(f"\n[OK] Embedder {native_dim}-dim == collection {qdrant_dim}-dim ✓")


# ─── BBS-style UI helpers (PTT / Bahamut inspired) ───────────────────
def _bbs_width() -> int:
    """Return a safe column width for the BBS UI, capped to [60, 78].

    We avoid any call that touches a real Windows console handle (e.g.
    shutil.get_terminal_size() or os.get_terminal_size()), because those
    can block indefinitely when stdout is redirected through a pipe
    (as is common when this module is imported by tests/probes).

    Fallback chain (no I/O):
      1. COLUMNS env var          (set by `resize` / cmder / ConEmu)
      2. QDRANT_RAG_WIDTH env var (manual override)
      3. Default 78
    """
    for var in ("COLUMNS", "QDRANT_RAG_WIDTH"):
        v = _os.environ.get(var, "").strip()
        if v.isdigit():
            n = int(v)
            if 30 <= n <= 200:
                return max(60, min(78, n))
    return 78


def _bbs_wlen(text: str) -> int:
    """Visual column width: CJK and full-width chars count as 2, others as 1."""
    width = 0
    for ch in text:
        code = ord(ch)
        if (
            0x1100 <= code <= 0x115F
            or 0x2E80 <= code <= 0x303E
            or 0x3041 <= code <= 0x33FF
            or 0x3400 <= code <= 0x4DBF
            or 0x4E00 <= code <= 0x9FFF
            or 0xA000 <= code <= 0xA4CF
            or 0xAC00 <= code <= 0xD7A3
            or 0xF900 <= code <= 0xFAFF
            or 0xFE30 <= code <= 0xFE4F
            or 0xFF00 <= code <= 0xFF60
            or 0xFFE0 <= code <= 0xFFE6
            or 0x20000 <= code <= 0x2FFFD
            or 0x30000 <= code <= 0x3FFFD
        ):
            width += 2
        elif code == 0x9:
            width += 0
        else:
            width += 1
    return width


def _bbs_ljust(text: str, width: int) -> str:
    pad = max(0, width - _bbs_wlen(text))
    return text + " " * pad


def _bbs_center(text: str, width: int) -> str:
    pad = max(0, width - _bbs_wlen(text))
    left = pad // 2
    right = pad - left
    return " " * left + text + " " * right


def _bbs_truncate(text: str, width: int) -> str:
    if _bbs_wlen(text) <= width:
        return text
    out = []
    used = 1
    for ch in text:
        cw = 2 if (0x4E00 <= ord(ch) <= 0x9FFF or 0x3040 <= ord(ch) <= 0x30FF
                   or 0xAC00 <= ord(ch) <= 0xD7A3) else 1
        if used + cw > width:
            break
        out.append(ch)
        used += cw
    return "".join(out) + "…"


def _bbs_separator(width=None, ch="─"):
    return ch * (width or _bbs_width())


# ─── ANSI helpers (for markdown-rendered BBS posts) ──────────────────
_ANSI_ESCAPE_RE = __import__("re").compile(r"\x1b\[[0-9;]*m")


def _bbs_wlen_ansi(text: str) -> int:
    """Visual width of text that may contain ANSI escape sequences."""
    width = 0
    i = 0
    while i < len(text):
        if text[i] == "\x1b" and i + 1 < len(text) and text[i + 1] == "[":
            j = i + 2
            while j < len(text) and text[j] != "m":
                j += 1
            i = j + 1 if j < len(text) else len(text)
            continue
        ch = text[i]
        code = ord(ch)
        if (
            0x1100 <= code <= 0x115F
            or 0x2E80 <= code <= 0x303E
            or 0x3041 <= code <= 0x33FF
            or 0x3400 <= code <= 0x4DBF
            or 0x4E00 <= code <= 0x9FFF
            or 0xA000 <= code <= 0xA4CF
            or 0xAC00 <= code <= 0xD7A3
            or 0xF900 <= code <= 0xFAFF
            or 0xFE30 <= code <= 0xFE4F
            or 0xFF00 <= code <= 0xFF60
            or 0xFFE0 <= code <= 0xFFE6
            or 0x20000 <= code <= 0x2FFFD
            or 0x30000 <= code <= 0x3FFFD
        ):
            width += 2
        else:
            width += 1
        i += 1
    return width


def _bbs_ljust_ansi(text: str, width: int) -> str:
    pad = max(0, width - _bbs_wlen_ansi(text))
    return text + " " * pad


def _bbs_wrap_ansi(text: str, width: int) -> list[str]:
    """Wrap text containing ANSI escapes; preserves escapes across word breaks."""
    out: list[str] = []
    for para in (text.splitlines() or [text]):
        if not para:
            out.append("")
            continue
        words = para.split(" ")
        line = ""
        line_w = 0
        for w in words:
            ww = _bbs_wlen_ansi(w)
            sep_w = 1 if line else 0
            if line_w + sep_w + ww <= width:
                line = (line + " " + w) if line else w
                line_w += sep_w + ww
            else:
                if line:
                    out.append(line)
                if ww > width:
                    chunk = ""
                    chunk_w = 0
                    j = 0
                    while j < len(w):
                        if w[j] == "\x1b" and j + 1 < len(w) and w[j + 1] == "[":
                            k = j + 2
                            while k < len(w) and w[k] != "m":
                                k += 1
                            chunk += w[j : k + 1]
                            j = k + 1
                            continue
                        cw = 2 if (
                            0x4E00 <= ord(w[j]) <= 0x9FFF
                            or 0x3040 <= ord(w[j]) <= 0x30FF
                            or 0xAC00 <= ord(w[j]) <= 0xD7A3
                        ) else 1
                        if chunk_w + cw > width and chunk:
                            out.append(chunk)
                            chunk = w[j]
                            chunk_w = cw
                        else:
                            chunk += w[j]
                            chunk_w += cw
                        j += 1
                    line = chunk
                    line_w = chunk_w
                else:
                    line = w
                    line_w = ww
        if line:
            out.append(line)
    return out


def _bbs_wrap(text, width):
    """Word-wrap text to fit within a *visual* width (CJK-aware)."""
    out = []
    for para in (text.splitlines() or [text]):
        if not para:
            out.append("")
            continue
        words = para.split(" ")
        line = ""
        line_w = 0
        for w in words:
            ww = _bbs_wlen(w)
            sep_w = 1 if line else 0
            if line_w + sep_w + ww <= width:
                line = (line + " " + w) if line else w
                line_w += sep_w + ww
            else:
                if line:
                    out.append(line)
                if ww > width:
                    chunk = ""
                    chunk_w = 0
                    for ch in w:
                        cw = 2 if (0x4E00 <= ord(ch) <= 0x9FFF or
                                   0x3040 <= ord(ch) <= 0x30FF or
                                   0xAC00 <= ord(ch) <= 0xD7A3) else 1
                        if chunk_w + cw > width:
                            out.append(chunk)
                            chunk = ch
                            chunk_w = cw
                        else:
                            chunk += ch
                            chunk_w += cw
                    line = chunk
                    line_w = chunk_w
                else:
                    line = w
                    line_w = ww
        if line:
            out.append(line)
    return out


def _bbs_box(title, body, width=None):
    w = width or _bbs_width()
    out = ["┌" + "─" * (w - 2) + "┐"]
    if title:
        t = f" {title} "
        avail = w - 2
        if _bbs_wlen(t) <= avail:
            centered = _bbs_center(t, avail)
            out.append("│" + centered + "│")
        else:
            out.append("│" + _bbs_ljust(t, avail) + "│")
        out.append("├" + "─" * (w - 2) + "┤")
    inner = w - 4
    for line in body:
        wrapped = _bbs_wrap(line, inner)
        if not wrapped:
            out.append("│" + " " * inner + "│")
            continue
        for wl in wrapped:
            out.append("│ " + _bbs_ljust(wl, inner) + " │")
    out.append("└" + "─" * (w - 2) + "┘")
    return out


def _bbs_status_bar(session, width=None):
    from datetime import datetime
    w = width or _bbs_width()
    now = datetime.now().strftime("%H:%M:%S")
    title = (session.title or "(no title)")[:18]
    parts = ["★ divine-sense-agent", "chat", f"sid:{session.id[:10]}",
             f"msgs:{len(session.messages)}", f"title:{title}", now]
    bar = " ".join(parts)
    bar = _bbs_truncate(bar, w)
    bar = _bbs_ljust(bar, w)
    return [bar, _bbs_separator(w, "═")]


def _bbs_footer(width=None):
    w = width or _bbs_width()
    sep = _bbs_separator(w)
    menu = "[e]xit  /info  /reset  /list  /load <id>"
    return [sep, _bbs_center(menu, w), sep]


# ─── Per-session question dedup (v0.2.8.1) ───────────────────────────
import hashlib


def _normalise_question(q: str) -> str:
    """Lower-case + collapse whitespace + strip trailing punctuation.

    Used as the key for the per-session dedup cache so trivial
    differences ("什麼?" vs "什麼" vs "什麼？" vs "  什麼 ") all
    hash to the same fingerprint.
    """
    s = (q or "").strip().lower()
    # Drop a tiny handful of CJK / ASCII terminal punctuation that
    # users often append when typing fast.
    for tail in ("?", "?", "!", "!", ".", ",", "，", "。", "：", ":", "？", "！"):
        if s.endswith(tail):
            s = s[: -len(tail)].rstrip()
    return " ".join(s.split())


def _question_fingerprint(q: str) -> str:
    return hashlib.sha1(_normalise_question(q).encode("utf-8")).hexdigest()[:16]


def _find_recent_assistant_reply(
    messages: list[dict], question: str
) -> tuple[str, list[str]] | None:
    """Walk the saved session messages looking for an earlier user/assistant
    pair whose user question normalises to the same fingerprint as
    ``question``.

    Returns ``(answer, sources)`` or ``None`` if no match is found.
    Linear scan is fine — session messages are small.
    """
    fp = _question_fingerprint(question)
    user_fp: str | None = None
    for m in messages:
        if m.get("role") == "user":
            user_fp = _question_fingerprint(m.get("content", ""))
        elif m.get("role") == "assistant" and user_fp == fp:
            return (m.get("content", ""), m.get("sources") or [])
    return None


def _bbs_post(role, name, ts, content, sources=None, width=None):
    w = width or _bbs_width()
    icon = "→" if role == "user" else "※"
    head = f"{icon} {role:<9s} {name} ({ts})"
    head = _bbs_truncate(head, w)
    head = _bbs_ljust(head, w)
    sep = _bbs_separator(w)
    out = [head, sep]
    inner = w - 2
    for line in (content.splitlines() or [content]):
        wrapped = _bbs_wrap(line, inner)
        if not wrapped:
            out.append(_bbs_ljust("", w))
            continue
        for wl in wrapped:
            out.append("  " + _bbs_ljust(wl, inner))
    if sources:
        for wl in _bbs_wrap("▌ 來源: " + ", ".join(sources), inner):
            out.append("  " + _bbs_ljust(wl, inner))
    out.append(sep)
    return out


# ─── MCP tool plumbing (v0.8.0+) ──────────────────────────────
def _make_agent(mcp_config: str | None, disable_tools: bool = False):
    """Build an Agent with an explicit McpManager.

    ``mcp_config=None`` -> use ``Agent.DEFAULT_MCP_CONFIG``.
    ``mcp_config=""``   -> local hosts only.
    ``mcp_config="..."``  -> load that path.

    ``disable_tools=True`` -> bypass tool calling entirely.
    """
    from src.agent.agent import Agent
    from src.mcp_client import McpManager

    if disable_tools:
        return _NoToolAgent()

    if mcp_config == "":
        manager = McpManager.from_config(
            None, include_local_hosts=True,
        )
        return Agent(mcp_manager=manager)

    manager = McpManager.from_config(
        mcp_config, include_local_hosts=True,
    )
    return Agent(mcp_manager=manager)


class _NoToolAgent:
    """Minimal shim that bypasses the tool-calling path.

    Forwards ``ask()`` / ``chat_turn()`` to a vanilla Agent with
    ``use_tools=False`` so CLI / chat code stays one-trait.
    """

    def __init__(self):
        from src.agent.agent import Agent
        self._delegate = Agent()

    def ask(self, *a, **kw):
        kw.setdefault("use_tools", False)
        return self._delegate.ask(*a, **kw)

    def chat_turn(self, *a, **kw):
        kw.setdefault("use_tools", False)
        return self._delegate.chat_turn(*a, **kw)

    def __getattr__(self, name):
        return getattr(self._delegate, name)


def _print_tool_banner(agent) -> None:
    """Print the active tool surface to stderr (one-liner)."""
    try:
        mgr = agent.mcp_manager
    except Exception:
        print(
            "[tools] no tool surface available",
            file=sys.stderr,
        )
        return
    tools = mgr.list_tools()
    if not tools:
        print("[tools] (legacy path only)", file=sys.stderr)
        return
    servers: dict[str, int] = {}
    for t in tools:
        servers[t.server_name] = servers.get(t.server_name, 0) + 1
    summary = ", ".join(
        f"{srv}({n})" for srv, n in sorted(servers.items())
    )
    print(f"[tools] active tool surface: {summary}",
          file=sys.stderr)
    if mgr.failed:
        for srv, why in mgr.failed.items():
            print(
                f"[tools] WARN {srv}: {why[:80]}",
                file=sys.stderr,
            )


def _bbs_post_md(role, name, ts, content, sources=None, width=None):
    """BBS post that renders `content` as Markdown (via src.ui.md_render)."""
    w = width or _bbs_width()
    icon = "→" if role == "user" else "※"
    head = f"{icon} {role:<9s} {name} ({ts})"
    head = _bbs_truncate(head, w)
    head = _bbs_ljust(head, w)
    sep = _bbs_separator(w)
    out = [head, sep]
    inner = w - 2

    try:
        from src.ui.md_render import render_markdown
    except Exception:
        return _bbs_post(role, name, ts, content, sources=sources, width=w)

    rendered = render_markdown(content)
    for line in (rendered.splitlines() or [rendered]):
        wrapped = _bbs_wrap_ansi(line, inner)
        if not wrapped:
            out.append(_bbs_ljust("", w))
            continue
        for wl in wrapped:
            out.append("  " + _bbs_ljust_ansi(wl, inner))
    if sources:
        src_line = "▌ 來源: " + ", ".join(sources)
        for wl in _bbs_wrap_ansi(src_line, inner):
            out.append("  " + _bbs_ljust_ansi(wl, inner))
    out.append(sep)
    return out


def _handle_chat(args):
    """Interactive REPL with persistent session history (BBS-style UI).

    Qdrant is a real HTTP server, so the Retriever talks to it
    directly in-process. (v0.4.0: no more client-side MCP
    subprocesses; see Agent docstring for details.)
    """
    from datetime import datetime
    from src.agent.agent import Agent
    from src.agent.session import SessionManager
    mgr = SessionManager()
    session = mgr.load(args.session) if args.session else mgr.create()
    if not session:
        session = mgr.create()

    agent = _make_agent(getattr(args, "mcp_config", None),
                       disable_tools=getattr(args, "no_tools", False))
    _print_tool_banner(agent)
    history = list(session.messages)
    # Per-turn web search toggle (defaults to whatever --web-search
    # was set on the command line; /web flips it mid-session).
    use_web = bool(getattr(args, "web_search", False))
    web_max = int(getattr(args, "web_max_results", 5))
    use_tools = not bool(getattr(args, "no_tools", False))
    # v0.8.5+: language directive — None means "let the LLM pick".
    # User can flip mid-session with ``/lang zh-TW|zh-CN|en|ja|auto``.
    use_lang = getattr(args, "lang", None)
    _LANG_CHOICES = ("zh-TW", "zh-CN", "en", "ja")
    w = _bbs_width()
    for line in _bbs_box(
        f"歡迎光臨 divine-sense-agent 聊天室 (session {session.id})",
        [
            f"  標題 : {session.title or '(待命名, 首次提問後自動產生)'}",
            f"  訊息 : {len(session.messages)} 條",
            f"  建立 : {session.created_at[:19]}",
            f"  更新 : {session.updated_at[:19] if session.updated_at else '-'}",
            "  搜尋 : in-process Retriever → Qdrant HTTP",
            "  工具 : LLM tool-calling (MCP / 本地) — /tools list",
            f"  模式 : tool-calling {'ON' if use_tools else 'OFF'} (--no-tools 啟動可改)",
            f"          config : {getattr(args, 'mcp_config', None) or '(none)'}",
            "",
            "  操作說明:",
            "    e 或 quit   離開",
            "    /info       顯示 session 資訊",
            "    /reset      清空目前 session",
            "    /list       列出所有 saved sessions",
            "    /load <id>  切換到指定 session",
            "    /web [on|off|status]   切換 Ollama Web Search",
            "    /tools [on|off|status|list]  切換 tool-calling, 或列出可用 tools",
            "",
            "  直接輸入訊息即可發問, 機器人會自動回覆。",
        ],
        width=w,
    ):
        print(line)
    print()

    try:
        while True:
            for line in _bbs_status_bar(session, width=w):
                print(line)
            try:
                user = input(f"[{session.id[:8]}] {session.title or 'You'}> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                for line in _bbs_footer(width=w):
                    print(line)
                print("[系統] 使用者中斷, 掰掰。")
                break
            if not user:
                continue
            if user in ("exit", "quit", ":q", "e"):
                for line in _bbs_footer(width=w):
                    print(line)
                print("[系統] 掰掰，下次再來!")
                break
            if user == "/info":
                print(_bbs_separator(w))
                for k, v in (
                    ("id", session.id),
                    ("title", session.title or "(untitled)"),
                    ("msgs", len(history)),
                    ("created", session.created_at[:19]),
                ):
                    print(f"  {k:<8}: {v}")
                continue
            if user == "/reset":
                history.clear()
                mgr.save(session)
                print(_bbs_separator(w))
                print("[系統] Session 已清空。")
                continue
            if user == "/list":
                print(_bbs_separator(w))
                print(f"{'ID':<14} | {'msgs':>4} | {'updated':<19} | title")
                print(_bbs_separator(w, "·"))
                for s in mgr.list():
                    print(
                        f"  {s['id']:<14} | {s['n_messages']:>4} | "
                        f"{s.get('updated_at', '?')[:19]} | {s['title'][:30]}"
                    )
                continue
            if user.startswith("/load "):
                new_id = user.split(maxsplit=1)[1].strip()
                new_s = mgr.load(new_id)
                if new_s is None:
                    print(f"[系統] 找不到 session {new_id}")
                    continue
                session = new_s
                history = list(session.messages)
                print(_bbs_separator(w))
                print(f"[系統] 已切換到 {session.id} ({len(history)} 條訊息)")
                continue
            if user in ("/web", "/web on"):
                use_web = True
                print(_bbs_separator(w))
                print("[系統] Web search: ON (下一輪起加 web context)")
                continue
            if user in ("/web off",):
                use_web = False
                print(_bbs_separator(w))
                print("[系統] Web search: OFF (下一輪起只查 Qdrant)")
                continue
            if user == "/web status":
                print(_bbs_separator(w))
                print(f"[系統] Web search: {'ON' if use_web else 'OFF'} "
                      f"(max {web_max} results)")
                continue
            # ─── v0.8.0+ tool-calling controls ─────────────────
            if user in ("/tools", "/tools on"):
                use_tools = True
                print(_bbs_separator(w))
                print("[系統] Tool calling: ON")
                continue
            if user == "/tools off":
                use_tools = False
                print(_bbs_separator(w))
                print("[系統] Tool calling: OFF (下一輪起走 legacy RAG)")
                continue
            if user == "/tools status":
                print(_bbs_separator(w))
                print(f"[系統] Tool calling: "
                      f"{'ON' if use_tools else 'OFF'}")
                try:
                    mgr_inst = agent.mcp_manager
                    tools = mgr_inst.list_tools()
                    print(f"       tools in catalog: {len(tools)}")
                    if mgr_inst.failed:
                        print(f"       failed servers: "
                              f"{list(mgr_inst.failed.keys())}")
                except Exception as e:
                    print(f"       (could not read catalog: {e!r})")
                continue
            if user == "/tools list":
                print(_bbs_separator(w))
                try:
                    tools = agent.mcp_manager.list_tools()
                except Exception as e:
                    print(f"[錯誤] {type(e).__name__}: {e}")
                    continue
                if not tools:
                    print("(no tools registered - check config/mcp.json)")
                    continue
                print(f"[系統] {len(tools)} tools available:")
                for t in tools:
                    line = f"  [{t.server_name}] {t.name}"
                    if t.description:
                        line += f" — {t.description[:60]}"
                    print(line)
                continue
            # ─── v0.8.5+ language directive ─────────────────────
            if user in ("/lang", "/lang status"):
                print(_bbs_separator(w))
                print(f"[系統] 回應語言: {use_lang or 'auto (LLM 自行挑選)'}")
                print(f"       可選: {', '.join(_LANG_CHOICES)} | auto")
                continue
            if user.startswith("/lang "):
                arg = user.split(maxsplit=1)[1].strip().lower()
                if arg == "auto":
                    use_lang = None
                    print(_bbs_separator(w))
                    print("[系統] 回應語言: auto (LLM 自行挑選)")
                    continue
                # Case-insensitive match against the supported list.
                match = next(
                    (c for c in _LANG_CHOICES if c.lower() == arg),
                    None,
                )
                if match is None:
                    print(_bbs_separator(w))
                    print(f"[錯誤] 不支援的語言: {arg!r}. "
                          f"可選: {', '.join(_LANG_CHOICES)} | auto")
                    continue
                use_lang = match
                print(_bbs_separator(w))
                print(f"[系統] 回應語言: {match} (下一輪起生效)")
                continue

            # ── Real user turn ───────────────────────────────
            # ── Per-session dedup: if the same question (normalised
            # SHA-1) already produced an assistant reply earlier in
            # this session, replay it instead of re-hitting Qdrant /
            # web. The user sees the saved answer plus a one-line
            # note explaining what happened.
            cache_hit = _find_recent_assistant_reply(session.messages, user)
            ts = datetime.now().isoformat(timespec="seconds")
            if cache_hit is not None:
                answer, sources = cache_hit
                # Mirror the user turn so the visual is consistent.
                mgr.append_message(session.id,
                                   {"role": "user", "content": user, "ts": ts})
                history.append({"role": "user", "content": user, "ts": ts})
                for line in _bbs_post(
                    "user", session.id[:8], ts[11:19], user, width=w
                ):
                    print(line)
                print(_bbs_separator(w))
                print("[系統] 此 session 內已答過相同的問題, "
                      "直接重播上次回答 (略過 RAG / Web 查詢).")
                print(_bbs_separator(w))
                header = (
                    f"※ assistant divine-sense-agent ({ts[11:19]})"
                )
                print(_bbs_ljust(header, w))
                print(_bbs_separator(w))
                try:
                    from src.ui.md_render import render_markdown
                    rendered = render_markdown(answer)
                except Exception:
                    rendered = answer
                for rline in (rendered.splitlines() or [""]):
                    print("  " + rline)
                if sources:
                    src_line = "▌ 來源: " + ", ".join(sources)
                    inner = w - 2
                    wrapped = _bbs_wrap_ansi(src_line, inner)
                    for wl in wrapped:
                        print("  " + _bbs_ljust_ansi(wl, inner))
                print(_bbs_separator(w))
                for line in _bbs_footer(width=w):
                    print(line)
                print()
                continue

            mgr.append_message(session.id,
                               {"role": "user", "content": user, "ts": ts})
            history.append({"role": "user", "content": user, "ts": ts})

            for line in _bbs_post("user", session.id[:8], ts[11:19], user, width=w):
                print(line)

            try:
                answer, context, sources = agent.chat_turn(
                    question=user, history=history[:-1],
                    top_k=args.top_k,
                    use_web_search=use_web,
                    web_max_results=web_max,
                    use_tools=use_tools,
                    response_language=use_lang,
                )
            except Exception as e:
                # Don't kill the REPL on transient errors (Qdrant down, LLM
                # key bad, network glitch, etc). Surface a short error and
                # pop the just-appended user message so the session doesn't
                # end up with an unanswered question.
                print(_bbs_separator(w))
                print(f"[錯誤] 問答失敗：{type(e).__name__}: {e}")
                print("        請確認 Qdrant server 已啟動且 .env LLM key 有效。")
                print("        （session 未儲存此輪，稍後可重試）")
                print(_bbs_separator(w))
                history.pop()
                mgr.save(session)
                continue

            ts = datetime.now().isoformat(timespec="seconds")
            bot_msg = {"role": "assistant", "content": answer, "ts": ts, "sources": sources}
            mgr.append_message(session.id, bot_msg)
            history.append(bot_msg)

            # Print header (BBS-style) on its own, then LLM answer in
            # raw form (so markdown tables render correctly via
            # md_render's ANSI boxes rather than being broken by the
            # narrow BBS-style frame), then the footer with sources.
            header = (
                f"※ assistant divine-sense-agent ({ts[11:19]})"
            )
            print(_bbs_ljust(header, w))
            print(_bbs_separator(w))
            try:
                from src.ui.md_render import render_markdown
                rendered = render_markdown(answer)
            except Exception:
                rendered = answer
            # Indent all rendered lines by 2 spaces so they're
            # visually associated with the assistant prefix even
            # though no BBS box wraps them.
            for rline in (rendered.splitlines() or [""]):
                print("  " + rline)
            if sources:
                src_line = "▌ 來源: " + ", ".join(sources)
                # Wrap sources to w
                inner = w - 2
                wrapped = _bbs_wrap_ansi(src_line, inner)
                for wl in wrapped:
                    print("  " + _bbs_ljust_ansi(wl, inner))
            print(_bbs_separator(w))

            for line in _bbs_footer(width=w):
                print(line)
            print()
    finally:
        # Qdrant itself is a long-lived HTTP server so no cleanup needed.
        # agent.shutdown() is a no-op in v0.4.0 (no MCP subprocesses)
        # but still called for API backward-compat.
        try:
            agent.shutdown()
        except Exception:
            pass


def _handle_sessions(args):
    """List / show / rename / delete saved chat sessions."""
    from src.agent.session import SessionManager

    mgr = SessionManager()
    cmd = getattr(args, "sessions_cmd", None)
    if cmd is None or cmd == "list":
        sessions = mgr.list()
        if not sessions:
            print("No saved sessions.")
            return
        print(f"=== Saved sessions ({len(sessions)}) ===")
        for s in sessions:
            print(
                f"  {s['id']:>14} | {s['n_messages']:>3} msgs | "
                f"updated={s.get('updated_at', '?')[:19]} | {s['title']}"
            )
        print()
        print("Usage: divine-sense-agent.bat chat --session <id>")
        print("       divine-sense-agent.bat sessions show <id>")
        print("       divine-sense-agent.bat sessions rename <id> <new title>")
        print("       divine-sense-agent.bat sessions delete <id>")
        return

    if cmd == "show":
        s = mgr.load(args.session_id)
        if s is None:
            print(f"No session with id {args.session_id}")
            return
        print(f"=== {s.id}  ({s.title}) ===")
        print(f"created : {s.created_at}")
        print(f"updated : {s.updated_at}")
        print(f"msgs    : {len(s.messages)}")
        print()
        for m in s.messages:
            role = m.get("role", "?")
            ts = m.get("ts", "")[:19]
            content = m.get("content", "")
            print(f"[{ts}] {role}> {content[:200]}{'…' if len(content) > 200 else ''}")
            for src in m.get("sources") or []:
                print(f"        ▌ source: {src}")
            print()
        return

    if cmd == "delete":
        if mgr.delete(args.session_id):
            print(f"Deleted {args.session_id}")
        else:
            print(f"No session with id {args.session_id}")

    if cmd == "rename":
        # v0.9.103.5 — title-only metadata edit
        # (SessionManager.rename: messages / timestamps untouched).
        new_title = " ".join(args.title or []).strip()
        if not new_title:
            print("[錯誤] 新標題不可為空白 (new title is empty)")
            return
        s = mgr.rename(args.session_id, new_title)
        if s is None:
            print(f"No session with id {args.session_id}")
        else:
            print(f"Renamed {s.id} → {s.title}")


def _handle_collection_op(cmd: str):
    """Drop (delete-collection) or drop+recreate (recreate-collection)."""
    from src.vector_store.qdrant_client import QdrantStore

    store = QdrantStore()
    if cmd == "delete-collection":
        print("Dropping collection...")
        store.delete_collection()
        print("  ✓ Done. Re-run `ingest` to repopulate.")
    else:  # recreate-collection
        print("Dropping and recreating collection (applying latest HNSW config)...")
        store.recreate_collection()
        stats = store.get_stats()
        print(f"  ✓ Done. New collection: {stats}")


def _handle_backfill_sparse(args):
    """v0.9.87.0 — add the bm25 sparse vector to every existing point
    (the hybrid-search migration for corpora ingested before the
    sparse field existed)."""
    from src.vector_store.qdrant_client import QdrantStore

    store = QdrantStore()
    print(f"Backfilling bm25 sparse vectors for '{store.collection}'...")
    n = store.backfill_sparse(batch=max(10, min(500, args.batch)))
    print(f"  ✓ Done. {n} points updated. Hybrid fused search now "
          f"covers the whole corpus (QDRANT_HYBRID=on).")


def _handle_audit_backends(args):
    """v0.9.112.0 — read-only hash-pollution scan (see
    ``src/ingest/reembed.py``)."""
    from src.ingest.reembed import audit_collection
    from src.vector_store.qdrant_client import QdrantStore

    store = QdrantStore()
    report = audit_collection(
        store, batch=max(10, min(512, getattr(args, "batch", None) or 256)),
    )
    if not report.get("ok"):
        print(f"[錯誤] {report.get('error', 'audit failed')}")
        return
    print(f"Audit '{report['collection']}': scanned {report['scanned']} "
          f"points — hash vectors: {len(report['flagged'])} "
          f"(tagged {report.get('tagged_hash', 0)} / exact-match "
          f"{report.get('matched_hash', 0)}), dim-mismatch skipped "
          f"{report.get('skipped_dim_mismatch', 0)}")
    for row in report.get("flagged", [])[:20]:
        print(f"  ⚠ hash: {row['filename']} (source "
              f"{row['source'][:80]})")
    if len(report.get("flagged", [])) > 20:
        print(f"  ... and {len(report['flagged']) - 20} more "
              f"(reembed-hash to repair)")
    if not report.get("flagged"):
        print("  ✓ clean — no hash-fallback vectors found")


def _handle_reembed_hash(args):
    """v0.9.112.0 — re-embed hash-flagged points with the current
    healthy backend (caches bypassed)."""
    from src.ingest.reembed import reembed_flagged
    from src.vector_store.qdrant_client import QdrantStore

    store = QdrantStore()
    print(f"Re-embedding hash vectors in '{store.collection}' "
          f"(this re-calls the embedding backend)...")
    report = reembed_flagged(
        store, limit=getattr(args, "limit", None),
    )
    if not report.get("ok"):
        print(f"[錯誤] {report.get('error', 'reembed failed')}")
        return
    print(f"  ✓ reembedded={report.get('reembedded', 0)} "
          f"(backends used: {report.get('backends_used', {})})")
    still = report.get("still_hash") or []
    if still:
        print(f"  ⚠ {len(still)} point(s) re-embedded to hash AGAIN "
              f"(every real backend was down) — left untouched; fix "
              f"the backend and re-run.")
    failed = report.get("failed") or []
    if failed:
        print(f"  ✗ {len(failed)} point(s) failed to embed "
              f"(see app.log).")


def _handle_backfill_headings(args):
    """v0.9.113.0 — STAIR heading-path backfill (metadata-only; see
    ``src/ingest/heading_backfill.py``)."""
    from src.ingest.heading_backfill import backfill_collection
    from src.vector_store.qdrant_client import QdrantStore

    store = (QdrantStore(collection=args.collection)
             if getattr(args, "collection", None) else QdrantStore())
    print(f"Backfilling heading_path in '{store.collection}' "
          f"(metadata-only, no re-embed)...")
    report = backfill_collection(
        store,
        limit=getattr(args, "limit", None) or 0,
        force=bool(getattr(args, "force", False)),
        log=print,
    )
    if report.get("errors"):
        print(f"  ⚠ {report['errors']} error(s) during backfill "
              f"(see output above)")
    if not report.get("updated"):
        print("  ✓ nothing to fill — every point already carries "
              "heading_path")
    else:
        print(f"  ✓ filled heading_path on {report['updated']} / "
              f"{report['scanned']} point(s) across "
              f"{report.get('targets', 0)} targets, "
              f"{report['sources']} sources; payload index: "
              f"{'ok' if report['heading_index'] else 'skipped'}")


def _handle_keep_auth(args):
    """v0.9.88.0 — one-time Google Keep OAuth consent (live mode)."""
    from src.keep_tools import KeepEngine

    engine = KeepEngine()
    mode = getattr(args, "scopes", "combined") or "combined"
    print(f"Running the Google Keep OAuth consent flow "
          f"(scopes={mode})...")
    out = engine.run_auth_flow(scope_mode=mode)
    if out.get("error"):
        print(f"[錯誤] {out['error']}")
        return
    print(f"  ✓ Token saved to {out['token_path']}")
    print(f"  scopes: {' '.join(out.get('scopes', []))}")
    print(f"  note: {out.get('note', '')}")
    if out.get("scope_mode") == "calendar":
        print("  → Keep API 若日後可授權：先到 Cloud Console 啟用 "
              "Google Keep API，再跑 `python cli.py keep-auth --scopes "
              "combined` 重新同意（scopes 會併入）。")
    print("  下一步：.env 設 KEEP_MODE=live + KEEP_WRITES_ENABLED=on，"
          "重啟後即可讀寫真實 Google Keep。")


def _handle_remove_files(args):
    """Remove one or more indexed files by basename (v0.7.0+).

    Cross-check the requested names against the live document list so
    we don't silently typo (and end up with 0 deleted, which looks
    like a success). Print a per-file table and a summary. Bail out
    on any single hard failure — partial deletion is a worse UX.
    """
    import json as _json
    from src.vector_store.qdrant_client import QdrantStore

    store = QdrantStore()

    # Cross-check: list_documents returns canonical basenames.
    # If the user typos, warn before we touch anything.
    existing = set(store.list_documents())
    requested = list(args.filenames)
    missing = [n for n in requested if n not in existing]
    present = [n for n in requested if n in existing]

    print(f"  [remove-files] requested: {len(requested)}, "
          f"present in collection: {len(present)}, "
          f"missing: {len(missing)}")
    if missing:
        print("  ✗ These filenames aren't in the current collection:")
        for m in missing:
            print(f"      - {m}")
        print("    (Tip: run `divine-sense-agent.bat stats` to see the live list.)")

    if not present:
        print("  Nothing to do.")
        return

    print("  Will delete:")
    for n in present:
        print(f"      - {n}")

    if not args.yes:
        try:
            answer = input("  Confirm? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("  Aborted.")
            return

    # Run the actual deletes one at a time. delete_by_filename
    # already invalidates cache after each call, so a subsequent
    # list_documents() in the same handler would see fresh data.
    total_chunks = 0
    failed = []
    try:
        for name in present:
            n = store.delete_by_filename(name)
            print(f"  ✓ {name}: {n} chunks deleted")
            if n > 0:
                total_chunks += n
    except Exception as e:
        # Partial-deleted state: surface the error and whatever we
        # managed to delete so the user can decide whether to retry
        # or clean up via delete-collection.
        print(f"  ✗ Stopped on error: {e!r}", file=sys.stderr)
        failed = [name for name in present if
                  name not in [m for m in missing]]
        print(f"  Successfully deleted before failure: "
              f"{total_chunks} chunks total.")
        sys.exit(1)

    # Final summary.
    after = store.list_documents()
    leftover = [n for n in present if n in after]
    if leftover:
        print(f"  [warn] These are still present "
              f"(delete may have silently skipped): {leftover}")

    print(f"  [remove-files] Done. Removed {len(present)} file(s), "
          f"{total_chunks} chunks total.")



def _handle_schedules(args):
    """List / show / add / remove / run / daemon — scheduled prompts.

    ``args.schedules_cmd`` is the sub-sub-action (list / add / ...).
    """
    from src.agent.scheduler import (
        Schedule, ScheduleManager, Scheduler, make_agent_on_fire,
    )
    mgr = ScheduleManager()
    cmd = getattr(args, "schedules_cmd", None) or "list"

    if cmd == "list":
        items = mgr.list()
        if not items:
            print("(no schedules; add one with: "
                  "divine-sense-agent.bat schedules add --name ... --prompt ... "
                  "--type interval --interval 3600)")
            return
        print(f"{'ID':<14} {'NAME':<24} {'TYPE':<18} {'STATUS':<11} "
              f"{'ENABLED':<8} {'FIRES':<6} {'NEXT':<20} LAST")
        print("-" * 120)
        for s in items:
            # v0.8.7+: lifecycle status. ``None`` means legacy
            # schedule (treat as "active" for display purposes).
            status_label = s.status or (
                "active" if s.enabled else "disabled"
            )
            print(
                f"{s.id:<14} {(s.name or ''):<24} {s.schedule_type:<18} "
                f"{status_label:<11} "
                f"{'yes' if s.enabled else 'no':<8} "
                f"{s.fire_count:<6} "
                f"{(s.next_fire_at or '-'):<20} "
                f"{s.last_fire_at or '-'}"
            )
        return

    if cmd == "show":
        s = mgr.get(args.schedule_id)
        if not s:
            print(f"[錯誤] schedule '{args.schedule_id}' not found",
                  file=sys.stderr)
            sys.exit(2)
        for k in (
            "id", "name", "prompt", "schedule_type",
            "once_at", "cron", "interval_seconds",
            "use_web_search", "use_tools", "web_max_results",
            "enabled", "fire_count",
            "next_fire_at", "last_fire_at",
            "last_session_id", "created_at",
            # v0.8.7+: lifecycle fields.
            "status", "max_fires", "expires_at",
            "last_error", "last_error_at",
        ):
            print(f"  {k:<18} {getattr(s, k)}")
        return

    if cmd == "add":
        # v0.8.7+: validate the new lifecycle flags up-front so
        # the user gets a clean error rather than a silent
        # no-op schedule (``compute_next_fire`` would auto-disable
        # a schedule with ``max_fires=0`` because 0 >= 0 already
        # trips the cap).
        if args.max_fires is not None and args.max_fires <= 0:
            print(
                "[錯誤] --max-fires must be a positive integer",
                file=sys.stderr,
            )
            sys.exit(2)
        if args.expires_at:
            # Validate the ISO-8601 string up-front.
            try:
                from datetime import datetime
                datetime.fromisoformat(
                    args.expires_at.replace("Z", "+00:00")
                )
            except ValueError as e:
                print(
                    f"[錯誤] invalid --expires-at "
                    f"{args.expires_at!r}: {e}",
                    file=sys.stderr,
                )
                sys.exit(2)
        # v0.9.111.0 — validate the optional fire-time model pin
        # up-front (catalog probe; fail-closed like every other flag).
        llm_pin = ""
        if getattr(args, "llm", None):
            from src.agent.task_llm import coerce_llm_pin

            llm_pin, llm_err = coerce_llm_pin(args.llm)
            if llm_err:
                print(f"[錯誤] invalid --llm: {llm_err}", file=sys.stderr)
                sys.exit(2)
        if args.type == "once_at":
            if not args.once_at:
                print("[錯誤] --once-at is required for once_at type",
                      file=sys.stderr)
                sys.exit(2)
            try:
                Schedule(
                    schedule_type="once_at", once_at=args.once_at,
                ).compute_next_fire()
            except ValueError as e:
                print(f"[錯誤] invalid --once-at {args.once_at!r}: {e}",
                      file=sys.stderr)
                sys.exit(2)
        elif args.type == "cron":
            if not args.cron:
                print("[錯誤] --cron is required for cron type",
                      file=sys.stderr)
                sys.exit(2)
            try:
                Schedule(
                    schedule_type="cron", cron=args.cron,
                ).compute_next_fire()
            except ValueError as e:
                print(f"[錯誤] invalid --cron {args.cron!r}: {e}",
                      file=sys.stderr)
                sys.exit(2)
        elif args.type == "interval_seconds":
            if not args.interval or args.interval <= 0:
                print("[錯誤] --interval (positive int) required",
                      file=sys.stderr)
                sys.exit(2)
        s = Schedule(
            name=args.name,
            prompt=args.prompt,
            schedule_type=(
                "interval_seconds"
                if args.type == "interval" else args.type
            ),
            once_at=args.once_at,
            cron=args.cron,
            interval_seconds=args.interval,
            use_web_search=args.web_search,
            use_tools=not args.no_tools,
            web_max_results=args.web_max_results,
            enabled=not args.disabled,
            # v0.8.7+: lifecycle caps. ``max_fires`` validated
            # by the dataclass (must be positive or None).
            max_fires=args.max_fires,
            expires_at=args.expires_at,
            # v0.9.111.0 — validated fire-time model pin ("" = default).
            llm=llm_pin,
        )
        try:
            mgr.save(s)
        except ValueError as e:
            print(f"[錯誤] invalid schedule: {e}", file=sys.stderr)
            sys.exit(2)
        print(f"✓ added schedule {s.id} (next_fire_at={s.next_fire_at})")
        print(f"  Run the daemon with: "
              f"divine-sense-agent.bat schedules daemon")
        return

    if cmd == "remove":
        if mgr.delete(args.schedule_id):
            print(f"✓ removed schedule {args.schedule_id} (and history)")
        else:
            print(f"[錯誤] schedule '{args.schedule_id}' not found",
                  file=sys.stderr)
            sys.exit(2)
        return

    if cmd in ("enable", "disable"):
        s = mgr.get(args.schedule_id)
        if not s:
            print(f"[錯誤] schedule '{args.schedule_id}' not found",
                  file=sys.stderr)
            sys.exit(2)
        s.enabled = (cmd == "enable")
        try:
            nxt = s.compute_next_fire()
            s.next_fire_at = (
                nxt.isoformat(timespec="seconds") if nxt else None
            )
        except ValueError:
            pass
        mgr.save(s)
        state = "enabled" if s.enabled else "disabled"
        print(f"✓ {state} schedule {s.id} (next_fire_at={s.next_fire_at})")
        return

    if cmd == "run-now":
        s = mgr.get(args.schedule_id)
        if not s:
            print(f"[錯誤] schedule '{args.schedule_id}' not found",
                  file=sys.stderr)
            sys.exit(2)
        from src.agent.agent import Agent
        from src.mcp_client import McpManager
        on_fire = make_agent_on_fire(
            agent_factory=lambda: Agent(
                mcp_manager=McpManager.from_config(
                    args.mcp_config,
                    include_local_hosts=True,
                ),
            )
        )
        try:
            on_fire(s)
            print(f"✓ fired {s.id}; session={s.last_session_id}")
        except Exception as e:
            print(f"[錯誤] run-now failed: {e!r}", file=sys.stderr)
            sys.exit(1)
        return

    if cmd == "history":
        s = mgr.get(args.schedule_id)
        if not s:
            print(f"[錯誤] schedule '{args.schedule_id}' not found",
                  file=sys.stderr)
            sys.exit(2)
        items = mgr.get_history(args.schedule_id, limit=args.limit)
        if not items:
            print("(no fire records yet)")
            return
        for i, e in enumerate(items, 1):
            err = " (error)" if e.get("error") else ""
            ans = (e.get("answer") or "").replace("\n", " ")[:120]
            print(f"{i:>3}. {e.get('ts', '?')}  session={e.get('session_id', '?')}{err}")
            print(f"     prompt: {e.get('prompt', '')}")
            print(f"     answer: {ans}{'…' if len(e.get('answer','') or '')>120 else ''}")
        return

    if cmd == "daemon":
        from src.agent.agent import Agent
        from src.mcp_client import McpManager
        mgr_obj = McpManager.from_config(
            args.mcp_config,
            include_local_hosts=True,
        )
        on_fire = make_agent_on_fire(
            agent_factory=lambda: Agent(mcp_manager=mgr_obj),
        )
        sched = Scheduler(manager=mgr, on_fire=on_fire)
        items = mgr.list()
        if not items:
            print("(no schedules; nothing to do; Ctrl-C to exit)")
        else:
            print(f"[daemon] watching {len(items)} schedule(s):")
            for s in items:
                if s.enabled:
                    print(
                        f"  • {s.id}  next={s.next_fire_at}  "
                        f"({s.schedule_type})"
                    )
        sched.start()
        print("[daemon] running; Ctrl-C to stop")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[daemon] stopping...")
            sched.stop()
            try:
                mgr_obj.close_all()
            except Exception:
                pass
        return

    print(f"[錯誤] unknown schedules sub-command: {cmd!r}",
          file=sys.stderr)
    sys.exit(2)


def _handle_todo(args):
    """v0.8.8+: CLI for the multi-level todo list. Sub-commands
    mirror the TodoLocalHost MCP surface so users can drive the
    same plan state from either chat or terminal."""
    from src.core.todo_manager import TodoManager
    mgr = TodoManager()
    sub = getattr(args, "todo_cmd", None)
    if sub is None:
        print("[錯誤] todo sub-command required: "
              "list / show / add / tree / progress / complete / delete",
              file=sys.stderr)
        sys.exit(2)
    if sub == "list":
        items = mgr.list_tasks(
            parent_id=args.parent, status=args.status,
            include_descendants=args.include_descendants,
        )
        if not items:
            print("(empty)")
            return
        print(f"{'TASK_ID':<14} {'STATUS':<12} {'PRI':<6}  TITLE")
        print("-" * 60)
        for e in items:
            print(
                f"{(e.get('task_id') or '?'):<14} "
                f"{(e.get('status') or '?'):<12} "
                f"{(e.get('priority') or '?'):<6}  "
                f"{e.get('title') or ''}"
            )
        return
    if sub == "show":
        t = mgr.get(args.task_id)
        if t is None:
            print(f"[錯誤] task {args.task_id!r} not found",
                  file=sys.stderr)
            sys.exit(1)
        print(f"id:        {t.task_id}")
        print(f"parent:    {t.parent_id}")
        print(f"title:     {t.title}")
        print(f"status:    {t.status}")
        print(f"priority:  {t.priority}")
        print(f"order:     {t.order}")
        print(f"depends_on:{t.depends_on}")
        print(f"created:   {t.created_at}")
        print(f"updated:   {t.updated_at}")
        print(f"completed: {t.completed_at}")
        if t.description:
            print(f"\ndescription:\n{t.description}")
        if t.notes:
            print(f"\nnotes:\n{t.notes}")
        if t.result:
            print(f"\nresult:\n{t.result}")
        if t.error:
            print(f"\nerror:\n{t.error}")
        return
    if sub == "add":
        kwargs: dict = {
            "title": args.title,
            "description": args.description or "",
            "priority": args.priority,
        }
        if args.parent:
            kwargs["parent_id"] = args.parent
        try:
            t = mgr.create(**kwargs)
        except ValueError as e:
            print(f"[錯誤] {e}", file=sys.stderr)
            sys.exit(1)
        print(f"created task {t.task_id} status={t.status}")
        return
    if sub == "tree":
        flat = mgr.render_tree()
        if not flat:
            print("(empty)")
            return
        for n in flat:
            depth = n.get("depth", 0)
            indent = "  " * depth
            print(f"{indent}- [{n.get('status')}] {n.get('title')} "
                  f"({n.get('task_id')})")
        return
    if sub == "progress":
        s = mgr.get_progress_summary(task_id=args.task)
        print(f"scope:     {s['task_id'] or '<global>'}")
        print(f"total:     {s['total']}")
        print(f"completion:{s['completion_pct'] * 100:.1f}%")
        print("by_status:")
        for k, v in s["by_status"].items():
            if v:
                print(f"  {k:<12} {v}")
        if s["blocked"]:
            print(f"blocked:   {s['blocked']}")
        if s["ready"]:
            ids = [r["task_id"] for r in s["ready"]]
            print(f"ready:     {ids}")
        if s["in_progress"]:
            ids = [r["task_id"] for r in s["in_progress"]]
            print(f"in_progress: {ids}")
        if s["next_recommended"]:
            print(f"next:      {s['next_recommended']}")
        return
    if sub == "complete":
        t = mgr.complete(args.task_id, result=args.result or "")
        if t is None:
            print(f"[錯誤] task {args.task_id!r} not found",
                  file=sys.stderr)
            sys.exit(1)
        print(f"completed {t.task_id} status={t.status}")
        return
    if sub == "delete":
        ok = mgr.delete(args.task_id, cascade=not args.no_cascade)
        print(f"{'deleted' if ok else 'no-op'}: {args.task_id}")
        return
    print(f"[錯誤] unknown todo sub-command: {sub!r}", file=sys.stderr)
    sys.exit(2)


def _handle_rules(args):
    """v0.9.124.0+ — CLI for the agent RULE BOOK; mirrors the
    RulebookLocalHost MCP surface (list / show / add / revise /
    rollback / history) over src.agent.rulebook."""
    from src.agent import rulebook as rb
    from src.agent.rulebook import (
        load_rulebook, rules_add, rules_history, rules_revise,
        rules_rollback,
    )

    sub = getattr(args, "rules_cmd", None)
    if sub is None:
        print("[錯誤] rules sub-command required: "
              "list / show / add / revise / rollback / history",
              file=sys.stderr)
        sys.exit(2)
    if sub == "list":
        from src.agent.rulebook import parse_rules
        text = load_rulebook()
        if not text:
            print(f"(empty rulebook — "
                  f"{rb.rulebook_path()} will hold it once rules_add)")
            return
        rules = parse_rules(text)
        for r in rules:
            ev = r.get("evidence") or "-"
            print(f"{r['id']:<8} {r['title']}")
            body = (r["body"] or "").replace("\n", " ")[:120]
            print(f"         text: {body}")
            print(f"         ev:   {ev[:100]}  updated: "
                  f"{r.get('updated') or '-'}")
        print(f"(path: {rb.rulebook_path()}; {len(rules)} rules)")
        return
    if sub == "show":
        from src.agent.rulebook import _max_chars
        text = load_rulebook()
        if getattr(args, "full", False):
            path = rb.rulebook_path()
            if path and path.is_file():
                text = path.read_text(
                    encoding="utf-8", errors="replace")
            else:
                text = ""
        print(text or f"(empty — {rb.rulebook_path()})")
        if text and len(text) >= _max_chars() and not args.full:
            print(f"(capped at {_max_chars()} chars — use --full)")
        return
    if sub == "add":
        if not args.yes:
            ans = input(
                f"Add rule {args.title!r} to the rulebook? (y/N) ")
            if ans.strip().lower() not in ("y", "yes"):
                print("cancelled")
                return
        out = rules_add(args.title, args.text, args.evidence)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    if sub == "revise":
        if not args.yes:
            ans = input(
                f"Revise {args.rule_id!r} (evidence required)? (y/N) ")
            if ans.strip().lower() not in ("y", "yes"):
                print("cancelled")
                return
        out = rules_revise(args.rule_id, args.text, args.evidence)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    if sub == "rollback":
        if not args.yes:
            ans = input(
                f"Roll back the rulebook x{args.steps}? (y/N) ")
            if ans.strip().lower() not in ("y", "yes"):
                print("cancelled")
                return
        out = rules_rollback(args.steps)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    if sub == "history":
        out = rules_history(args.limit)
        if not out.get("ok"):
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return
        for e in out.get("entries", []):
            print(f"{e['mtime']:<25} {e['chars']:>7} B  {e['file']}")
        print(f"({out.get('snapshots')} snapshots)")
        diff = out.get("latest_diff") or ""
        if diff:
            print("-" * 60)
            print(diff)
        return
    print(f"[錯誤] unknown rules sub-command: {sub!r}", file=sys.stderr)
    sys.exit(2)


def _handle_skills(args):
    """v0.9.116.0+ — CLI for the SKILL.md FILE library; mirrors the
    SkillLibraryLocalHost MCP surface (import / list / show / search /
    remove / use) over src.skill_library."""
    from src.skill_library import (
        get_skill, import_skill, list_skills,
        remove_skill, search_skills, use_skill,
    )
    sub = getattr(args, "skills_cmd", None)
    if sub is None:
        print("[錯誤] skills sub-command required: "
              "list / show / search / add / remove / use",
              file=sys.stderr)
        sys.exit(2)
    if sub == "list":
        out = list_skills()
        for s in out.get("skills", []):
            if s.get("invalid"):
                print(f"{s['name']:<24} [invalid] {s['invalid']}")
                continue
            print(f"{s['name']:<24} {s.get('total_bytes', 0):>9} B  "
                  f"{(s.get('description') or '')[:80]}")
        print(f"(root: {out.get('root')}; {out.get('count')} skills)")
        return
    if sub == "show":
        print(json.dumps(get_skill(args.name),
                         ensure_ascii=False, indent=2))
        return
    if sub == "search":
        out = search_skills(args.query, limit=args.limit)
        for m in out.get("matches", []):
            print(f"{m['name']:<24} score={m['score']:<4} "
                  f"where={','.join(m['where'])}")
            print(f"  {m.get('snippet') or ''}")
        print(f"({out.get('count')} matches for {out.get('query')!r})")
        return
    if sub == "add":
        if not args.yes:
            ans = input(
                f"Import {args.source!r} into the skill library? (y/N) ")
            if ans.strip().lower() not in ("y", "yes"):
                print("cancelled")
                return
        out = import_skill(
            args.source, name=args.name,
            overwrite=args.overwrite, confirm=True)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    if sub == "remove":
        if not args.yes:
            ans = input(f"DELETE skill {args.name!r}? (y/N) ")
            if ans.strip().lower() not in ("y", "yes"):
                print("cancelled")
                return
        out = remove_skill(args.name, confirm=True)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    if sub == "use":
        out = use_skill(args.name, max_chars=args.max_chars)
        print(out.get("usage_note") or "")
        print("-" * 60)
        print(out.get("body") or "")
        return
    print(f"[錯誤] unknown skills sub-command: {sub!r}", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
