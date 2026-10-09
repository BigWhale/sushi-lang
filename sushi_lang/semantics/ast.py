from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Union, Literal, TYPE_CHECKING
from sushi_lang.internals.report import Origin, Span
from sushi_lang.semantics.typesys import FunctionType, Type

from lark import Token

#: The metadata of a field that names a node another field of the same node holds. The
#: node walk (`ast_walk.children`) does not descend it, so each node is visited once.
ALIAS = {"alias": True}

if TYPE_CHECKING:
    from sushi_lang.semantics.conversions import Conversion
    from sushi_lang.semantics.generics.extension_targets import ExtensionTarget
    from sushi_lang.semantics.generics.types import TemplateId
    from sushi_lang.semantics.namespaces import NamespaceRef
    from sushi_lang.semantics.param_modes import ParamMode


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
    # Which `expand` copies this statement was spliced from, outermost first; empty for
    # a written statement. Two neighbours with different marks were never written one
    # after the other, so the dead-code rule (CE0140) does not read them as a sequence.
    expand_copies: Tuple[int, ...] = field(default=(), kw_only=True)


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
    # `public use`: the unit re-exports what the import brings, so its own importers get
    # the effect of the same `use` (section 8.1, Ruling 7). A re-export is of names and
    # takes no `as` (CE3016).
    is_public: bool = False
    public_span: Optional[Span] = None
    # A `use <lib/...>`: the `library_name` and `library_version` of the `.slib` that the
    # build found for the path. The driver stamps both before the analysis, because a
    # library is identified by its stamped name and not by the written path (#1120).
    library_name: Optional[str] = None
    library_version: Optional[str] = None

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
    # A capture of a name that the enclosing lambda captures is a read off the
    # environment of that lambda; the `lift` pass sets it (#1127). None reads the name.
    capture_source: Optional["Expr"] = None


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
    # Where each constraint is WRITTEN, index-aligned with `constraints` and covering
    # the qualifier with it. `loc` marks the whole `T: Hidden + Loud`, and a rule about
    # a constraint is a rule about the perk name (#706).
    constraint_spans: List[Optional[Span]] = None
    # The file each constraint is written in, index-aligned with `constraints`; None for
    # the file of the declaration that holds the parameter. A receiver bound that an
    # extension inherits from a type of another unit is written in that unit (#1070).
    constraint_files: List[Optional[str]] = field(default_factory=list)
    # The count of the leading constraints that a receiver parameter inherits from the
    # type that its target names (#1070, R1). The declaration of that type judges them.
    inherited: int = 0

    def __post_init__(self):
        if self.constraints is None:
            self.constraints = []
        if self.constraint_namespaces is None:
            self.constraint_namespaces = [None] * len(self.constraints)
        if self.constraint_spans is None:
            self.constraint_spans = [None] * len(self.constraints)
        if not self.constraint_files:
            self.constraint_files = [None] * len(self.constraints)

    def constraint_span(self, index: int) -> Optional[Span]:
        """Where constraint `index` is written, or None when nothing recorded it."""
        if 0 <= index < len(self.constraint_spans):
            return self.constraint_spans[index]
        return None

    def constraint_file(self, index: int) -> Optional[str]:
        """The file constraint `index` is written in, or None for the holder's own file."""
        if 0 <= index < len(self.constraint_files):
            return self.constraint_files[index]
        return None

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
    err_type: Optional[Type] = None  # The `| E` channel; None is a bare function
    name_span: Optional[Span] = None
    ret_span: Optional[Span] = None
    # Where the channel is WRITTEN. An extension and a perk contract both keep it, and
    # a function dropped it, so a diagnostic about the channel pointed at the return
    # type beside it -- or, in a perk implementation, at nothing at all (#662).
    err_span: Optional[Span] = None
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
    # The generic function this body is a monomorphized instance OF. Every instance
    # carries the TEMPLATE's spans, so a diagnostic raised in one is a diagnostic about
    # the template's source and is told once, not once per instantiation (#648). Set by
    # generics/monomorphize, and carried onto a lambda lifted out of such a body.
    instance_of: Optional[str] = None
    # Where a `static` marker was written on a perk-implementation method. The
    # grammar admits it in that position only so the perk pass can refuse it (CE4014).
    static_span: Optional[Span] = None
    # A spelled `Result@(T, E)` return as the interned enum, stamped by the resolve
    # pass (#857). `ret` keeps the type as written for the typecheck pass.
    resolved_result: Optional[Type] = None
    # Whether the WRITTEN signature has a channel, on a copy whose `ret` a substitution
    # may have made a Result (a monomorphized instance, a lifted lambda). None on a
    # written declaration, which `channel.has_channel` reads directly.
    written_channel: Optional[bool] = None
    # The `lib/<library>/<unit>` whose scope the names of this body resolve in: a copy
    # of a compiled library's template, which lands in a unit of the consumer (#1120).
    scope_unit: Optional[str] = None
    # The written type-pack parameters of the template this body is an instance of. The
    # copy fans each one out, so the name is no local; a use outside `expand` is CE0144.
    pack_names: Tuple[str, ...] = ()
    # The template this body is a copy of, by identity (#1070). The reporter mutes the
    # copies of a template whose check refused it: the template said the fault one time.
    template_id: Optional["TemplateId"] = None
    # The key of the copy, which each copy its body names records as its parent: the
    # growth rule reads that chain (`generics/instance_growth.py`). None on a written
    # declaration. It holds no node.
    instance_key: Optional[tuple] = None


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
    # The library unit that declared a constant a library ships as source. The
    # consumer's copy of the global takes its symbol from this unit, not from the
    # unit that holds the copy; None for a constant this program declares.
    home_unit: Optional[str] = None
    # The `lib/<library>/<unit>` whose scope the initializer of a compiled library's
    # constant resolves in, and the unit the constant is a name of (#1120).
    scope_unit: Optional[str] = None


