"""
测试 SkillTool 加载真实工作区 skills/ 目录中的技能。

工作区结构（测试运行时必须存在）：
  skills/
    web_search/SKILL.md
    file_edit/SKILL.md
    Administrator/SKILL.md         (12306 高铁票, frontmatter name="12306")
    skills-AMap-.../SKILL.md       (高德地图, metadata.openclaw 含 env+bins)
"""
from __future__ import annotations

import asyncio
import os
import pytest
from pathlib import Path

from packages.tools import SkillTool

# 工作区根目录（tests/ 的上一级）
WORKSPACE_ROOT = Path(__file__).parent.parent

# 高德地图技能目录名（长哈希名）
AMAP_DIR = next(
    (d for d in (WORKSPACE_ROOT / "skills").iterdir()
     if d.is_dir() and "amap" in d.name.lower()),
    None,
)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# list 动作
# ---------------------------------------------------------------------------

class TestSkillToolList:
    def setup_method(self):
        self.tool = SkillTool(workspace_root=WORKSPACE_ROOT)

    def test_list_returns_ok(self):
        result = run(self.tool.arun({"action": "list"}))
        assert result["ok"] is True
        assert result["action"] == "list"
        assert result["tool_name"] == "skill"

    def test_list_finds_simple_skills(self):
        result = run(self.tool.arun({"action": "list"}))
        names = {s["name"] for s in result["skills"]}
        assert "web_search" in names
        assert "file_edit" in names

    def test_list_finds_12306_by_frontmatter_name(self):
        """12306 SKILL.md 的 frontmatter name 字段是 '12306'，目录名是 'Administrator'。"""
        result = run(self.tool.arun({"action": "list"}))
        names = {s["name"] for s in result["skills"]}
        assert "12306" in names

    def test_list_finds_amap_skill(self):
        result = run(self.tool.arun({"action": "list"}))
        names = {s["name"] for s in result["skills"]}
        assert "amap-lbs-skill" in names

    def test_list_count_matches_metadata(self):
        result = run(self.tool.arun({"action": "list"}))
        assert result["metadata"]["count"] == len(result["skills"])

    def test_list_skills_have_required_fields(self):
        result = run(self.tool.arun({"action": "list"}))
        for skill in result["skills"]:
            for field in ("name", "title", "description", "path", "root",
                          "disable_model_invocation", "user_invocable",
                          "has_scripts", "requires_env", "requires_bins"):
                assert field in skill, f"missing field '{field}' in skill {skill.get('name')}"

    def test_list_query_filters_correctly(self):
        result = run(self.tool.arun({"action": "list", "query": "web"}))
        names = {s["name"] for s in result["skills"]}
        assert "web_search" in names
        assert "file_edit" not in names

    def test_list_query_no_match_returns_empty(self):
        result = run(self.tool.arun({"action": "list", "query": "nonexistent_xyz_skill"}))
        assert result["skills"] == []
        assert result["metadata"]["count"] == 0

    def test_list_skills_sorted_by_path(self):
        result = run(self.tool.arun({"action": "list"}))
        paths = [s["path"] for s in result["skills"]]
        assert paths == sorted(paths)

    def test_list_title_parsed_from_heading(self):
        result = run(self.tool.arun({"action": "list"}))
        by_name = {s["name"]: s for s in result["skills"]}
        assert by_name["web_search"]["title"] == "Web Search Skill"
        assert by_name["file_edit"]["title"] == "File Edit Skill"

    def test_list_amap_has_env_deps(self):
        result = run(self.tool.arun({"action": "list"}))
        by_name = {s["name"]: s for s in result["skills"]}
        amap = by_name.get("amap-lbs-skill", {})
        assert "AMAP_WEBSERVICE_KEY" in amap.get("requires_env", [])

    def test_list_amap_has_bin_deps(self):
        result = run(self.tool.arun({"action": "list"}))
        by_name = {s["name"]: s for s in result["skills"]}
        amap = by_name.get("amap-lbs-skill", {})
        assert "node" in amap.get("requires_bins", [])

    def test_list_12306_has_bin_deps(self):
        result = run(self.tool.arun({"action": "list"}))
        by_name = {s["name"]: s for s in result["skills"]}
        skill_12306 = by_name.get("12306", {})
        assert "node" in skill_12306.get("requires_bins", [])


