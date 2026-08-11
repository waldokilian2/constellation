; =============================================================================
; Constellation — Java symbol / import / call queries
; tree-sitter 0.26 / tree-sitter-java
;
; Capture-name conventions (shared across ALL language query files):
;   @sym.def          — full definition node (line numbers, kind via LanguageConfig)
;   @sym.name         — the name identifier
;   @import.statement — full import node
;   @import.module    — the imported module/type path
;   @call.target      — the called method/function name
;   @call.receiver    — the object the call is made on
;   @call.arguments   — the argument list
;
; Annotation / modifier interpretation is NOT done here — it lives in the Java
; backend (engine/languages/java_ast.py), which reads annotations off a symbol's
; definition node on demand. This query discovers structure only.
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols — types
; ---------------------------------------------------------------------------

(class_declaration
  name: (identifier) @sym.name
) @sym.def

(interface_declaration
  name: (identifier) @sym.name
) @sym.def

(enum_declaration
  name: (identifier) @sym.name
) @sym.def

; Java 16+ records: record Point(double x, double y) {}
(record_declaration
  name: (identifier) @sym.name
) @sym.def

; ---------------------------------------------------------------------------
; Symbols — methods / constructors
; ---------------------------------------------------------------------------

(method_declaration
  name: (identifier) @sym.name
) @sym.def

(constructor_declaration
  name: (identifier) @sym.name
) @sym.def

; ---------------------------------------------------------------------------
; Imports
; ---------------------------------------------------------------------------

(import_declaration
  (scoped_identifier) @import.module
) @import.statement

; ---------------------------------------------------------------------------
; Calls
; ---------------------------------------------------------------------------

; Simple / static call: foo(args)
(method_invocation
  name: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; Method call on an object: obj.method(args)
(method_invocation
  object: (identifier) @call.receiver
  name: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; Chained call: a().b(args) — receiver is itself a call, left empty by the
; interpreter so the method name isn't mistaken for a receiver.
(method_invocation
  object: (method_invocation)
  name: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; Constructor: new ClassName(args)
(object_creation_expression
  type: (type_identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site
