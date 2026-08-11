"""LanguageSpec for Java."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="java",
    display_name="Java",
    extensions=frozenset({".java"}),
    grammar_package="tree_sitter_java",
    grammar_loader="language",
    scm_file="java.scm",
    heritage_node_types=frozenset(
        {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}
    ),
    import_support="full",
    entry_stems=("main", "app", "application"),
    # Maven/Gradle test layout conventions.
    test_dir_paths=("src/test/java", "src/test"),
    test_dir_tokens=("test", "tests"),
    test_camel_suffixes=("Test", "Tests", "IT"),
    manifest_files=("pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle"),
    blocked_dirs=("target", "build", ".gradle", "out"),
    blocked_extensions=(".class",),
    # Java stdlib calls filtered out of call trees as noise.
    builtin_calls=frozenset(
        {
            "println", "printf", "print",
            "toString", "hashCode", "equals", "getClass",
            "valueOf", "format",
            "stream", "collect", "toList", "map", "filter", "forEach",
            "size", "isEmpty", "contains", "indexOf",
            "add", "remove", "clear", "put", "get",
            "next", "hasNext", "iterator",
            "of", "from", "copyOf", "range", "between",
            "findFirst", "findAny", "orElse", "orElseThrow",
            "values", "keySet", "entrySet",
            "asList", "singletonList", "emptyList",
            "isPresent",
            "close", "flush", "shutdown", "stop",
        }
    ),
    color_hex="#ED8B00",
)
