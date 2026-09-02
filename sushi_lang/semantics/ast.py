from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Union, Literal, TYPE_CHECKING
from sushi_lang.internals.report import Origin, Span
from sushi_lang.semantics.typesys import Type

from lark import Token

if TYPE_CHECKING:
    from sushi_lang.semantics.generics.extension_targets import ExtensionTarget
    from sushi_lang.semantics.namespaces import NamespaceRef


@dataclass(slots=True)
class Node:
    loc: Optional[Span]
    # The `borrow` pass records here what it proved about this node's ownership, and
    # `backend/ownership.py` acts on it. A DECLARED field rather than an attribute
    # written on in passing: see IR.md section 5. kw_only, because `loc` carries no
    # default and every subclass adds positional fields after it.
    ownership_provenance: Optional["Provenance"] = field(default=None, kw_only=True)
    # `f(nom x)` is a call-site MARKER, not an operator: the builder flags the argument
    # rather than wrapping it in a node every pass would dispatch on. It can land on any
    # expression, which is why it is declared here and not per class.
    nom_marked: bool = field(default=False, kw_only=True)
    nom_span: Optional[Span] = field(default=None, kw_only=True)

@dataclass(slots=True)
class Stmt(Node):
    pass


@dataclass(slots=True)
class DocTag:
    """One recognised item of a doc block's Markdown list (documentation.md S3)."""
    kind: str                    # "parameter" | "returns" | "errors" | "example" | "unknown"
    name: Optional[str]          # the parameter name, for kind == "parameter"
    text: str
    loc: Optional[Span] = None
    word: str = ""               # the keyword AS WRITTEN; what CE7004 reports


@dataclass(slots=True)
class DocExample:
    """One fenced code block under an `- Example:` tag (documentation.md S10, R14).

    Kept verbatim, because an example is code: the fold that joins a tag's
    continuation lines strips every line, which destroys the indentation a program
    needs. `defect` is set when the tag introduces nothing a runner could compile,
    and the `docs` pass turns it into CE7007 or CE7008.
    """
    code: str                    # the fence body, dedented by the fence's own indent
    attrs: str = ""              # the fence info string, as written
    loc: Optional[Span] = None   # the opening fence, or the tag when there is none
    defect: Optional[Literal["no-fence", "unterminated"]] = None


@dataclass(slots=True)
class DocBlock:
    """A `##: ... :##` block, dedented, parked on the node it documents."""
    summary: str                 # the first paragraph
    text: str                    # the whole block, dedented, tags included
    tags: List[DocTag]
    # The prose between the summary and the FIRST tag, which is what a `.slib` record
    # carries. Parsed and never derived: "the block with the tag lines taken out" reads
    # the tail of a fenced example as prose (documentation.md section 8, R1).
    body: str = ""
    loc: Optional[Span] = None
    # The fenced examples, in source order. Their own structure rather than the text of
    # an `- Example:` tag, which is stripped and folded (documentation.md S10, R14).
    examples: List[DocExample] = field(default_factory=list)
    # Why this block reached `Program.orphan_docs`, and None while it is attached.
    # "detached" documents nothing (CW7001); "in-body" stands in a body it is not
    # the first item of (CE7005). The two are separate rules, not one.
    orphan_reason: Optional[Literal["detached", "in-body"]] = None


@dataclass(slots=True)
class UseStatement(Node):
    path: str                        # Path string like "math/integer" or "core/results"
    is_stdlib: bool = False          # True for <module>, False for "module"
    is_library: bool = False         # True for <lib/module>, False otherwise
    # The `as NAME` clause. It decides WHERE the imported names land, and nothing
    # else (`docs/design/unit-namespaces.md` section 2).
    alias: Optional[str] = None
    alias_span: Optional[Span] = None

@dataclass(slots=True)
class Program(Node):
    uses: List["UseStatement"]
    constants: List["ConstDef"]
    structs: List["StructDef"]
    enums: List["EnumDef"]
    perks: List["PerkDef"]
    functions: List["FuncDef"]
    extensions: List["ExtendDef"]           # Non-generic extensions only
    generic_extensions: List["ExtendDef"]   # Generic extensions only (e.g., extend Box<T>)
    perk_impls: List["ExtendWithDef"]          # Non-generic perk implementations only
    # Perk implementations on a GENERIC target (`extend Box@(T) with Show`). Templates,
    # moved here by the collector for the same reason `generic_extensions` exists: every
    # later walk over `perk_impls` assumes a concrete `self`.
    generic_perk_impls: List["ExtendWithDef"] = None
    externals: List["ExternalBlock"] = None
    doc: Optional["DocBlock"] = None            # the unit block: first item, attached to nothing
    orphan_docs: List["DocBlock"] = None        # every block that documents nothing
    # Where this unit's first WRITTEN declaration stands. Read by CE3014, and recorded
    # here because the tree is the only place source order survives: a library's
    # constants and private types are appended to a host unit's lists later, carrying
    # spans from their own file.
    first_declaration_span: Optional[Span] = None

    def __post_init__(self):
        if self.externals is None:
            self.externals = []
        if self.orphan_docs is None:
            self.orphan_docs = []
        if self.generic_perk_impls is None:
            self.generic_perk_impls = []

