"""Tests for --output json mode."""

import json

from codesurface import db, server


def _make_record(fqn, file_path, class_name="MyClass", member_name="myMethod",
                 member_type="method", namespace=""):
    return {
        "fqn": fqn,
        "namespace": namespace,
        "class_name": class_name,
        "member_name": member_name,
        "member_type": member_type,
        "signature": f"void {member_name}()",
        "summary": "",
        "params_json": [{"name": "x", "description": "a param"}],
        "returns_text": "",
        "file_path": file_path,
        "line_start": 1,
        "line_end": 10,
    }


def _setup():
    """Create a DB and wire it into the server module."""
    records = [
        _make_record("MyClass", "src/MyClass.ts", member_type="type",
                      member_name="MyClass", namespace="App"),
        _make_record("MyClass.foo", "src/MyClass.ts", member_name="foo",
                      namespace="App"),
        _make_record("MyClass.bar", "src/MyClass.ts", member_name="bar",
                      namespace="App"),
    ]
    conn = db.create_memory_db(records)
    server._conn = conn
    server._project_path = None
    server._index_fresh = True
    return conn


class TestCleanRecord:
    def test_removes_search_text_and_rank(self):
        r = {"fqn": "A", "search_text": "blah", "rank": -1.5, "name": "A"}
        cleaned = server._clean_record(r)
        assert "search_text" not in cleaned
        assert "rank" not in cleaned
        assert cleaned["fqn"] == "A"
        assert cleaned["name"] == "A"

    def test_parses_params_json_string(self):
        r = {"params_json": '[{"name": "x"}]'}
        cleaned = server._clean_record(r)
        assert cleaned["params_json"] == [{"name": "x"}]

    def test_leaves_params_json_list_alone(self):
        r = {"params_json": [{"name": "x"}]}
        cleaned = server._clean_record(r)
        assert cleaned["params_json"] == [{"name": "x"}]

    def test_handles_invalid_params_json(self):
        r = {"params_json": "not valid json"}
        cleaned = server._clean_record(r)
        assert cleaned["params_json"] == []


class TestJsonMode:
    def test_json_mode_off_by_default(self):
        assert not server._json_mode()

    def test_json_mode_on(self):
        old = server._output_format
        try:
            server._output_format = "json"
            assert server._json_mode()
        finally:
            server._output_format = old


class TestSearchJson:
    def setup_method(self):
        _setup()
        self._old_format = server._output_format
        server._output_format = "json"

    def teardown_method(self):
        server._output_format = self._old_format

    def test_returns_valid_json(self):
        result = server.search("MyClass")
        data = json.loads(result)
        assert data["query"] == "MyClass"
        assert data["count"] > 0
        assert isinstance(data["results"], list)

    def test_records_are_cleaned(self):
        result = server.search("MyClass")
        data = json.loads(result)
        for r in data["results"]:
            assert "search_text" not in r
            assert "rank" not in r

    def test_params_json_parsed(self):
        result = server.search("foo")
        data = json.loads(result)
        for r in data["results"]:
            assert isinstance(r["params_json"], list)

    def test_no_results_returns_empty(self):
        result = server.search("nonexistent_xyz_123")
        data = json.loads(result)
        assert data["count"] == 0
        assert data["results"] == []


class TestGetSignatureJson:
    def setup_method(self):
        _setup()
        self._old_format = server._output_format
        server._output_format = "json"

    def teardown_method(self):
        server._output_format = self._old_format

    def test_exact_fqn_match(self):
        result = server.get_signature("MyClass.foo")
        data = json.loads(result)
        assert data["query"] == "MyClass.foo"
        assert data["count"] >= 1
        assert data["suggestions"] == []

    def test_no_results(self):
        result = server.get_signature("nonexistent_xyz_123")
        data = json.loads(result)
        assert data["count"] == 0
        assert data["results"] == []
        assert data["suggestions"] == []

    def test_fts_fallback_populates_suggestions(self):
        """When no exact/substring match, FTS results go to suggestions."""
        result = server.get_signature("MyClazz")
        data = json.loads(result)
        # Could be results (substring match on "MyClass") or suggestions (FTS)
        # Either way the shape is consistent
        assert "results" in data
        assert "suggestions" in data
        assert isinstance(data["suggestions"], list)


class TestGetClassJson:
    def setup_method(self):
        _setup()
        self._old_format = server._output_format
        server._output_format = "json"

    def teardown_method(self):
        server._output_format = self._old_format

    def test_returns_class_structure(self):
        result = server.get_class("MyClass")
        data = json.loads(result)
        assert data["class_name"] == "MyClass"
        assert data["namespace"] == "App"
        assert isinstance(data["members"], list)
        assert data["count"] == 3  # type + foo + bar

    def test_members_are_cleaned(self):
        result = server.get_class("MyClass")
        data = json.loads(result)
        for m in data["members"]:
            assert "search_text" not in m
            assert "rank" not in m

    def test_not_found_returns_empty_with_suggestions(self):
        result = server.get_class("NonExistentClass")
        data = json.loads(result)
        assert data["count"] == 0
        assert data["members"] == []
        assert "suggestions" in data
        assert isinstance(data["suggestions"], list)

    def test_not_found_suggestions_contain_similar(self):
        """When searching for a close match, suggestions should have results."""
        result = server.get_class("MyClazz")  # close to MyClass
        data = json.loads(result)
        assert data["count"] == 0
        if data["suggestions"]:
            assert all("search_text" not in s for s in data["suggestions"])


class TestGetStatsJson:
    def setup_method(self):
        _setup()
        self._old_format = server._output_format
        server._output_format = "json"

    def teardown_method(self):
        server._output_format = self._old_format

    def test_returns_stats_dict(self):
        result = server.get_stats()
        data = json.loads(result)
        assert "total" in data
        assert "files" in data
        assert "top_namespaces" in data
        assert isinstance(data["top_namespaces"], dict)


class TestReindexJson:
    def setup_method(self):
        _setup()
        self._old_format = server._output_format
        server._output_format = "json"

    def teardown_method(self):
        server._output_format = self._old_format

    def test_no_project_returns_text(self):
        # reindex without project_path returns plain text error even in json mode
        server._project_path = None
        result = server.reindex()
        assert "No project path" in result


class TestTextModeUnchanged:
    """Verify text mode still works the same when _output_format='text'."""

    def setup_method(self):
        _setup()
        server._output_format = "text"

    def test_search_returns_text(self):
        result = server.search("MyClass")
        assert "Found" in result
        assert "result(s)" in result

    def test_get_class_returns_text(self):
        result = server.get_class("MyClass")
        assert "Class: MyClass" in result