# ---------------------------------------------------------------------------
# inspect 动作
# ---------------------------------------------------------------------------

class TestSkillToolInspect:
    def setup_method(self):
        self.tool = SkillTool(workspace_root=WORKSPACE_ROOT)

    def test_inspect_by_name(self):
        result = run(self.tool.arun({"action": "inspect", "skill_name": "web_search"}))
        assert result["ok"] is True
        assert result["skill"]["name"] == "web_search"
        assert result["skill"]["title"] == "Web Search Skill"

    def test_inspect_description_non_empty(self):
        result = run(self.tool.arun({"action": "inspect", "skill_name": "file_edit"}))
        assert result["skill"]["description"] != ""

    def test_inspect_by_path(self):
        path_arg = str(WORKSPACE_ROOT / "skills" / "web_search")
        result = run(self.tool.arun({"action": "inspect", "path": path_arg}))
        assert result["skill"]["name"] == "web_search"

    def test_inspect_has_skill_dir_field(self):
        result = run(self.tool.arun({"action": "inspect", "skill_name": "web_search"}))
        assert "skill_dir" in result["skill"]
        assert "directory" in result["skill"]
        assert "root" in result["skill"]

    def test_inspect_12306_by_frontmatter_name(self):
        """应该能用 frontmatter 中的 name='12306' 查找，即使目录名是 Administrator。"""
        result = run(self.tool.arun({"action": "inspect", "skill_name": "12306"}))
        assert result["skill"]["name"] == "12306"

    def test_inspect_amap_openclaw_fields(self):
        result = run(self.tool.arun({"action": "inspect", "skill_name": "amap-lbs-skill"}))
        skill = result["skill"]
        assert "AMAP_WEBSERVICE_KEY" in skill["requires_env"]
        assert "node" in skill["requires_bins"]
        assert skill["version"] == "2.0.0"

    def test_inspect_unknown_skill_raises(self):
        with pytest.raises(FileNotFoundError, match="skill not found"):
            run(self.tool.arun({"action": "inspect", "skill_name": "no_such_skill"}))


# ---------------------------------------------------------------------------
# read 动作
# ---------------------------------------------------------------------------

class TestSkillToolRead:
    def setup_method(self):
        self.tool = SkillTool(workspace_root=WORKSPACE_ROOT)

    def test_read_returns_content(self):
        result = run(self.tool.arun({"action": "read", "skill_name": "web_search"}))
        assert result["ok"] is True
        assert "# Web Search Skill" in result["content"]

    def test_read_content_has_steps(self):
        result = run(self.tool.arun({"action": "read", "skill_name": "web_search"}))
        assert "## Steps" in result["content"]

    def test_read_metadata_title(self):
        result = run(self.tool.arun({"action": "read", "skill_name": "web_search"}))
        assert result["metadata"]["title"] == "Web Search Skill"

    def test_read_metadata_has_skill_dir(self):
        result = run(self.tool.arun({"action": "read", "skill_name": "web_search"}))
        assert "skill_dir" in result["metadata"]

    def test_read_12306_by_frontmatter_name(self):
        result = run(self.tool.arun({"action": "read", "skill_name": "12306"}))
        assert result["skill_name"] == "12306"
        assert "12306" in result["content"] or "Train" in result["content"]

    def test_read_missing_args_raises(self):
        with pytest.raises(ValueError, match="skill.read requires"):
            run(self.tool.arun({"action": "read"}))

    def test_read_unknown_skill_raises(self):
        with pytest.raises(FileNotFoundError, match="skill not found"):
            run(self.tool.arun({"action": "read", "skill_name": "ghost_skill"}))


# ---------------------------------------------------------------------------
# invoke 动作 (Phase 3)
# ---------------------------------------------------------------------------