@dataclass(slots=True)
class VarDef(ConstDef):
    """Unit-level storage: `var T name = <constant expression>`.

    A `ConstDef` in every field, and a subclass so that every pass which treats the two
    alike -- collection, name resolution, the type of a bare name, visibility, the
    namespace alias, the doc lints -- handles one node kind. The passes that tell them
    apart ask `isinstance(node, VarDef)`: a constant is `.rodata` with no address, a
    variable is data-segment storage that a rebind, a `poke` and a mutating method
    reach (docs/design/unit-storage.md).
    """
    # A consumer of a BINARY library declares the storage the library's bitcode
    # defines, under this symbol; None for a variable this program defines itself.
    link_symbol: Optional[str] = None

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
    # Written `error`, not `enum`: the type may be the `E` of a `Result`.
    is_error: bool = False

# The method name of a conversion, `extend Source as Target:` (docs/design/error-conversion.md
# section 8.2). `as` is a reserved word, so no written method has this name.
CONVERSION_METHOD = "as"


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
    # A `static` method has NO receiver (#542): no implicit `self`, and the call is
    # written on the type name. The span is what CE0134 points at.
    is_static: bool = False
    static_span: Optional[Span] = None
    # The unit that declared the template of a monomorphized copy (#1064). The copy is
    # checked in that unit's scope and defined in that unit's module. None on a written one.
    home_unit: Optional[str] = None
    # The file of the template record of a monomorphized copy: a library template's is
    # its source slice (#1070). None on a written one.
    template_file: Optional[str] = None
    # Whether the template's WRITTEN signature has a channel; see `FuncDef`.
    written_channel: Optional[bool] = None
    # A template a binary library ships, and how a diagnostic in its body is rendered;
    # see `FuncDef`. A copy keeps both, so its body may call the library's privates.
    is_library_template: bool = False
    library_origin: Optional[Origin] = None
    # The scope the names of the body resolve in; see `FuncDef`.
    scope_unit: Optional[str] = None
    # A conversion: the pair the collect pass filed, with its types resolved. None on
    # every other extension, and on a refused conversion.
    declared_conversion: Optional["Conversion"] = None
    # What each argument at the top level of the WRITTEN target declares: a bare or a
    # bounded name, or None for another argument (#1070). Index-aligned with the `@(...)`
    # arguments, or the one array element. It holds no node.
    target_params: Tuple[Optional[BoundedTypeParam], ...] = ()
    # The template of a copy, for the reporter's mute (#1070); see `FuncDef`. None on a
    # written extension.
    template_id: Optional["TemplateId"] = None
    # The target of the template of a copy as the source wrote it (`Box@(T)`), so a
    # diagnostic about the declaration names the declaration. None on a written one.
    template_target: Optional[str] = None
    # The key of a call-site copy; see `FuncDef`. None on every other extension.
    instance_key: Optional[tuple] = None

    @property
    def is_conversion(self) -> bool:
        """A conversion: the target is the return type and the one method-level type argument."""
        return self.name == CONVERSION_METHOD

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
    # True for a copy the compiler cut per instantiation, the meaning `FuncDef`
    # carries. The copy goes home to the unit that declared its template, so without
    # the mark a walk over that unit reads one written declaration once for each
    # instantiation (#657).
    is_synthesized: bool = False
    # The alias of `extend Dog with p.Named`, or None for a bare perk name.
    perk_namespace: Optional[str] = None
    # What each argument at the top level of the written target declares; see `ExtendDef`.
    target_params: Tuple[Optional[BoundedTypeParam], ...] = ()

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
    # A link name written as a string constant (#1089). `link_name` is empty until the
    # `ffi-clash` step folds it.
    link_expr: Optional["Expr"] = None

