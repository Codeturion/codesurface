"""Tests for the include_tests parameter on search and get_class_members."""

from codesurface import db


def _make_record(fqn, file_path, class_name="MyClass", member_name="myMethod",
                 member_type="method"):
    return {
        "fqn": fqn,
        "namespace": "",
        "class_name": class_name,
        "member_name": member_name,
        "member_type": member_type,
        "signature": f"void {member_name}()",
        "summary": "",
        "params_json": [],
        "returns_text": "",
        "file_path": file_path,
        "line_start": 1,
        "line_end": 10,
    }


def _setup_db():
    """Create a DB with both source and test records."""
    records = [
        # Source files
        _make_record("MyClass", "src/MyClass.ts", member_type="type",
                      member_name="MyClass"),
        _make_record("MyClass.foo", "src/MyClass.ts", member_name="foo"),
        _make_record("MyClass.bar", "src/MyClass.ts", member_name="bar"),
        # .test. pattern
        _make_record("MyClass.testHelper", "src/MyClass.test.ts",
                      member_name="testHelper"),
        # .spec. pattern
        _make_record("MyClass.specHelper", "src/MyClass.spec.ts",
                      member_name="specHelper"),
        # __tests__/ directory pattern
        _make_record("MyClass.dirTest", "src/__tests__/MyClass.ts",
                      member_name="dirTest"),
        # test_ filename pattern (Python convention)
        _make_record("TestUtils", "src/test_utils.py", class_name="TestUtils",
                      member_type="type", member_name="TestUtils"),
        _make_record("TestUtils.setup", "src/test_utils.py",
                      class_name="TestUtils", member_name="setup"),
        # _test. filename pattern (Go convention)
        _make_record("MyClass.goTest", "src/myclass_test.go",
                      member_name="goTest"),
        # /tests/ directory pattern
        _make_record("Fixtures", "tests/fixtures.ts", class_name="Fixtures",
                      member_type="type", member_name="Fixtures"),
    ]
    return db.create_memory_db(records)


class TestSearchIncludeTests:
    def test_excludes_test_files_by_default(self):
        conn = _setup_db()
        results = db.search(conn, "MyClass", include_tests=False)
        paths = [r["file_path"] for r in results]
        assert all(
            ".test." not in p
            and ".spec." not in p
            and "__tests__" not in p
            and "_test." not in p
            for p in paths
        )

    def test_includes_test_files_when_requested(self):
        conn = _setup_db()
        results = db.search(conn, "MyClass", include_tests=True)
        paths = [r["file_path"] for r in results]
        assert any(".test." in p or "__tests__" in p or "_test." in p for p in paths)

    def test_excludes_tests_dir(self):
        conn = _setup_db()
        results = db.search(conn, "Fixtures", include_tests=False)
        paths = [r["file_path"] for r in results]
        assert all("tests/" not in p for p in paths)

    def test_includes_tests_dir_when_requested(self):
        conn = _setup_db()
        results = db.search(conn, "Fixtures", include_tests=True)
        paths = [r["file_path"] for r in results]
        assert any("tests/" in p for p in paths)

    def test_excludes_test_underscore_prefix(self):
        conn = _setup_db()
        results = db.search(conn, "TestUtils", include_tests=False)
        paths = [r["file_path"] for r in results]
        assert all("/test_" not in p for p in paths)

    def test_excludes_go_test_suffix(self):
        conn = _setup_db()
        results = db.search(conn, "goTest", include_tests=False)
        assert len(results) == 0

    def test_does_not_exclude_non_test_dirs_containing_test(self):
        """A dir like _test_fixture/ should NOT be excluded."""
        conn = _setup_db()
        records = [
            _make_record("Calc", "_test_fixture/calculator.py",
                          class_name="Calc", member_type="type",
                          member_name="Calc"),
        ]
        db.insert_records(conn, records)
        results = db.search(conn, "Calc", include_tests=False)
        assert len(results) == 1
        assert results[0]["file_path"] == "_test_fixture/calculator.py"


class TestGetClassMembersIncludeTests:
    def test_excludes_test_files_by_default(self):
        conn = _setup_db()
        members = db.get_class_members(conn, "MyClass", include_tests=False)
        paths = [m["file_path"] for m in members]
        assert all(
            ".test." not in p
            and ".spec." not in p
            and "__tests__" not in p
            for p in paths
        )
        assert len(members) == 3  # type + foo + bar

    def test_includes_test_files_when_requested(self):
        conn = _setup_db()
        members = db.get_class_members(conn, "MyClass", include_tests=True)
        assert len(members) == 7  # type + foo + bar + testHelper + specHelper + dirTest + goTest

    def test_default_is_exclude(self):
        conn = _setup_db()
        members_default = db.get_class_members(conn, "MyClass")
        members_explicit = db.get_class_members(conn, "MyClass", include_tests=False)
        assert len(members_default) == len(members_explicit)