@dataclass(slots=True)
class Param:
    name: str
    ty: Optional[Type]
    name_span: Optional[Span] = None
    type_span: Optional[Span] = None
    loc: Optional[Span] = None
    is_variadic: bool = False         # True for a trailing ...T native variadic param;
    is_pack: bool = False             # True for a v2 type-pack value parameter (...Ts args);
    self_mode: Optional[str] = None   # "peek"/"poke" for a `poke self` receiver parameter
                                      # (#327); ty is None. Stripped-and-lifted onto the
                                      # declaration by the builders, never reaches collect.
    is_nom: bool = False              # `nom T name`: the CALLEE takes ownership. The only
                                      # mode bit the type cannot carry -- peek/poke ride on
                                      # ReferenceType. See docs/design/borrow-model.md S6.
    nom_span: Optional[Span] = None   # the `nom` marker itself, for diagnostics
    # A lambda's captures are Params, and the `borrow` pass stamps each one.
    ownership_provenance: Optional["Provenance"] = None


@dataclass(slots=True)
class BoundedTypeParam:
    """Type parameter with optional perk constraints (e.g., T: Hashable)."""
    name: str
    constraints: List[str] = None  # Perk names (e.g., ["Hashable", "Eq"])
    loc: Optional[Span] = None
    is_pack: bool = False          # True for a variadic type pack (...Ts)
    # The alias each constraint was written behind, index-aligned with `constraints`
    # and None where it was written bare. The perk name stays the table key: a
    # qualifier picks WHICH declaration is meant and never makes a second perk.
    constraint_namespaces: List[Optional[str]] = None

    def __post_init__(self):
        if self.constraints is None:
            self.constraints = []
        if self.constraint_namespaces is None:
            self.constraint_namespaces = [None] * len(self.constraints)

    def written_constraints(self) -> List[str]:
        """Each constraint as the user wrote it, for a diagnostic to quote."""
        return [name if ns is None else f"{ns}.{name}"
                for name, ns in zip(self.constraints, self.constraint_namespaces,
                                    strict=False)]

    def __str__(self) -> str:
        prefix = "..." if self.is_pack else ""
        if self.constraints:
            constraints_str = " + ".join(self.written_constraints())
            return f"{prefix}{self.name}: {constraints_str}"
        return f"{prefix}{self.name}"

@dataclass(slots=True)
class FuncDef(Node):
    name: str
    params: List[Param]
    ret: Optional[Type]
    body: "Block"
    is_public: bool = False
    type_params: Optional[List[BoundedTypeParam]] = None
    err_type: Optional[Type] = None  # Error type for Result<T, E> (None = StdError default)
    name_span: Optional[Span] = None
    ret_span: Optional[Span] = None
    is_library_template: bool = False  # True if reconstructed from a consumed library's .slib templates
    # Set with `is_library_template` and never without it: the mark answers who may
    # be called from this body (#468), the origin answers how a diagnostic raised in
    # it is rendered (#471).
    library_origin: Optional[Origin] = None
    self_mode: Optional[str] = None  # "peek"/"poke" for a perk-IMPL method declared
                                     # `(poke self, ...)` (#327). Always None on a plain
                                     # top-level function (collect rejects it there).
    self_mode_span: Optional[Span] = None
    doc: Optional[DocBlock] = None
    public_span: Optional[Span] = None
    # True for a body the compiler generated (a derived clone/hash, a monomorphized
    # instance); the library manifest and the doc lints both skip one.
    is_synthesized: bool = False
    # The unit that DECLARED the generic this instance came from, so two units'
    # instances of one generic stay distinct (#495). Set by generics/synthesis.py.
    home_unit: Optional[str] = None