@dataclass(slots=True)
class ExternalVar(Node):
    """A C global variable inside an unsafe external block (#1090). Read-only from Sushi."""
    name: str                    # Sushi-visible name (e.g., "environ")
    ty: Optional[Type]           # A number, bool, ptr or Maybe@(ptr)
    link_name: str               # C link symbol; empty until a `link_expr` is folded
    name_span: Optional[Span] = None
    type_span: Optional[Span] = None
    doc: Optional[DocBlock] = None
    link_expr: Optional["Expr"] = None

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
    variables: List[ExternalVar] = field(default_factory=list)

@dataclass(slots=True)
class Block(Node):
    statements: List[Stmt]
    doc: Optional[DocBlock] = None
    # Set on a CALLABLE's body block: the locals whose every move does not dominate the
    # scope exit, so each needs a run-time drop flag (#414).
    conditional_move_names: Optional[frozenset] = None


@dataclass(slots=True)
class DestructureTarget:
    """One element of a `let` destructure: a binder, a `_`, or a nested destructure.

    A binder has a `name`, and `ty` is its written type or None for a bare binder. A `_`
    has neither a name nor `nested`. The typecheck pass stamps `element_type`, and
    `place_type` on a hidden binder of a destructuring rebind: the type of its place.
    """
    name: Optional[str] = None
    ty: Optional[Type] = None
    nested: Optional[List["DestructureTarget"]] = None
    name_span: Optional[Span] = None
    type_span: Optional[Span] = None
    loc: Optional[Span] = None
    element_type: Optional[Type] = None
    place_type: Optional[Type] = None


def destructure_binders(targets: Optional[List[DestructureTarget]]):
    """Every binder of a destructure, nested ones included, in source order."""
    for target in targets or ():
        if target.nested is not None:
            yield from destructure_binders(target.nested)
        elif target.name is not None:
            yield target


@dataclass(slots=True)
class Let(Stmt):
    name: str
    ty: Optional[Type]
    value: "Expr"
    name_span: Optional[Span] = None
    type_span: Optional[Span] = None
    # `let (a, b) = v` (TUPLE.md 4.2): the `Let` binds the whole tuple under a hidden
    # `name`, and the targets split it. None for an ordinary `let`.
    targets: Optional[List[DestructureTarget]] = None
    # The hidden whole of `(a, b) := v`: its binders are hidden, and the rebinds right
    # after it (`a := #rb0`, ...) give each binder the type of its place.
    rebinds: bool = False

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
    # The struct, enum, array or container the value prints through `Display`, stamped
    # by the typecheck pass; the backend reads it.
    display_type: Optional["Type"] = None

@dataclass(slots=True)
class PrintLn(Stmt):
    value: "Expr"
    display_type: Optional["Type"] = None