class TestSkillToolInvoke:
    def setup_method(self):
        self.tool = SkillTool(workspace_root=WORKSPACE_ROOT)

    def test_invoke_renders_basedir(self):
        """12306 SKILL.md 使用 {baseDir}/scripts/query.mjs，invoke 后应替换为真实路径。"""
        result = run(self.tool.arun({"action": "invoke", "skill_name": "12306"}))
        assert result["ok"] is True
        assert "{baseDir}" not in result["rendered_content"]
        assert "scripts/query.mjs" in result["rendered_content"]

    def test_invoke_renders_arguments(self, tmp_path: Path):
        skill_dir = tmp_path / "skills" / "greeter"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: greeter\ndescription: Greet someone\n---\nHello $ARGUMENTS!\n",
            encoding="utf-8",
        )
        tool = SkillTool(workspace_root=tmp_path)
        result = run(tool.arun({"action": "invoke", "skill_name": "greeter", "arguments": "World"}))
        assert "Hello World!" in result["rendered_content"]
        assert "$ARGUMENTS" not in result["rendered_content"]

    def test_invoke_renders_positional_args(self, tmp_path: Path):
        skill_dir = tmp_path / "skills" / "migrator"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: migrator\ndescription: Migrate\n---\nMigrate $0 from $1 to $2\n",
            encoding="utf-8",
        )
        tool = SkillTool(workspace_root=tmp_path)
        result = run(tool.arun({
            "action": "invoke",
            "skill_name": "migrator",
            "arguments": "SearchBar React Vue",
        }))
        assert "Migrate SearchBar from React to Vue" in result["rendered_content"]

    def test_invoke_renders_named_args(self, tmp_path: Path):
        skill_dir = tmp_path / "skills" / "fixer"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: fixer\ndescription: Fix issue\narguments: [issue, branch]\n---\n"
            "Fix $issue on $branch\n",
            encoding="utf-8",
        )
        tool = SkillTool(workspace_root=tmp_path)
        result = run(tool.arun({
            "action": "invoke",
            "skill_name": "fixer",
            "arguments": "123 main",
        }))
        assert "Fix 123 on main" in result["rendered_content"]

    def test_invoke_replaces_skill_dir_variants(self, tmp_path: Path):
        skill_dir = tmp_path / "skills" / "multi"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: multi\ndescription: test\n---\n"
            "{baseDir} {SKILL_DIR} ${CLAUDE_SKILL_DIR}\n",
            encoding="utf-8",
        )
        tool = SkillTool(workspace_root=tmp_path)
        result = run(tool.arun({"action": "invoke", "skill_name": "multi"}))
        expected_dir = str(skill_dir.resolve())
        rendered = result["rendered_content"]
        assert "{baseDir}" not in rendered
        assert "{SKILL_DIR}" not in rendered
        assert "${CLAUDE_SKILL_DIR}" not in rendered
        assert expected_dir in rendered

    def test_invoke_missing_args_raises(self):
        with pytest.raises(ValueError, match="skill.invoke requires"):
            run(self.tool.arun({"action": "invoke"}))


# ---------------------------------------------------------------------------
# list_files 动作 (Phase 4)
# ---------------------------------------------------------------------------

class TestSkillToolListFiles:
    def setup_method(self):
        self.tool = SkillTool(workspace_root=WORKSPACE_ROOT)

    def test_list_files_amap_has_scripts(self):
        result = run(self.tool.arun({"action": "list_files", "skill_name": "amap-lbs-skill"}))
        assert result["ok"] is True
        roles = {f["role"] for f in result["files"]}
        assert "entrypoint" in roles
        assert "script" in roles

    def test_list_files_amap_entrypoint_is_skill_md(self):
        result = run(self.tool.arun({"action": "list_files", "skill_name": "amap-lbs-skill"}))
        entrypoints = [f for f in result["files"] if f["role"] == "entrypoint"]
        assert len(entrypoints) == 1
        assert entrypoints[0]["path"] == "SKILL.md"

    def test_list_files_amap_has_js_scripts(self):
        result = run(self.tool.arun({"action": "list_files", "skill_name": "amap-lbs-skill"}))
        script_paths = [f["path"] for f in result["files"] if f["role"] == "script"]
        assert any(p.endswith(".js") for p in script_paths)

    def test_list_files_simple_skill(self):
        result = run(self.tool.arun({"action": "list_files", "skill_name": "web_search"}))
        assert result["metadata"]["count"] == 1
        assert result["files"][0]["path"] == "SKILL.md"
        assert result["files"][0]["role"] == "entrypoint"

    def test_list_files_missing_args_raises(self):
        with pytest.raises(ValueError, match="skill.list_files requires"):
            run(self.tool.arun({"action": "list_files"}))