@dataclass(slots=True)
class ConstDef(Node):
    name: str
    ty: Optional[Type]           # Constant type (must be specified)
    value: "Expr"
    is_public: bool = True
    name_span: Optional[Span] = None
    type_span: Optional[Span] = None
    doc: Optional[DocBlock] = None
    public_span: Optional[Span] = None

@dataclass(slots=True)
class StructField:
    """Single field in a struct definition."""
    ty: Optional[Type]
    name: str
    loc: Optional[Span] = None
    doc: Optional[DocBlock] = None

@dataclass(slots=True)
class StructDef(Node):
    """Struct definition with fields."""
    name: str
    fields: List[StructField]
    type_params: Optional[List[BoundedTypeParam]] = None
    name_span: Optional[Span] = None
    doc: Optional[DocBlock] = None
    is_public: bool = True
    public_span: Optional[Span] = None

@dataclass(slots=True)
class EnumVariant:
    """Single variant in an enum definition."""
    name: str                           # Variant name (e.g., "Some", "None")
    associated_types: List[Type]        # Associated data types (empty for unit variants)
    name_span: Optional[Span] = None
    loc: Optional[Span] = None
    doc: Optional[DocBlock] = None

@dataclass(slots=True)
class EnumDef(Node):
    """Enum definition with variants."""
    name: str                           # Enum name (e.g., "Option", "Result")
    variants: List[EnumVariant]
    type_params: Optional[List[BoundedTypeParam]] = None
    name_span: Optional[Span] = None
    doc: Optional[DocBlock] = None
    is_public: bool = True
    public_span: Optional[Span] = None

@dataclass(slots=True)
class ExtendDef(Node):
    target_type: Optional[Type]  # Type being extended (int, bool, string)
    name: str
    params: List[Param]          # Parameters excluding implicit 'self'
    ret: Optional[Type]
    body: "Block"
    target_type_span: Optional[Span] = None
    name_span: Optional[Span] = None
    ret_span: Optional[Span] = None
    self_mode: Optional[str] = None  # "peek"/"poke" when declared `(poke self, ...)` (#327);
    self_mode_span: Optional[Span] = None
    # What a `@(...)` target's arguments mean: a constraint, parameter names, or a mix.
    # Stamped by the collect pass, which knows which names are declared types (#393).
    target_shape: Optional["ExtensionTarget"] = None
    type_params: Optional[List[BoundedTypeParam]] = None  # method-level `@(U)`
    err_type: Optional[Type] = None  # `| E` opts into the Result channel
    err_span: Optional[Span] = None
    # The SOLVED method-level type arguments of a monomorphized copy, in declaration
    # order; part of the emitted symbol's identity. () or None on every other node.
    method_type_args: Optional[tuple] = None
    doc: Optional[DocBlock] = None

@dataclass(slots=True)
class PerkMethodSignature:
    """Method signature required by a perk."""
    name: str
    params: List[Param]
    ret: Optional[Type]
    loc: Optional[Span] = None
    name_span: Optional[Span] = None
    ret_span: Optional[Span] = None
    self_mode: Optional[str] = None  # "peek"/"poke" when the perk declares `(poke self, ...)` (#327)
    self_mode_span: Optional[Span] = None
    err_type: Optional[Type] = None  # `| E` opts the CONTRACT into the Result channel
    err_span: Optional[Span] = None
    doc: Optional[DocBlock] = None

@dataclass(slots=True)
class PerkDef(Node):
    """Perk definition (trait/interface)."""
    name: str
    methods: List[PerkMethodSignature]
    type_params: Optional[List[BoundedTypeParam]] = None
    name_span: Optional[Span] = None
    doc: Optional[DocBlock] = None
    is_public: bool = True
    public_span: Optional[Span] = None

@dataclass(slots=True)
class ExtendWithDef(Node):
    """Perk implementation (extend Type with Perk)."""
    target_type: Optional[Type]
    perk_name: str
    methods: List[FuncDef]
    target_type_span: Optional[Span] = None
    perk_name_span: Optional[Span] = None
    doc: Optional[DocBlock] = None

@dataclass(slots=True)
class TypeConstraint:
    """Perk constraint on a type parameter (T: Hashable)."""
    perk_name: str
    loc: Optional[Span] = None

@dataclass(slots=True)
class ExternalDecl(Node):
    """A single foreign function declaration inside an unsafe external block."""
    name: str                    # Sushi-visible name (e.g., "strlen")
    params: List[Param]          # Parameters (C-ABI representable types)
    ret: Optional[Type]          # Raw C return type (NOT wrapped in Result)
    link_name: str               # C link symbol (e.g., "strlen")
    is_variadic: bool = False     # Trailing `...` for untyped C varargs (e.g. printf)
    name_span: Optional[Span] = None
    ret_span: Optional[Span] = None
    doc: Optional[DocBlock] = None