@dataclass(slots=True)
class Assert(Stmt):
    """`assert(cond)` / `assert(cond, message)`: a trap when `cond` is false."""
    cond: "Expr"
    message: Optional["Expr"] = None
    # The file name a diagnostic in this unit shows, stamped by the AST builder: the
    # backend prints it with `loc` when the condition is false.
    source_label: str = "<input>"

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
    # The `??` binder (`foreach(line?? in it)`). The AST builder renamed the loop's own
    # binding to a hidden name and prepended `let <T> <name> = <hidden>??` to the body;
    # this points at that Let so the typecheck pass can fill in its type once the item
    # type is known. Nothing downstream needs a rule of its own: the unwrap is the
    # ordinary TryExpr and the binding the ordinary Let. The body holds the Let, so this
    # field is an ALIAS and the node walk does not descend it (#1174).
    item_try_let: "Optional[Let]" = field(default=None, metadata=ALIAS)
    item_try_span: Optional[Span] = None
    # A `next()` protocol iterator (HANDLES.md ruling R21): the synthetic
    # `<hidden>.next()` call the typecheck pass built and stamped, and the hidden local
    # the iterator lives in. Both None for an `Iterator@(T)`, which is the buffer walk.
    protocol_next: "Optional[MethodCall]" = None
    protocol_iter_name: Optional[str] = None

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
    bindings: List['PatternItem']
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
    """A literal pattern: an integer (#415), a byte (`a'x'`) or a string.

    `display` keeps the source spelling for diagnostics, quotes included. `value` is
    the Python integer (sign already applied) or the string after escape processing,
    so `"a"` and `'a'` are one value. `radix` feeds the same fit rule as a
    context-typed literal: a non-decimal literal is a bit pattern. A string keeps 10."""
    value: int | str
    display: str
    radix: int = 10
    # A byte arm (`a'/' ->`): an integer arm that CE2076 offers the string form for.
    is_byte: bool = False


@dataclass(slots=True)
class RangePattern(Node):
    """An integer range in a pattern: `0x80..=0x8f`, `a'0'..=a'9'`, `0..10`.

    The two bounds are literal patterns, in every form an integer arm takes. `inclusive`
    is the `..=` form. The values the range matches depend on the type at its position,
    because a non-decimal bound is a bit pattern of that type: the typecheck pass and the
    backend both read them from `semantics/integer_patterns.py`. A string bound parses,
    and the typecheck pass refuses it (CE2072).
    """
    low: LiteralPattern
    high: LiteralPattern
    inclusive: bool

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
    inner_pattern: Union[str, 'Pattern', 'TuplePattern', 'LiteralPattern', 'RangePattern',
                         'OrPattern']
    inner_borrow: Optional[str] = None    # None | "peek" | "poke"
    inner_borrow_span: Optional[Span] = None


@dataclass(slots=True)
class TuplePattern(Node):
    """A tuple pattern: `(Color.Red, n)`, one item for each element of the tuple.

    It stands at the top of an arm, in an enum payload, or in another tuple pattern
    (docs/design/tuples.md section 5b).
    """
    elements: List['PatternItem']


@dataclass(slots=True)
class OrPattern(Node):
    """`|` alternatives: `0 | 1 | 2`, `Shape.Circle(r) | Shape.Ring(r)`, `Maybe.Some(1 | 2)`.

    It stands at the top of an arm and in every position of a pattern. It always holds two
    or more alternatives: the AST builder makes no OrPattern for one. Each alternative binds
    the same names with the same types and modes (CE2126), so the bindings of the FIRST
    alternative are the bindings of the whole pattern.
    """
    alternatives: List[Union['PatternItem', WildcardPattern]]


# One position of a pattern: a binding (a name, `_`, `poke x`, `nom x`), an enum pattern,
# an integer literal, an integer range, a tuple pattern, an `Own(...)` pattern, or `|`
# alternatives of these.
PatternItem = Union[str, Pattern, OwnPattern, RefBinding, NomBinding, LiteralPattern,
                    RangePattern, TuplePattern, OrPattern]


def alternatives_of(item: object) -> list:
    """The alternatives of a pattern item: its own list for `|` alternatives, else itself."""
    if isinstance(item, OrPattern):
        return list(item.alternatives)
    return [item]