# ---------------------------------------------------------------------------
# check_deps 动作 (Phase 4)
# ---------------------------------------------------------------------------

class TestSkillToolCheckDeps:
    def setup_method(self):
        self.tool = SkillTool(workspace_root=WORKSPACE_ROOT)

    def test_check_deps_amap_structure(self):
        result = run(self.tool.arun({"action": "check_deps", "skill_name": "amap-lbs-skill"}))
        assert result["ok"] is True
        assert result["action"] == "check_deps"
        assert result["skill_name"] == "amap-lbs-skill"
        # 结果字段完整性
        for field in ("deps_satisfied", "missing_env", "present_env",
                      "missing_bins", "present_bins", "install_instructions"):
            assert field in result

    def test_check_deps_amap_env_reported(self):
        result = run(self.tool.arun({"action": "check_deps", "skill_name": "amap-lbs-skill"}))
        all_env = result["missing_env"] + result["present_env"]
        assert "AMAP_WEBSERVICE_KEY" in all_env

    def test_check_deps_amap_bin_reported(self):
        result = run(self.tool.arun({"action": "check_deps", "skill_name": "amap-lbs-skill"}))
        all_bins = result["missing_bins"] + result["present_bins"]
        assert "node" in all_bins

    def test_check_deps_12306_bin_reported(self):
        result = run(self.tool.arun({"action": "check_deps", "skill_name": "12306"}))
        all_bins = result["missing_bins"] + result["present_bins"]
        assert "node" in all_bins

    def test_check_deps_no_deps_skill(self, tmp_path: Path):
        skill_dir = tmp_path / "skills" / "nodeps"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: nodeps\ndescription: no deps\n---\nSome content.\n",
            encoding="utf-8",
        )
        tool = SkillTool(workspace_root=tmp_path)
        result = run(tool.arun({"action": "check_deps", "skill_name": "nodeps"}))
        assert result["deps_satisfied"] is True
        assert result["missing_env"] == []
        assert result["missing_bins"] == []

    def test_check_deps_missing_bin(self, tmp_path: Path):
        skill_dir = tmp_path / "skills" / "needsbin"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            '---\nname: needsbin\ndescription: needs a bin\n'
            'metadata: {"openclaw":{"requires":{"bins":["__nonexistent_binary_xyz__"]}}}\n'
            '---\nContent.\n',
            encoding="utf-8",
        )
        tool = SkillTool(workspace_root=tmp_path)
        result = run(tool.arun({"action": "check_deps", "skill_name": "needsbin"}))
        assert result["deps_satisfied"] is False
        assert "__nonexistent_binary_xyz__" in result["missing_bins"]

    def test_check_deps_missing_args_raises(self):
        with pytest.raises(ValueError, match="skill.check_deps requires"):
            run(self.tool.arun({"action": "check_deps"}))


# ---------------------------------------------------------------------------
# build_skill_listing (Phase 2)
# ---------------------------------------------------------------------------

class TestBuildSkillListing:
    def setup_method(self):
        self.tool = SkillTool(workspace_root=WORKSPACE_ROOT)

    def test_listing_is_non_empty(self):
        listing = self.tool.build_skill_listing()
        assert listing != ""
        assert "## Available Skills" in listing

    def test_listing_includes_skill_names(self):
        listing = self.tool.build_skill_listing()
        assert "web_search" in listing
        assert "amap-lbs-skill" in listing
        assert "12306" in listing

    def test_listing_includes_emoji(self):
        listing = self.tool.build_skill_listing()
        assert "🚄" in listing  # 12306 emoji

    def test_listing_includes_deps_note(self):
        listing = self.tool.build_skill_listing()
        # AMap requires env AMAP_WEBSERVICE_KEY
        assert "AMAP_WEBSERVICE_KEY" in listing

    def test_listing_excludes_disable_model_invocation(self, tmp_path: Path):
        skill_dir = tmp_path / "skills" / "manualonly"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: manualonly\ndescription: manual only\ndisable-model-invocation: true\n---\nContent.\n",
            encoding="utf-8",
        )
        tool = SkillTool(workspace_root=tmp_path)
        listing = tool.build_skill_listing()
        assert "manualonly" not in listing

    def test_listing_empty_when_no_skills(self, tmp_path: Path):
        # No skill roots at all
        tool = SkillTool(workspace_root=tmp_path, skill_roots=[tmp_path / "nonexistent"])
        listing = tool.build_skill_listing()
        assert listing == ""