@dataclass(slots=True)
class ExternalBlock(Node):
    """An unsafe external block declaring foreign functions under a namespace."""
    abi: str                          # ABI string (only "C" accepted in v1)
    namespace: str                    # Namespace binding (e.g., "libc")
    reason: Optional[str]             # because "..." reason (None silences nothing)
    decls: List[ExternalDecl]
    abi_span: Optional[Span] = None
    namespace_span: Optional[Span] = None
    doc: Optional[DocBlock] = None

@dataclass(slots=True)
class Block(Node):
    statements: List[Stmt]
    doc: Optional[DocBlock] = None
    # Set on a CALLABLE's body block: the locals whose every move does not dominate the
    # scope exit, so each needs a run-time drop flag (#414).
    conditional_move_names: Optional[frozenset] = None


@dataclass(slots=True)
class Let(Stmt):
    name: str
    ty: Optional[Type]
    value: "Expr"
    name_span: Optional[Span] = None
    type_span: Optional[Span] = None

@dataclass(slots=True)
class Rebind(Stmt):
    target: "Expr"  # Can be Name or MemberAccess (for field rebinding)
    value: "Expr"

@dataclass(slots=True)
class ExprStmt(Stmt):
    expr: "Expr"

@dataclass(slots=True)
class Return(Stmt):
    value: "Expr"

@dataclass(slots=True)
class Print(Stmt):
    value: "Expr"

@dataclass(slots=True)
class PrintLn(Stmt):
    value: "Expr"

@dataclass(slots=True)
class If(Stmt):
    arms: List[Tuple["Expr", Block]]     # [(cond, block), ...]
    else_block: Optional[Block]

@dataclass(slots=True)
class While(Stmt):
    cond: "Expr"
    body: Block

@dataclass(slots=True)
class Foreach(Stmt):
    """Foreach loop statement: foreach(type item in iterable):"""
    item_name: str
    item_type: Optional[Type]   # Declared type (may be None for inference)
    iterable: "Expr"
    body: Block
    item_name_span: Optional[Span] = None
    item_type_span: Optional[Span] = None
    item_borrow: Optional[str] = None       # None | "peek" | "poke"
    item_borrow_span: Optional[Span] = None

@dataclass(slots=True)
class Expand(Stmt):
    """Compile-time pack-expansion statement: expand(a in args):"""
    var: str
    iterable: "Expr"            # Value-pack reference being expanded (typically a Name)
    body: Block                 # Body unrolled per pack element
    var_span: Optional[Span] = None

@dataclass(slots=True)
class Break(Stmt):
    pass

@dataclass(slots=True)
class Continue(Stmt):
    pass

@dataclass(slots=True)
class Pattern(Node):
    """Pattern for match arms: EnumName.VariantName(binding1, binding2, ...)"""
    enum_name: str
    variant_name: str
    bindings: List[Union[str, 'Pattern', 'OwnPattern', 'RefBinding', 'NomBinding']]
    enum_name_span: Optional[Span] = None
    variant_name_span: Optional[Span] = None
    # The alias the enum was written behind: `geo.Sign.Plus ->`. The enum name stays
    # the key, so exhaustiveness, payload binding and the literal-arm rules read
    # exactly what they read for a bare arm (unit-namespaces.md section 5.2).
    namespace: Optional[str] = None

@dataclass(slots=True)
class WildcardPattern(Node):
    """Wildcard pattern (_) for match arms - catches all remaining variants"""
    pass

@dataclass(slots=True)
class LiteralPattern(Node):
    """An integer literal pattern in a match arm on an integer scrutinee (#415).

    `display` keeps the source spelling for diagnostics; `value` is the Python
    integer (sign already applied). `radix` feeds the same fit rule as a
    context-typed literal: a non-decimal literal is a bit pattern."""
    value: int
    display: str
    radix: int = 10

@dataclass(slots=True)
class RefBinding(Node):
    """A reference binding in a match pattern: `Shape.Poly(poke p)` (#300 phase 3)."""
    name: str
    mode: str                        # "peek" | "poke"