def pattern_bindings(item: object, parent: "Optional[Node]" = None,
                     every_alternative: bool = False):
    """(name, owner, span) for each name that a pattern item binds, in source order.

    The owner is the pattern node that holds the binding (a written-binder key); the span
    is the binding's own span, or the owner's span for a bare name. `|` alternatives bind
    what their first alternative binds (CE2126 holds the others to it);
    `every_alternative` walks each alternative instead.
    """
    if isinstance(item, OrPattern):
        for alternative in (item.alternatives if every_alternative
                            else item.alternatives[:1]):
            yield from pattern_bindings(alternative, parent, every_alternative)
    elif isinstance(item, (Pattern, TuplePattern)):
        for sub in (item.bindings if isinstance(item, Pattern) else item.elements):
            yield from pattern_bindings(sub, item, every_alternative)
    elif isinstance(item, OwnPattern):
        inner = item.inner_pattern
        if isinstance(inner, str):
            if inner != "_":
                yield inner, item, item.loc or (parent.loc if parent else None)
        else:
            yield from pattern_bindings(inner, item, every_alternative)
    elif isinstance(item, str):
        if item != "_" and parent is not None:
            yield item, parent, parent.loc
    elif isinstance(item, (RefBinding, NomBinding)):
        yield item.name, parent, item.loc or (parent.loc if parent else None)


def pattern_source(item: object) -> str:
    """A pattern as it is written, for a diagnostic: `(Color.Red, nom s)`."""
    if isinstance(item, Pattern):
        head = f"{item.enum_name}.{item.variant_name}"
        if item.namespace is not None:
            head = f"{item.namespace}.{head}"
        if not item.bindings:
            return head
        return f"{head}({', '.join(pattern_source(b) for b in item.bindings)})"
    if isinstance(item, TuplePattern):
        return f"({', '.join(pattern_source(e) for e in item.elements)})"
    if isinstance(item, OwnPattern):
        inner = pattern_source(item.inner_pattern)
        if item.inner_borrow is not None:
            inner = f"{item.inner_borrow} {inner}"
        return f"Own({inner})"
    if isinstance(item, RefBinding):
        return f"{item.mode} {item.name}"
    if isinstance(item, NomBinding):
        return f"nom {item.name}"
    if isinstance(item, LiteralPattern):
        return item.display
    if isinstance(item, RangePattern):
        return f"{item.low.display}{'..=' if item.inclusive else '..'}{item.high.display}"
    if isinstance(item, OrPattern):
        return " | ".join(pattern_source(alt) for alt in item.alternatives)
    if isinstance(item, str):
        return item
    return "_"

@dataclass(slots=True)
class MatchArm(Node):
    """Single arm in a match statement/expression"""
    pattern: Union[Pattern, LiteralPattern, RangePattern, WildcardPattern, TuplePattern,
                   OrPattern]
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
    # The typecheck pass refused the arms as not exhaustive (CE2040 / CE2074, #886).
    not_exhaustive: bool = False


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
    # The source spelling of a byte literal (`a'/'`), or None for a number literal. A
    # byte literal defaults to u8, and a diagnostic names it by this spelling.
    byte_spelling: Optional[str] = None

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
    # For each hole that prints through `Display` (a struct, an enum, an array or a
    # container), its type; None elsewhere. Stamped by the typecheck pass; the backend reads it.
    display_types: Optional[List[Optional["Type"]]] = None

@dataclass(slots=True)
class ArrayElement(Node):
    value: "Expr"
    count: Optional["Expr"] = None   # `value; count`. None is a plain element.

@dataclass(slots=True)
class ArrayLiteral(Node):
    elements: List["ArrayElement"]
    # The `T[N]` this literal is, stamped by the typecheck pass: the declared type of its
    # position, else the type inferred from its elements (#889). The backend reads it.
    resolved_type: Optional["Type"] = None

@dataclass(slots=True)
class IndexAccess(Node):
    array: "Expr"
    index: "Expr"
    inferred_element_type: Optional["Type"] = None  # Element type inferred by the typecheck pass.
    reads_a_string_byte: bool = False  # `s[i]` on a string (#1091), stamped by the typecheck pass
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
    # The struct or enum a comparison compares, stamped by the typecheck pass when the
    # operator reads a contract (`Eq` for `==`, `Ord` for `<`). The backend reads it.
    operand_type: Optional["Type"] = None

