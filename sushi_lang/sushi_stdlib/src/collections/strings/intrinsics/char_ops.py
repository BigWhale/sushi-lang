"""ASCII Character Operations Intrinsics"""

import llvmlite.ir as ir


def emit_toupper_intrinsic(module: ir.Module) -> ir.Function:
    """Emit `i32 llvm_toupper(i32 c)`."""
    func_name = "llvm_toupper"

    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    i32 = ir.IntType(32)

    fn_ty = ir.FunctionType(i32, [i32])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "c"

    entry_block = func.append_basic_block("entry")
    convert_block = func.append_basic_block("convert")
    return_block = func.append_basic_block("return")

    builder = ir.IRBuilder(entry_block)
    c = func.args[0]
    is_lower_ge_a = builder.icmp_unsigned(">=", c, ir.Constant(i32, 97), name="is_ge_a")
    is_lower_le_z = builder.icmp_unsigned("<=", c, ir.Constant(i32, 122), name="is_le_z")
    is_lowercase = builder.and_(is_lower_ge_a, is_lower_le_z, name="is_lowercase")
    builder.cbranch(is_lowercase, convert_block, return_block)

    builder = ir.IRBuilder(convert_block)
    uppercase = builder.sub(c, ir.Constant(i32, 32), name="uppercase")
    builder.branch(return_block)

    builder = ir.IRBuilder(return_block)
    result = builder.phi(i32, name="result")
    result.add_incoming(uppercase, convert_block)
    result.add_incoming(c, entry_block)
    builder.ret(result)

    return func


def emit_tolower_intrinsic(module: ir.Module) -> ir.Function:
    """Emit `i32 llvm_tolower(i32 c)`."""
    func_name = "llvm_tolower"

    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    i32 = ir.IntType(32)

    fn_ty = ir.FunctionType(i32, [i32])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "c"

    entry_block = func.append_basic_block("entry")
    convert_block = func.append_basic_block("convert")
    return_block = func.append_basic_block("return")

    builder = ir.IRBuilder(entry_block)
    c = func.args[0]
    is_upper_ge_a = builder.icmp_unsigned(">=", c, ir.Constant(i32, 65), name="is_ge_A")
    is_upper_le_z = builder.icmp_unsigned("<=", c, ir.Constant(i32, 90), name="is_le_Z")
    is_uppercase = builder.and_(is_upper_ge_a, is_upper_le_z, name="is_uppercase")
    builder.cbranch(is_uppercase, convert_block, return_block)

    builder = ir.IRBuilder(convert_block)
    lowercase = builder.add(c, ir.Constant(i32, 32), name="lowercase")
    builder.branch(return_block)

    builder = ir.IRBuilder(return_block)
    result = builder.phi(i32, name="result")
    result.add_incoming(lowercase, convert_block)
    result.add_incoming(c, entry_block)
    builder.ret(result)

    return func


def emit_isspace_intrinsic(module: ir.Module) -> ir.Function:
    """Emit `i8 llvm_isspace(i32 c)`."""
    func_name = "llvm_isspace"

    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    i8 = ir.IntType(8)
    i32 = ir.IntType(32)

    fn_ty = ir.FunctionType(i8, [i32])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "c"

    entry_block = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry_block)

    c = func.args[0]

    is_space = builder.icmp_unsigned("==", c, ir.Constant(i32, 32), name="is_space")      # ' '
    is_tab = builder.icmp_unsigned("==", c, ir.Constant(i32, 9), name="is_tab")           # '\t'
    is_newline = builder.icmp_unsigned("==", c, ir.Constant(i32, 10), name="is_newline")  # '\n'
    is_cr = builder.icmp_unsigned("==", c, ir.Constant(i32, 13), name="is_cr")            # '\r'

    is_ws1 = builder.or_(is_space, is_tab, name="is_ws1")
    is_ws2 = builder.or_(is_newline, is_cr, name="is_ws2")
    is_whitespace = builder.or_(is_ws1, is_ws2, name="is_whitespace")

    result = builder.zext(is_whitespace, i8, name="result")
    builder.ret(result)

    return func