@dataclass(slots=True)
class NomBinding(Node):
    """A TAKING binding in a match pattern: `Result.Ok(nom f)` (HANDLES.md ruling R11).

    Its own node rather than a third `RefBinding.mode`, because `nom` is not a
    reference: a `peek`/`poke` binding is a pointer into the scrutinee's payload and
    carries a `ReferenceType`, while this one is a VALUE the arm now owns. Parameters
    already split the same way -- `nom` is a flag on the parameter, `peek`/`poke` are
    a type.
    """
    name: str


@dataclass(slots=True)
class OwnPattern(Node):
    """Own(inner_pattern) - auto-unwrap Own<T> in pattern matching."""
    inner_pattern: Union[str, 'Pattern']
    inner_borrow: Optional[str] = None    # None | "peek" | "poke"
    inner_borrow_span: Optional[Span] = None

@dataclass(slots=True)
class MatchArm(Node):
    """Single arm in a match statement/expression"""
    pattern: Union[Pattern, LiteralPattern, WildcardPattern]
    body: Union["Expr", "Block"]

@dataclass(slots=True)
class Match(Stmt):
    """Match statement: match expr: pattern -> body"""
    scrutinee: "Expr"
    arms: List[MatchArm]
    # Concrete monomorphized enum type of the scrutinee, resolved by the type
    # checker (the typecheck pass) and consumed by the backend. Stored here because the
    # backend cannot always re-derive it from the scrutinee expression alone
    # (e.g. an indexed element, a fn-field call, or a user method returning
    # Maybe/Result), and a miss would otherwise silently drop pattern bindings.
    resolved_scrutinee_type: Optional[Type] = None
    # An INTEGER scrutinee instead (#415). The backend dispatches on this; an EnumType
    # scrutinee uses `resolved_scrutinee_type` above.
    integer_match_type: Optional[Type] = None
    # `match nom r:` (ruling R11). The match CONSUMES a scrutinee that names storage,
    # which is what makes a `nom` payload binding legal on it. A temporary scrutinee is
    # owned by construction and needs no marker.
    consumes_scrutinee: bool = False
    consumes_span: Optional[Span] = None


@dataclass(slots=True)
class Name(Node):
    id: str
    # A bare name in a fn-typed position gets the expected FunctionType, so a reference
    # to a generic function can solve its type arguments. `Lambda` carries the same
    # field for the same reason.
    expected_type: Optional[Type] = None

@dataclass(slots=True)
class IntLit(Node):
    value: int
    radix: int = 10  # 2 (binary), 8 (octal), 10 (decimal), 16 (hexadecimal)
    resolved_type: Optional[Type] = None
    # Set once the literal's range has been checked against its context type, so a
    # second look does not report CE2049 twice.
    range_checked: bool = False
    # `x as i64` gives the literal the cast's target as its context, so its range is
    # judged against that and not against the default i32.
    in_cast_context: bool = False

@dataclass(slots=True)
class FloatLit(Node):
    value: float
    resolved_type: Optional[Type] = None
    range_checked: bool = False

@dataclass(slots=True)
class BoolLit(Node):
    value: bool

@dataclass(slots=True)
class BlankLit(Node):
    """Blank literal (~) - represents the single value of blank type"""
    pass

@dataclass(slots=True)
class StringLit(Node):
    value: str

@dataclass(slots=True)
class InterpolatedString(Node):
    """Represents a string with interpolated expressions like "Hello, {name}!" """
    parts: List[Union[str, "Expr"]]  # Alternating string literals and expressions

@dataclass(slots=True)
class ArrayElement(Node):
    value: "Expr"
    count: Optional["Expr"] = None   # `value; count`. None is a plain element.

@dataclass(slots=True)
class ArrayLiteral(Node):
    elements: List["ArrayElement"]

@dataclass(slots=True)
class IndexAccess(Node):
    array: "Expr"
    index: "Expr"
    inferred_element_type: Optional["Type"] = None  # Element type inferred by the typecheck pass.
                                            # The backend reads the typecheck pass's stamp rather than
                                            # re-deriving; with none, `rows[0].hash()` died
                                            # as CE0019 (#286). Siblings:
                                            # `inferred_return_type`,
                                            # `inferred_unwrapped_type`.

UnOp = Literal["neg", "not", "~"]
@dataclass(slots=True)
class UnaryOp(Node):
    op: UnOp
    expr: "Expr"

BinOp = Literal["+", "-", "*", "/", "%", "==", "!=", "<", "<=", ">", ">=", "and", "or", "xor", "&", "|", "^", "<<", ">>"]
@dataclass(slots=True)
class BinaryOp(Node):
    op: BinOp
    left: "Expr"
    right: "Expr"

