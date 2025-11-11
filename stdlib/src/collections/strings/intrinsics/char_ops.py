"""
ASCII Character Operations Intrinsics

Pure LLVM IR implementations of character operations, replacing C standard library functions:
- llvm_toupper: Convert ASCII lowercase to uppercase
- llvm_tolower: Convert ASCII uppercase to lowercase
- llvm_isspace: Check if character is ASCII whitespace
"""

import llvmlite.ir as ir


def emit_toupper_intrinsic(module: ir.Module) -> ir.Function:
    """Emit the ASCII toupper intrinsic function.

    Converts lowercase ASCII characters ('a'-'z') to uppercase ('A'-'Z').
    All other characters are returned unchanged.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: i32 llvm_toupper(i32 c)
    """
    func_name = "llvm_toupper"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Types
    i1 = ir.IntType(1)
    i32 = ir.IntType(32)

    # Function signature: i32 llvm_toupper(i32 c)
    fn_ty = ir.FunctionType(i32, [i32])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "c"

    # Create blocks
    entry_block = func.append_basic_block("entry")
    convert_block = func.append_basic_block("convert")
    return_block = func.append_basic_block("return")

    # Entry block: check if c is in range 'a' (97) to 'z' (122)
    builder = ir.IRBuilder(entry_block)
    c = func.args[0]
    is_lower_ge_a = builder.icmp_unsigned(">=", c, ir.Constant(i32, 97), name="is_ge_a")
    is_lower_le_z = builder.icmp_unsigned("<=", c, ir.Constant(i32, 122), name="is_le_z")
    is_lowercase = builder.and_(is_lower_ge_a, is_lower_le_z, name="is_lowercase")
    builder.cbranch(is_lowercase, convert_block, return_block)

    # Convert block: subtract 32 to convert to uppercase
    builder = ir.IRBuilder(convert_block)
    uppercase = builder.sub(c, ir.Constant(i32, 32), name="uppercase")
    builder.branch(return_block)

    # Return block: phi node to select result
    builder = ir.IRBuilder(return_block)
    result = builder.phi(i32, name="result")
    result.add_incoming(uppercase, convert_block)
    result.add_incoming(c, entry_block)
    builder.ret(result)

    return func


def emit_tolower_intrinsic(module: ir.Module) -> ir.Function:
    """Emit the ASCII tolower intrinsic function.

    Converts uppercase ASCII characters ('A'-'Z') to lowercase ('a'-'z').
    All other characters are returned unchanged.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: i32 llvm_tolower(i32 c)
    """
    func_name = "llvm_tolower"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Types
    i1 = ir.IntType(1)
    i32 = ir.IntType(32)

    # Function signature: i32 llvm_tolower(i32 c)
    fn_ty = ir.FunctionType(i32, [i32])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "c"

    # Create blocks
    entry_block = func.append_basic_block("entry")
    convert_block = func.append_basic_block("convert")
    return_block = func.append_basic_block("return")

    # Entry block: check if c is in range 'A' (65) to 'Z' (90)
    builder = ir.IRBuilder(entry_block)
    c = func.args[0]
    is_upper_ge_a = builder.icmp_unsigned(">=", c, ir.Constant(i32, 65), name="is_ge_A")
    is_upper_le_z = builder.icmp_unsigned("<=", c, ir.Constant(i32, 90), name="is_le_Z")
    is_uppercase = builder.and_(is_upper_ge_a, is_upper_le_z, name="is_uppercase")
    builder.cbranch(is_uppercase, convert_block, return_block)

    # Convert block: add 32 to convert to lowercase
    builder = ir.IRBuilder(convert_block)
    lowercase = builder.add(c, ir.Constant(i32, 32), name="lowercase")
    builder.branch(return_block)

    # Return block: phi node to select result
    builder = ir.IRBuilder(return_block)
    result = builder.phi(i32, name="result")
    result.add_incoming(lowercase, convert_block)
    result.add_incoming(c, entry_block)
    builder.ret(result)

    return func


def emit_isspace_intrinsic(module: ir.Module) -> ir.Function:
    """Emit the ASCII isspace intrinsic function.

    Checks if a character is ASCII whitespace:
    - Space (32)
    - Tab (9)
    - Newline (10)
    - Carriage return (13)

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: i8 llvm_isspace(i32 c)
    """
    func_name = "llvm_isspace"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Types
    i1 = ir.IntType(1)
    i8 = ir.IntType(8)
    i32 = ir.IntType(32)

    # Function signature: i8 llvm_isspace(i32 c)
    fn_ty = ir.FunctionType(i8, [i32])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "c"

    # Create entry block
    entry_block = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry_block)

    c = func.args[0]

    # Check for each whitespace character
    is_space = builder.icmp_unsigned("==", c, ir.Constant(i32, 32), name="is_space")      # ' '
    is_tab = builder.icmp_unsigned("==", c, ir.Constant(i32, 9), name="is_tab")           # '\t'
    is_newline = builder.icmp_unsigned("==", c, ir.Constant(i32, 10), name="is_newline")  # '\n'
    is_cr = builder.icmp_unsigned("==", c, ir.Constant(i32, 13), name="is_cr")            # '\r'

    # Combine all checks with OR
    is_ws1 = builder.or_(is_space, is_tab, name="is_ws1")
    is_ws2 = builder.or_(is_newline, is_cr, name="is_ws2")
    is_whitespace = builder.or_(is_ws1, is_ws2, name="is_whitespace")

    # Convert i1 to i8 (bool representation in Sushi)
    result = builder.zext(is_whitespace, i8, name="result")
    builder.ret(result)

    return func