@dataclass(slots=True)
class TupleLiteral(Node):
    """A tuple literal: two or more expressions in parentheses, `(1, "a")`."""
    elements: List["Expr"]
    # The interned tuple type, stamped by the typecheck pass; the backend reads it.
    resolved_type: Optional["Type"] = None

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
    # A BLOCK body (`|x|:` and its statements) or a bare expression (`|x| x + 1`). The
    # shape is the predicate: a reader asks `isinstance(body, Block)`.
    body: Union["Expr", "Block"]
    ret: Optional[Type] = None
    err_type: Optional[Type] = None
    captures: Optional[List["Param"]] = None
    lifted_name: Optional[str] = None
    # Filled by the type pass: the lambda's resolved FunctionType (params + captures
    # typed, ok/err resolved). `expected_type` is a FunctionType propagated from the
    # binding/argument context, used to infer bare-param types (`|x|`).
    resolved_type: Optional[FunctionType] = None
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
    callee_param_modes: "Optional[Tuple[ParamMode, ...]]" = None
    callee_param_names: Optional[List[str]] = None
    callee_param_types: Optional[Tuple[Type, ...]] = None
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
    # The built-in method family that answered the call ("array", "list", ...), or None
    # for a user method. The borrow pass reads a method-effect row only for its family.
    callee_builtin_family: Optional[str] = None
    # What the typecheck pass resolved about the callee's parameters. The borrow pass
    # reads the modes off THIS node, so losing them makes every `nom` parameter inert.
    callee_param_modes: "Optional[Tuple[ParamMode, ...]]" = None
    callee_param_names: Optional[List[str]] = None
    callee_param_types: Optional[Tuple[Type, ...]] = None
    # Where the trailing arguments collect into a `...T` array, for a call behind a namespace.
    callee_variadic_at: Optional[int] = None
    # The rest of what the typecheck pass resolves about a call. Every call node
    # carries the whole set, so a pass never has to ask which call shape it has.
    callee_fn_type: Optional[Type] = None  # set when the callee resolves to a FunctionType
    callee_unresolved: bool = False  # set when no callee was found at all (CE2008, CE2092)
    external_ref: Optional[Tuple[str, str]] = None  # (namespace, name) for an FFI call
    variadic_arg_types: Optional[List] = None  # an extern variadic call's promoted arg types
    # The SOLVED method-level type arguments of a method-generic extension call
    # (`name@(U)`); the backend composes them into the callee symbol.
    callee_method_type_args: Optional[Tuple] = None
    # The names a call was written with. A method has no field names, so these are
    # carried only to be refused (CE6104); the builder used to drop them (#563).
    field_names: Optional[List[str]] = None


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
    callee_builtin_family: Optional[str] = None  # see MethodCall
    # What the typecheck pass resolved about the callee's parameters. The borrow pass
    # reads the modes off THIS node, so losing them makes every `nom` parameter inert.
    callee_param_modes: "Optional[Tuple[ParamMode, ...]]" = None
    callee_param_names: Optional[List[str]] = None
    callee_param_types: Optional[Tuple[Type, ...]] = None
    # Where the trailing arguments collect into a `...T` array, for a call behind a namespace.
    callee_variadic_at: Optional[int] = None
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
    # True when the callee is a STATIC method (#542): no receiver crosses, and the
    # backend emits the symbol with the argument list alone. Stamped by the typecheck
    # pass, exactly as the receiver mode beside it is.
    callee_is_static: bool = False
    # The type a static call was written on, interned. The backend needs it to spell
    # the symbol, and the receiver `Name` is not a value it could infer one from.
    callee_static_target: Optional["Type"] = None


@dataclass(slots=True)
class MemberAccess(Node):
    """Member access expression: obj.field"""
    receiver: "Expr"    # The struct expression (p in p.x)
    member: str
    namespace_ref: Optional["NamespaceRef"] = None  # a name read through an alias
    # `libc.environ`: the (namespace, name) of an external variable (#1090).
    external_var_ref: Optional[Tuple[str, str]] = None
    resolved_enum_type: Optional["Type"] = None  # a bare `Maybe.None`: the interned instance (#545)
    expected_type: Optional[Type] = None  # a generic fn behind an alias solves from it (#1017)
    member_span: Optional[Span] = None  # the `.member` postfix; `loc` is the whole read

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
    # The `T[]` the position expects, stamped by the typecheck pass (#544). An EMPTY
    # literal has no other source for its element type, and every position that has a
    # declared type -- a `let`, a field, a payload, a parameter, a `.realise()` default,
    # a bare extension's return -- hands it over through one propagation arm.
    resolved_type: Optional["Type"] = None