@dataclass(slots=True)
class Spread(Node):
    """A bloomed call argument: `arr...` fans an existing array's elements into a variadic `...T`
    slot. Only valid as the sole, last trailing argument of a call to a variadic function; the
    source array is moved (consumed) into the callee.
    """
    value: "Expr"   # the array expression being bloomed (e.g. Name("args"))

@dataclass(slots=True)
class Lambda(Node):
    """A lambda literal (closure)."""
    params: List[Param]
    body: Union["Expr", "Block"]
    is_block_body: bool = False
    ret: Optional[Type] = None
    err_type: Optional[Type] = None
    captures: Optional[List["Param"]] = None
    lifted_name: Optional[str] = None
    # Filled by the type pass: the lambda's resolved FunctionType (params + captures
    # typed, ok/err resolved). `expected_type` is a FunctionType propagated from the
    # binding/argument context, used to infer bare-param types (`|x|`).
    resolved_type: Optional[Type] = None
    expected_type: Optional[Type] = None
    env_struct: Optional[Type] = None


@dataclass(slots=True)
class Call(Node):
    # Usually a Name (a direct function call), but widened to any Expr so a
    # function VALUE can be called through: env.f(x) (a captured closure, from
    # lambda-lifting), obj.handler() (a fn-typed field), arr[0](), (e)().
    callee: "Expr"
    args: List["Expr"]
    field_names: Optional[List[str]] = None  # For named struct construction
    # Explicit call-site type arguments: `identity@(i32)(5)`. None when the call
    # relies on inference. Present only on the direct-call (free-function) path;
    # the parser never attaches these to method/indirect calls.
    type_args: Optional[List["Type"]] = None
    # Span of the `@(...)` type-arg list, for diagnostics (CE2062 arity, constraint
    # failures) that must underline the type args rather than the callee.
    type_args_loc: Optional["Span"] = None
    # Set by the type checker when `callee` is a non-Name expression resolving to a
    # FunctionType: the backend uses it to emit the fat-pointer indirect call without
    # re-inferring the callee's signature.
    callee_fn_type: Optional[Type] = None
    # Set by the type checker when it found no callee at all (CE2008, CE2092). The passes
    # after it then know there is no signature, and say nothing about the arguments.
    callee_unresolved: bool = False
    # What the typecheck pass resolved about the callee's parameters. The borrow pass
    # reads the modes off THIS node, so losing them makes every `nom` parameter inert.
    callee_param_modes: Optional[List] = None
    callee_param_names: Optional[List[str]] = None
    callee_param_types: Optional[List] = None
    # An extern variadic call's promoted argument types (CE5005 checks them).
    variadic_arg_types: Optional[List] = None
    # The rest of what the typecheck pass resolves about a call. Every call node
    # carries the whole set, so a pass never has to ask which call shape it has.
    callee_self_mode: Optional[str] = None  # "peek"/"poke"; see MethodCall
    external_ref: Optional[Tuple[str, str]] = None  # (namespace, name) for an FFI call
    inferred_return_type: Optional["Type"] = None  # Return type inferred by the typecheck pass
    namespace_ref: Optional["NamespaceRef"] = None  # a call through a `use ... as` alias
    resolved_enum_type: Optional["Type"] = None  # Resolved concrete enum type
    resolved_struct_type: Optional["Type"] = None  # Resolved concrete struct type


@dataclass(slots=True)
class MethodCall(Node):
    receiver: "Expr"    # The object/expression being called (x in x.add(5))
    method: str
    args: List["Expr"]
    inferred_return_type: Optional["Type"] = None  # Return type inferred by type checker
    resolved_struct_type: Optional["Type"] = None  # Resolved concrete struct type (populated by type checker)
    resolved_enum_type: Optional["Type"] = None  # Resolved concrete enum type (populated by type checker)
    # A call through a `use ... as` alias; see DotCall for what the triple carries.
    namespace_ref: Optional["NamespaceRef"] = None
    callee_self_mode: Optional[str] = None  # "peek"/"poke" when the resolved method takes
                                            # `poke self` (#327); stamped by the typecheck pass, read
                                            # by the borrow pass (a poke call is a receiver WRITE)
                                            # and the backend (pass a pointer)
    # What the typecheck pass resolved about the callee's parameters. The borrow pass
    # reads the modes off THIS node, so losing them makes every `nom` parameter inert.
    callee_param_modes: Optional[List] = None
    callee_param_names: Optional[List[str]] = None
    callee_param_types: Optional[List] = None
    # The rest of what the typecheck pass resolves about a call. Every call node
    # carries the whole set, so a pass never has to ask which call shape it has.
    callee_fn_type: Optional[Type] = None  # set when the callee resolves to a FunctionType
    callee_unresolved: bool = False  # set when no callee was found at all (CE2008, CE2092)
    external_ref: Optional[Tuple[str, str]] = None  # (namespace, name) for an FFI call
    variadic_arg_types: Optional[List] = None  # an extern variadic call's promoted arg types
    # The SOLVED method-level type arguments of a method-generic extension call
    # (`name@(U)`); the backend composes them into the callee symbol.
    callee_method_type_args: Optional[Tuple] = None