# ---------------------------------------------------------------------------
# 无效 action
# ---------------------------------------------------------------------------

class TestSkillToolInvalidAction:
    def test_unknown_action_raises(self):
        tool = SkillTool(workspace_root=WORKSPACE_ROOT)
        with pytest.raises(ValueError, match="skill.action must be"):
            run(tool.arun({"action": "delete"}))


# ---------------------------------------------------------------------------
# 隔离测试（tmp_path）
# ---------------------------------------------------------------------------

class TestSkillToolIsolated:
    def test_custom_skill_roots(self, tmp_path: Path):
        custom_root = tmp_path / "custom_skills"
        skill_dir = custom_root / "my_skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "# My Custom Skill\n\nThis skill does something special.\n",
            encoding="utf-8",
        )
        tool = SkillTool(workspace_root=tmp_path, skill_roots=[custom_root])
        result = run(tool.arun({"action": "list"}))
        assert result["metadata"]["count"] == 1
        assert result["skills"][0]["name"] == "my_skill"
        assert result["skills"][0]["title"] == "My Custom Skill"
        assert result["skills"][0]["description"] == "This skill does something special."

    def test_skill_without_heading_uses_dir_name(self, tmp_path: Path):
        skill_dir = tmp_path / "skills" / "no_heading"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "Just plain text, no heading.\n", encoding="utf-8"
        )
        tool = SkillTool(workspace_root=tmp_path)
        result = run(tool.arun({"action": "list"}))
        skill = result["skills"][0]
        assert skill["name"] == "no_heading"
        assert skill["title"] == "no_heading"
        assert skill["description"] == "Just plain text, no heading."

    def test_multiple_skills_sorted(self, tmp_path: Path):
        for name in ["zebra", "apple", "mango"]:
            d = tmp_path / "skills" / name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(
                f"# {name.title()} Skill\n\nContent.\n", encoding="utf-8"
            )
        tool = SkillTool(workspace_root=tmp_path)
        result = run(tool.arun({"action": "list"}))
        paths = [s["path"] for s in result["skills"]]
        assert paths == sorted(paths)
        assert len(paths) == 3

    def test_read_via_skill_path_directly(self, tmp_path: Path):
        skill_dir = tmp_path / "skills" / "demo"
        skill_dir.mkdir(parents=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text("# Demo\n\nHello world.\n", encoding="utf-8")
        tool = SkillTool(workspace_root=tmp_path)
        result = run(tool.arun({"action": "read", "path": str(skill_file)}))
        assert result["skill_name"] == "demo"
        assert "# Demo" in result["content"]

    def test_frontmatter_name_overrides_dir_name(self, tmp_path: Path):
        skill_dir = tmp_path / "skills" / "SomeLongDirName"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: short-name\ndescription: test skill\n---\n# Title\n\nContent.\n",
            encoding="utf-8",
        )
        tool = SkillTool(workspace_root=tmp_path)
        result = run(tool.arun({"action": "list"}))
        assert result["skills"][0]["name"] == "short-name"
        # Also resolvable by frontmatter name
        read = run(tool.arun({"action": "read", "skill_name": "short-name"}))
        assert read["skill_name"] == "short-name"

    def test_frontmatter_inline_json_metadata(self, tmp_path: Path):
        """测试像 12306 那样 metadata 是 inline JSON 字符串的解析。"""
        skill_dir = tmp_path / "skills" / "inline_meta"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            '---\nname: inline_meta\ndescription: test\n'
            'metadata: {"openclaw":{"emoji":"🔥","requires":{"bins":["node"]}}}\n'
            '---\n# Inline Meta\n\nContent.\n',
            encoding="utf-8",
        )
        tool = SkillTool(workspace_root=tmp_path)
        result = run(tool.arun({"action": "inspect", "skill_name": "inline_meta"}))
        skill = result["skill"]
        assert "node" in skill["requires_bins"]
        listing = tool.build_skill_listing()
        assert "🔥" in listing