@dataclass(slots=True)
class CastExpr(Node):
    expr: "Expr"
    target_type: Type
    source_type: Optional[Type] = None  # Operand's semantic type, stamped by the typecheck pass (signedness for codegen)
    # The declared conversion `as` calls between two error types (`semantics/conversions.py`),
    # stamped by the typecheck pass. None for a numeric cast and an identity cast.
    inferred_conversion: Optional["Conversion"] = None

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
    inferred_func_return_type: "Optional[Type]" = None
    # The declared conversion the propagate path calls when the two error types differ
    # (`semantics/conversions.py`). None when the error propagates unchanged.
    inferred_conversion: Optional["Conversion"] = None

@dataclass(slots=True)
class RangeExpr(Node):
    """Range expression: start..end or start..=end, and `(start..end).rev()`.

    A range always goes up. `reverse` is set by `.rev()`, which the AST builder folds into
    the node: the same values, last first. A second `.rev()` clears it.
    """
    start: "Expr"           # Start expression (must evaluate to integer)
    end: "Expr"             # End expression (must evaluate to integer)
    inclusive: bool         # True for ..=, False for ..
    reverse: bool = False   # True after an odd number of `.rev()` calls

Expr = Union[Name, IntLit, FloatLit, BoolLit, BlankLit, StringLit, InterpolatedString, ArrayLiteral, IndexAccess, UnaryOp, BinaryOp, Call, MethodCall, DotCall, MemberAccess, EnumConstructor, DynamicArrayNew, DynamicArrayFrom, CastExpr, Borrow, TryExpr, RangeExpr, Spread, Lambda, TupleLiteral]
# The three call shapes; each carries the whole set of callee stamps.
CallLike = Union[Call, MethodCall, DotCall]
# The two call shapes with a receiver and a method name.
MethodLike = Union[MethodCall, DotCall]

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
    "Node", "Program", "UseStatement", "DocBlock", "DocTag", "DocExample", "FuncDef", "ConstDef", "VarDef", "StructDef", "StructField", "EnumDef", "EnumVariant", "ExtendDef", "ExternalBlock", "ExternalDecl", "ExternalVar", "Block", "Param",
    "Let", "ExprStmt", "Return", "Print", "PrintLn", "Assert", "If", "While", "Foreach", "Expand", "Match", "MatchArm", "Pattern", "LiteralPattern", "RangePattern", "WildcardPattern", "TuplePattern", "OrPattern", "alternatives_of", "Break", "Continue",
    "Name", "IntLit", "FloatLit", "BoolLit", "BlankLit", "StringLit", "InterpolatedString", "ArrayElement", "ArrayLiteral", "DynamicArrayNew", "DynamicArrayFrom", "IndexAccess", "UnaryOp", "UnOp", "BinaryOp", "BinOp", "Call", "MethodCall", "DotCall", "MemberAccess", "EnumConstructor", "CastExpr", "Borrow", "TryExpr", "RangeExpr", "Spread", "Lambda", "TupleLiteral", "DestructureTarget", "destructure_binders",
    "PerkDef", "PerkMethodSignature", "ExtendWithDef", "BoundedTypeParam", "TypeConstraint", "OwnPattern", "RefBinding", "NomBinding",
    "Stmt", "Expr", "Rebind", "normalize_bin_op",
]


# `Provenance` annotates Node.ownership_provenance. It lands at the BOTTOM because
# `ownership` reaches back into this module (through generics.cloning) for MethodCall,
# so the import can only run once every class above exists. The totality gates resolve
# the annotation through this module's globals, which is why it must be a real name here
# and not a TYPE_CHECKING-only one.
from sushi_lang.semantics.ownership import Provenance  # noqa: E402