@dataclass(slots=True)
class DotCall(Node):
    """Unified node for X.Y(args) - resolved during semantic analysis."""
    receiver: "Expr"    # The receiver expression (variable, type name, etc.)
    method: str
    args: List["Expr"]
    inferred_return_type: Optional["Type"] = None  # Return type inferred by type checker
    resolved_enum_type: Optional["Type"] = None  # Resolved concrete enum type (populated by type checker)
    resolved_struct_type: Optional["Type"] = None  # Resolved concrete struct type (populated by type checker)
    external_ref: Optional[Tuple[str, str]] = None  # (namespace, name) for FFI calls (set by type checker)
    # `(namespace kind, origin, name)` for a call through a `use ... as` alias: which
    # kind of producer answered, the unit or module it named, and the declared name.
    # The back end resolves through the origin, so a shadowed name reaches the right
    # declaration (`docs/design/unit-namespaces.md` section 5).
    namespace_ref: Optional["NamespaceRef"] = None
    callee_self_mode: Optional[str] = None  # "peek"/"poke" when the resolved method takes
                                            # `poke self` (#327); see MethodCall
    # What the typecheck pass resolved about the callee's parameters. The borrow pass
    # reads the modes off THIS node, so losing them makes every `nom` parameter inert.
    callee_param_modes: Optional[List] = None
    callee_param_names: Optional[List[str]] = None
    callee_param_types: Optional[List] = None
    # An extern variadic call's promoted argument types (CE5005 checks them).
    variadic_arg_types: Optional[List] = None
    field_names: Optional[List[str]] = None  # named construction through a namespace
    # Explicit call-site type arguments, as `Call` carries them. A qualified call to a
    # return-type-only generic is the reason they are here: `it.empty_list@(i32)()` is a
    # direct call to a named free function once the alias folds away, so the rule CE6102
    # states is unharmed and only the parse shape changed (unit-namespaces.md 5.1).
    type_args: Optional[List["Type"]] = None
    type_args_loc: Optional["Span"] = None
    # The rest of what the typecheck pass resolves about a call. Every call node
    # carries the whole set, so a pass never has to ask which call shape it has.
    callee_fn_type: Optional[Type] = None  # set when the callee resolves to a FunctionType
    callee_unresolved: bool = False  # set when no callee was found at all (CE2008, CE2092)
    # The SOLVED method-level type arguments of a method-generic extension call; see
    # MethodCall.
    callee_method_type_args: Optional[Tuple] = None


@dataclass(slots=True)
class MemberAccess(Node):
    """Member access expression: obj.field"""
    receiver: "Expr"    # The struct expression (p in p.x)
    member: str
    namespace_ref: Optional["NamespaceRef"] = None  # a name read through an alias

@dataclass(slots=True)
class EnumConstructor(Node):
    """Enum variant constructor: Option.Some(42) or Color.Red"""
    enum_name: str
    variant_name: str
    args: List["Expr"]  # Arguments for associated data (empty for unit variants)
    enum_name_span: Optional[Span] = None
    variant_name_span: Optional[Span] = None
    resolved_enum_type: Optional["Type"] = None  # Resolved concrete enum type (populated by type checker)

@dataclass(slots=True)
class DynamicArrayNew(Node):
    resolved_type: Optional["Type"] = None  # The `T[]` this empty array is (typecheck pass)

@dataclass(slots=True)
class DynamicArrayFrom(Node):
    elements: ArrayLiteral  # from([1, 2, 3]) -> holds the array literal

@dataclass(slots=True)
class CastExpr(Node):
    expr: "Expr"
    target_type: Type
    source_type: Optional[Type] = None  # Operand's semantic type, stamped by the typecheck pass (signedness for codegen)

@dataclass(slots=True)
class Borrow(Node):
    """Borrow expression: peek expr or poke expr"""
    expr: "Expr"  # The expression being borrowed (typically a Name)
    mutability: Literal["peek", "poke"]

