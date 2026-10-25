# -*- coding: utf-8 -*-
"""v0.9.116.0 — skill FILE library (``src/skill_library.py`` +
``SkillLibraryLocalHost`` + stdio mirror + cli ``skills``) 的契約測試。

鎖定：
* frontmatter 零依賴解析（folded / literal / 無 fence / 未閉合）；
* 名稱 sanitize（分隔符/路徑/CJK→dash/空 fallback）；
* import fail-closed（confirm 閘零副作用 / 同名衝突 / library 內來源
  / 檔數與大小 cap / 單檔與 URL 匯入語意 / github blob→raw）；
* list / get / search（CJK 分大小寫無關 + 評分 + snippet + use_hint）；
* remove confirm 閘 + missing/traversal 防護；
* host surface / LocalTool 包裝容忍 / handler 永不 raise；
* stdio parity + prompt 區塊 / ToolRoute / .env.example / manager / cli 接線。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

SKILL_TEMPLATE = (
    "---\nname: {name}\ndescription: {desc}\n---\n\n"
    "# {title}\n\nbody with 狀態詞 keywords.\n"
)


def _mk_skill(src: Path, name: str = "demo") -> None:
    src.mkdir(exist_ok=True)
    (src / "SKILL.md").write_text(
        SKILL_TEMPLATE.format(name=name, desc='"A demo skill for tests."',
                              title=name),
        encoding="utf-8")
    (src / "references").mkdir(exist_ok=True)
    (src / "references" / "terms.md").write_text("術語表\n",
                                                 encoding="utf-8")
    (src / "examples").mkdir(exist_ok=True)
    (src / "examples" / "ex.md").write_text("示例\n", encoding="utf-8")
    (src / ".hidden").write_text("hidden", encoding="utf-8")


def _ste_doc(name: str = "ste") -> str:
    return ("---\n"
            "name: " + name + "\n"
            "description: >\n"
            "  Make the agent report results by the ASD-STE100 "
            "principles.\n"
            "  Second folded line.\n"
            "---\n\n"
            "# ste-zh\n\n"
            "狀態詞固定：已完成、失败、阻塞。\n")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    monkeypatch.setenv("QDRANT_SKILLS_DIR", str(tmp_path / "skills"))
    import src.skill_library as sl
    return sl


class TestNameSanitize:
    def test_space_becomes_dash(self, env):
        assert env.sanitize_skill_name("Demo Skill") == "demo-skill"

    def test_upper_to_lower(self, env):
        assert env.sanitize_skill_name("STE-ZH") == "ste-zh"

    def test_cjk_collapses_to_tail(self, env):
        assert env.sanitize_skill_name("中文skill") == "skill"

    def test_empty_returns_empty_string(self, env):
        assert env.sanitize_skill_name("") == ""
        assert env.sanitize_skill_name("   ") == ""

    def test_traversal_and_separators_raise(self, env):
        for bad in ("../x", "a/b", "a\\b", "..", "."):
            with pytest.raises(env.SkillError):
                env.sanitize_skill_name(bad)

    def test_dots_only_stripped(self, env):
        assert env.sanitize_skill_name("...") == ""


class TestFrontmatterParser:
    def test_simple_scalar(self, env):
        fm, body = env.parse_frontmatter(
            "---\nname: ste\nlicense: MIT\n---\n\nbody text\n")
        assert fm["name"] == "ste" and fm["license"] == "MIT"
        assert "body text" in body

    def test_quoted_value_stripped(self, env):
        fm, _ = env.parse_frontmatter(
            '---\nname: "demo"\ndescription: \'x y\'\n---\nbody\n')
        assert fm["name"] == "demo" and fm["description"] == "x y"

    def test_folded_block_joins_with_space(self, env):
        fm, _ = env.parse_frontmatter(_ste_doc())
        assert fm["description"] == (
            "Make the agent report results by the ASD-STE100 principles. "
            "Second folded line.")

    def test_literal_block_joins_with_newline(self, env):
        fm, _ = env.parse_frontmatter(
            "---\nname: x\nsteps: |\n  one\n  two\n---\nbody")
        assert fm["steps"] == "one\ntwo"

    def test_no_fence(self, env):
        fm, body = env.parse_frontmatter("# just markdown\n\n狀態詞\n")
        assert fm == {} and "狀態詞" in body

    def test_unclosed_fence(self, env):
        fm, body = env.parse_frontmatter("---\nname: x\n\nbody")
        assert fm == {} and "body" in body


class TestImportConfirmGate:
    def test_no_confirm_writes_nothing(self, env, tmp_path):
        src = tmp_path / "src"
        _mk_skill(src)
        out = env.import_skill(str(src))
        assert out.get("error") and "confirm" in out["error"]
        lib = tmp_path / "skills"
        assert not lib.exists() or not list(lib.iterdir())

    def test_empty_source(self, env):
        out = env.import_skill("")
        assert out.get("error") and "confirm" in out["error"]
        with pytest.raises(env.SkillError):
            env.import_skill("", confirm=True)


class TestImportFolder:
    def test_folder_import_copies_companions(self, env, tmp_path):
        src = tmp_path / "src"
        _mk_skill(src)
        out = env.import_skill(str(src), confirm=True)
        assert out["ok"] and out["name"] == "demo"
        rels = [f["rel_path"] for f in out["files"]]
        assert rels == ["SKILL.md", "examples/ex.md",
                        "references/terms.md"]
        assert not any(".hidden" in r for r in rels)
        lib = tmp_path / "skills" / "demo"
        assert (lib / "SKILL.md").is_file()
        assert (lib / "references" / "terms.md").is_file()
        meta = json.loads(
            (lib / "_skill_meta.json").read_text(encoding="utf-8"))
        assert meta["name"] == "demo"
        assert meta["source_type"] == "local-folder"

    def test_name_from_frontmatter_beats_folder_name(self, env,
                                                     tmp_path):
        src = tmp_path / "weird-folder"
        _mk_skill(src, name="inside-name")
        out = env.import_skill(str(src), confirm=True)
        assert out["name"] == "inside-name"

    def test_explicit_name_override(self, env, tmp_path):
        src = tmp_path / "src"
        _mk_skill(src)
        out = env.import_skill(str(src), name="My Choice",
                               confirm=True)
        assert out["name"] == "my-choice"

    def test_conflict_refused_library_intact(self, env, tmp_path):
        src = tmp_path / "src"
        _mk_skill(src)
        env.import_skill(str(src), confirm=True)
        with pytest.raises(env.SkillError) as ctx:
            env.import_skill(str(src), confirm=True)
        assert "overwrite" in str(ctx.value)
        assert env.get_skill("demo")["ok"]

    def test_conflict_overwrite_replaces(self, env, tmp_path):
        src = tmp_path / "src"
        _mk_skill(src)
        env.import_skill(str(src), confirm=True)
        (src / "SKILL.md").write_text(
            SKILL_TEMPLATE.format(name="demo", desc='"v2."',
                                  title="demo"),
            encoding="utf-8")
        out = env.import_skill(str(src), confirm=True, overwrite=True)
        assert out["ok"]
        assert env.use_skill("demo")["description"] == "v2."

    def test_missing_source(self, env, tmp_path):
        with pytest.raises(env.SkillError):
            env.import_skill(str(tmp_path / "nope"), confirm=True)

    def test_folder_without_skill_doc(self, env, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        with pytest.raises(env.SkillError):
            env.import_skill(str(d), confirm=True)

    def test_source_inside_library_refused(self, env, tmp_path):
        src = tmp_path / "src"
        _mk_skill(src)
        env.import_skill(str(src), confirm=True)
        with pytest.raises(env.SkillError):
            env.import_skill(str(tmp_path / "skills" / "demo"),
                             confirm=True)

    def test_file_count_cap_whole_batch(self, env, tmp_path,
                                        monkeypatch):
        monkeypatch.setenv("QDRANT_SKILL_LIB_MAX_FILES", "2")
        src = tmp_path / "src"
        _mk_skill(src)  # SKILL.md + 2 companions = 3 > 2
        with pytest.raises(env.SkillError):
            env.import_skill(str(src), confirm=True)

    def test_big_file_skipped_with_note(self, env, tmp_path,
                                        monkeypatch):
        monkeypatch.setenv("QDRANT_SKILL_FILE_MAX_BYTES", "10")
        src = tmp_path / "src"
        _mk_skill(src)
        (src / "references" / "terms.md").write_text("x" * 100,
                                                     encoding="utf-8")
        out = env.import_skill(str(src), confirm=True)
        rels = [f["rel_path"] for f in out["files"]]
        assert "references/terms.md" not in rels
        assert any("skipped references/terms.md" in n
                   for n in out["notes"])

    def test_single_file_import(self, env, tmp_path):
        f = tmp_path / "just.md"
        f.write_text(_ste_doc("solo"), encoding="utf-8")
        out = env.import_skill(str(f), confirm=True)
        assert out["name"] == "solo"
        assert out["source_type"] == "local-file"
        assert [x["rel_path"] for x in out["files"]] == ["SKILL.md"]
        assert any("companion" in n.lower() for n in out["notes"])

    def test_single_file_name_falls_back_to_stem(self, env, tmp_path):
        f = tmp_path / "nostem.md"
        f.write_text("# no frontmatter\n狀態詞 body\n", encoding="utf-8")
        out = env.import_skill(str(f), confirm=True)
        assert out["name"] == "nostem"


class TestImportURL:
    def test_blob_to_raw_conversion(self, env):
        assert env.github_raw_url(
            "https://github.com/o/r/blob/main/SKILL.md") == (
            "https://raw.githubusercontent.com/o/r/main/SKILL.md")
        assert env.github_raw_url(
            "https://github.com/o/r/raw/master/x/SKILL.md") == (
            "https://raw.githubusercontent.com/o/r/master/x/SKILL.md")
        assert env.github_raw_url(
            "https://example.com/a.md") == "https://example.com/a.md"

    def test_url_import_with_injected_fetch(self, env, monkeypatch):
        monkeypatch.setattr(env, "fetch_url_text",
                            lambda u: _ste_doc("remote"))
        out = env.import_skill(
            "https://github.com/o/r/blob/main/SKILL.md", confirm=True)
        assert out["ok"] and out["name"] == "remote"
        assert out["source_type"] == "url"
        assert any("companion" in n.lower() for n in out["notes"])
        assert (env.skills_dir() / "remote" / "SKILL.md").is_file()

    def test_url_import_explicit_name(self, env, monkeypatch):
        monkeypatch.setattr(env, "fetch_url_text",
                            lambda u: _ste_doc("remote"))
        out = env.import_skill("https://x/y.md", name="Alias One",
                               confirm=True)
        assert out["name"] == "alias-one"

    def test_url_empty_body_refused(self, env, monkeypatch):
        monkeypatch.setattr(env, "fetch_url_text",
                            lambda u: "---\nname: e\n---\n\n")
        with pytest.raises(env.SkillError):
            env.import_skill("https://x/SKILL.md", confirm=True)


class TestListGetSearch:
    def _two(self, env, tmp_path):
        # write straight INTO the library (the query list/get/search
        # surface reads the library, not importable sources)
        a = tmp_path / "skills" / "ste"
        a.mkdir(parents=True)
        a.joinpath("SKILL.md").write_text(_ste_doc("ste"),
                                          encoding="utf-8")
        b = tmp_path / "skills" / "demo"
        b.mkdir(parents=True)
        b.joinpath("SKILL.md").write_text(
            SKILL_TEMPLATE.format(name="demo",
                                  desc='"demo keyword."',
                                  title="demo"),
            encoding="utf-8")

    def test_list_sorted_with_fields(self, env, tmp_path):
        self._two(env, tmp_path)
        out = env.list_skills()
        names = [s["name"] for s in out["skills"]]
        assert names == ["demo", "ste"]
        ste = out["skills"][1]
        assert ste["description"].startswith("Make the agent report")
        assert any(f["rel_path"] == "SKILL.md" for f in ste["files"])
        assert ste["total_bytes"] > 0 and ste["mtime"]

    def test_list_invalid_folder_surfaced(self, env, tmp_path):
        self._two(env, tmp_path)
        (tmp_path / "skills" / "broken").mkdir()
        out = env.list_skills()
        assert any(s.get("invalid") for s in out["skills"])

    def test_get_text_use_hint_and_provenance(self, env, tmp_path):
        src = tmp_path / "s"
        _mk_skill(src)
        env.import_skill(str(src), confirm=True)
        out = env.get_skill("demo")
        assert out["ok"] and out["frontmatter"]["name"] == "demo"
        assert "狀態詞" in out["text"]
        assert "read_file" in out["use_hint"]
        assert out["skill_md_path"].replace("\\", "/").endswith(
            "skills/demo/SKILL.md")
        assert out["source_type"] == "local-folder"

    def test_get_missing(self, env):
        with pytest.raises(env.SkillError):
            env.get_skill("ghost")

    def test_search_name_first(self, env, tmp_path):
        self._two(env, tmp_path)
        out = env.search_skills("demo")
        assert out["matches"][0]["name"] == "demo"
        assert "name" in out["matches"][0]["where"]

    def test_search_body_cjk(self, env, tmp_path):
        self._two(env, tmp_path)
        out = env.search_skills("狀態詞")
        names = [m["name"] for m in out["matches"]]
        assert "ste" in names
        assert out["matches"][0]["snippet"]

    def test_search_description_hit(self, env, tmp_path):
        self._two(env, tmp_path)
        out = env.search_skills("ASD-STE100")
        assert [m["name"] for m in out["matches"]][0] == "ste"
        assert "description" in out["matches"][0]["where"]

    def test_search_empty_query(self, env):
        with pytest.raises(env.SkillError):
            env.search_skills("   ")

    def test_search_limit(self, env, tmp_path):
        self._two(env, tmp_path)
        for i in range(5):
            d = tmp_path / "skills" / f"kw{i}"
            d.mkdir(parents=True)
            d.joinpath("SKILL.md").write_text(
                f"---\nname: kw{i}\n---\n\ncommon 狀態詞 {i}\n",
                encoding="utf-8")
        out = env.search_skills("狀態詞", limit=3)
        assert out["count"] == 3


class TestUse:
    def test_use_returns_body_and_note(self, env, tmp_path):
        src = tmp_path / "s"
        _mk_skill(src)
        env.import_skill(str(src), confirm=True)
        out = env.use_skill("demo")
        assert out["ok"] and out["name"] == "demo"
        assert out["body"].startswith("# demo")
        assert "read_file" in out["usage_note"]
        assert out["skill_md_path"].replace("\\", "/").endswith(
            "skills/demo/SKILL.md")

    def test_use_truncation_note(self, env, tmp_path, monkeypatch):
        src = tmp_path / "s"
        _mk_skill(src)
        env.import_skill(str(src), confirm=True)
        monkeypatch.setenv("QDRANT_SKILL_LIB_BODY_CHARS", "30")
        out = env.use_skill("demo")
        assert out["truncated"] and "truncated at 30" in out["body"]

    def test_use_ste_folded_description(self, env, tmp_path):
        d = tmp_path / "skills" / "ste"
        d.mkdir(parents=True)
        d.joinpath("SKILL.md").write_text(_ste_doc(), encoding="utf-8")
        out = env.use_skill("ste")
        assert out["description"].startswith("Make the agent report")
        assert "狀態詞固定" in out["body"]


class TestRemove:
    def _setup(self, env, tmp_path):
        src = tmp_path / "s"
        _mk_skill(src)
        env.import_skill(str(src), confirm=True)
        return tmp_path / "skills" / "demo"

    def test_no_confirm_refused(self, env, tmp_path):
        p = self._setup(env, tmp_path)
        out = env.remove_skill("demo")
        assert out.get("error") and "confirm" in out["error"]
        assert p.exists()

    def test_confirm_removes(self, env, tmp_path):
        p = self._setup(env, tmp_path)
        out = env.remove_skill("demo", confirm=True)
        assert out["ok"] and out["removed"] == "demo"
        assert out["files_count"] == 3
        assert not p.exists()

    def test_missing(self, env):
        with pytest.raises(env.SkillError):
            env.remove_skill("ghost", confirm=True)

    def test_traversal_refused(self, env, tmp_path):
        self._setup(env, tmp_path)
        for bad in ("../skills", "..", "demo/../ste"):
            with pytest.raises(env.SkillError):
                env.remove_skill(bad, confirm=True)


class TestHostSurface:
    def _host(self):
        from src.mcp_client.host import SkillLibraryLocalHost

        return SkillLibraryLocalHost()

    def test_tool_names_and_required_fields(self):
        h = self._host()
        names = [t.name for t in h.list_tools()]
        assert names == ["skill_import", "skill_list", "skill_get",
                         "skill_search", "skill_remove", "skill_use"]
        imp = next(t for t in h.list_tools()
                   if t.name == "skill_import")
        assert imp.input_schema["required"] == ["source"]
        assert "confirm" in imp.input_schema["properties"]
        assert imp.server_name == "skill-library-local"
        rm = next(t for t in h.list_tools() if t.name == "skill_remove")
        assert rm.input_schema["required"] == ["name"]

    def test_wrapper_dict_coercion_on_local_tool(self, env, tmp_path):
        src = tmp_path / "s"
        _mk_skill(src)
        env.import_skill(str(src), confirm=True)
        h = self._host()
        tools = {t.name: t for t in h.list_tools()}
        res = tools["skill_list"]({"args": {}})
        assert not res.is_error
        assert json.loads(res.to_message_text())["ok"] is True

    def test_handlers_never_raise(self, env):
        h = self._host()
        assert h._get({"name": "ghost"}).get("error")
        assert h._use({"name": "ghost"}).get("error")
        assert h._search({"query": ""}).get("error")
        assert "confirm" in h._import({"source": "x"})["error"]
        assert "confirm" in h._remove({"name": "x"})["error"]
        assert h._import({"source": ""}).get("error")
        assert h._import({"source":
                          str(__import__("os").path.sep.join(
                              ("C:", "<bad>", "x")))}).get("error")

    def test_host_import_remove_roundtrip(self, env, tmp_path):
        src = tmp_path / "s"
        _mk_skill(src)
        h = self._host()
        assert h._import({"source": str(src), "confirm": True})["ok"]
        use = h._use({"name": "demo"})
        assert use["ok"] and use["body"].startswith("# demo")
        assert h._remove({"name": "demo", "confirm": True})["ok"]
        assert h._use({"name": "demo"}).get("error")


class TestStdioParity:
    def test_surface_matches(self):
        from src.mcp_server.skill_library_server import (
            SkillLibraryMCPServer,
        )

        srv = SkillLibraryMCPServer()
        spec_names = [t.name for t in srv._tool_specs()]
        from src.mcp_client.host import SkillLibraryLocalHost

        host_names = [t.name for t in SkillLibraryLocalHost()
                      .list_tools()]
        assert spec_names == host_names == [
            "skill_import", "skill_list", "skill_get",
            "skill_search", "skill_remove", "skill_use",
        ]


class TestWiring:
    def test_system_prompt_block_present(self):
        src = Path("src/agent/agent.py").read_text(encoding="utf-8")
        assert "Skill file library (v0.9.116+, skill-library-" in src
        assert src.count("Skill file library") == 1
        assert "skill_use" in src

    def test_toolroute_triggers(self):
        import src.agent.agent as A

        hit_lib = any(srv == "skill-library-local"
                      for pat, srv in A._TOOLROUTE_SERVER_TRIGGERS
                      if re.search(pat, "幫我把這個技能庫的 ste 匯入"))
        assert hit_lib
        # the compiler server mapping is untouched for generic skill talk
        hit_plan = any(srv == "tacti-local"
                       for pat, srv in A._TOOLROUTE_SERVER_TRIGGERS
                       if re.search(pat, "把這個 workflow 編譯成計畫"))
        assert hit_plan

    def test_env_example_block(self):
        src = Path(".env.example").read_text(encoding="utf-8")
        assert "QDRANT_SKILLS_DIR" in src
        assert "skill-library-local" in src
        assert "QDRANT_SKILL_LIB_BODY_CHARS" in src

    def test_manager_registration(self):
        src = Path("src/mcp_client/manager.py").read_text(
            encoding="utf-8")
        assert "SkillLibraryLocalHost()" in src
        assert "_skill_library_local_host" in src
        assert 'mgr.failed["skill-library-local"]' in src

    def test_manager_actually_seeds_the_host(self):
        """NameError-proof registration check (behavioral, not just
        a source grep — the v0.9.116 boot seed failed with exactly
        'name SkillLibraryLocalHost is not defined')."""
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(path="/nonexistent/mcp.json")
        names = {t.name for t in mgr.list_tools()
                 if t.server_name == "skill-library-local"}
        assert names == {"skill_import", "skill_list", "skill_get",
                         "skill_search", "skill_remove", "skill_use"}
        assert "skill-library-local" not in mgr.failed

    def test_cli_wiring(self):
        src = Path("cli.py").read_text(encoding="utf-8")
        assert '"skills"' in src
        assert "_handle_skills" in src
        assert 'args.command == "skills"' in src
        assert '"skills_cmd"' in src

    def test_stdio_server_module_wiring(self):
        src = Path(
            "src/mcp_server/skill_library_server.py"
        ).read_text(encoding="utf-8")
        assert "python -m src.mcp_server.skill_library_server" in src
        assert "skill_use" in src

# ── v0.9.120.2 — two skill systems routing (question-side) ───────
class TestTwoSkillSystemsRouting:
    """app.log 2026-10-12 incidents: a QUESTION naming the intent
    mounted NOTHING (the v0.9.104.1 trigger rows were prev-REPLY
    mining only) — 「目前有那些skill」→ the LLM improvised
    script_list; 「list skill library」→ a Qdrant search_documents;
    「做成skill存檔」→ a hand-written write_file "skill" inside the
    import staging. Fix: the same curated trigger rows now union
    from the QUESTION too + the library row learns generic
    list/save intents."""

    @staticmethod
    def _route(question):
        import src.agent.agent as A

        servers, _ = A._toolroute_servers_for(None, question, "")
        return servers

    def test_listing_question_mounts_library_not_tacti(self):
        s = self._route("目前有那些skill")
        assert "tacti-local" not in s
        assert "skill-library-local" in s

    def test_list_skill_library_mounts_library(self):
        assert "skill-library-local" in self._route(
            "list skill library")

    def test_save_as_skill_mounts_library(self):
        for q in ("把本次去背景方式做成skill存檔",
                  "把這個做法存成 skill",
                  "幫我列出 skills"):
            assert "skill-library-local" in self._route(q), q

    def test_union_only_mounts_nothing_extra(self):
        s = self._route("today's weather")
        assert "tacti-local" not in s
        assert "skill-library-local" not in s

    def test_prompt_has_two_system_clarification(self):
        import src.agent.agent as A

        a = A.Agent.__new__(A.Agent)
        a.last_tool_dispatches = []
        p = a._system_prompt(tools_enabled=True)
        assert "TACTI vs SKILL" in p
        assert "NEVER script_list, NEVER search_documents" in p
        assert "NEVER hand-write a 「skill」" in p

    def test_prompt_legacy_mode_excluded(self):
        import src.agent.agent as A

        a = A.Agent.__new__(A.Agent)
        a.last_tool_dispatches = []
        p = a._system_prompt(tools_enabled=False)
        assert "TACTI vs SKILL" not in p