@dataclass(slots=True)
class TryExpr(Node):
    """Try expression: expr??"""
    expr: "Expr"  # The expression being unwrapped (must be Result<T>)

    inferred_inner_type: "Optional[Type]" = None
    inferred_unwrapped_type: "Optional[Type]" = None
    inferred_success_tag: "Optional[int]" = None
    inferred_error_type: "Optional[Type]" = None
    inferred_error_tag: "Optional[int]" = None
    inferred_func_return_type: "Optional[Type]" = None

@dataclass(slots=True)
class RangeExpr(Node):
    """Range expression: start..end or start..=end"""
    start: "Expr"           # Start expression (must evaluate to integer)
    end: "Expr"             # End expression (must evaluate to integer)
    inclusive: bool         # True for ..=, False for ..

Expr = Union[Name, IntLit, FloatLit, BoolLit, BlankLit, StringLit, InterpolatedString, ArrayLiteral, IndexAccess, UnaryOp, BinaryOp, Call, MethodCall, DotCall, MemberAccess, EnumConstructor, DynamicArrayNew, DynamicArrayFrom, CastExpr, Borrow, TryExpr, RangeExpr, Spread, Lambda]

def normalize_bin_op(op_tok_or_str: Token | str) -> BinOp:
    """Accepts either a Token (from the parser) or a str (already a lexeme). Returns one of:
    "+","-","*","/","%","==","!=","<","<=",">",">=","and","or","&","|","^","<<",">>". Raises if
    unknown (fail-fast so we don't emit invalid AST).
    """
    op_map = {
        "PLUS": "+", "MINUS": "-", "STAR": "*", "SLASH": "/", "MOD": "%",
        "EQEQ": "==", "NEQ": "!=",
        "LT": "<", "LE": "<=", "GT": ">", "GE": ">=",
        "AND": "and", "OR": "or", "XOR": "xor",
        "BIT_AND": "&", "BIT_OR": "|", "BIT_XOR": "^",
        "LSHIFT": "<<", "RSHIFT": ">>",

        "+": "+", "-": "-", "*": "*", "/": "/", "%": "%",
        "==": "==", "!=": "!=",
        "<": "<", "<=": "<=", ">": ">", ">=": ">=",
        "and": "and", "or": "or", "xor": "xor",
        "&&": "and", "||": "or", "^^": "xor",
        "&": "&", "|": "|", "^": "^",
        "<<": "<<", ">>": ">>",
    }

    key = getattr(op_tok_or_str, "type", None)
    if key is not None:
        hit = op_map.get(key)
        if hit is not None:
            return hit

    val = getattr(op_tok_or_str, "value", op_tok_or_str)
    hit = op_map.get(val)
    if hit is not None:
        return hit

    raise NotImplementedError(f"unknown binary operator: {op_tok_or_str!r}")


__all__ = [
    "Node", "Program", "UseStatement", "DocBlock", "DocTag", "DocExample", "FuncDef", "ConstDef", "StructDef", "StructField", "EnumDef", "EnumVariant", "ExtendDef", "ExternalBlock", "ExternalDecl", "Block", "Param",
    "Let", "ExprStmt", "Return", "Print", "PrintLn", "If", "While", "Foreach", "Expand", "Match", "MatchArm", "Pattern", "LiteralPattern", "WildcardPattern", "Break", "Continue",
    "Name", "IntLit", "FloatLit", "BoolLit", "BlankLit", "StringLit", "InterpolatedString", "ArrayElement", "ArrayLiteral", "DynamicArrayNew", "DynamicArrayFrom", "IndexAccess", "UnaryOp", "UnOp", "BinaryOp", "BinOp", "Call", "MethodCall", "DotCall", "MemberAccess", "EnumConstructor", "CastExpr", "Borrow", "TryExpr", "RangeExpr", "Spread", "Lambda",
    "PerkDef", "PerkMethodSignature", "ExtendWithDef", "BoundedTypeParam", "TypeConstraint", "OwnPattern", "RefBinding", "NomBinding",
    "Stmt", "Expr", "Rebind", "normalize_bin_op",
]


# `Provenance` annotates Node.ownership_provenance. It lands at the BOTTOM because
# `ownership` reaches back into this module (through generics.cloning) for MethodCall,
# so the import can only run once every class above exists. The totality gates resolve
# the annotation through this module's globals, which is why it must be a real name here
# and not a TYPE_CHECKING-only one.
from sushi_lang.semantics.ownership import Provenance  # noqa: E402
