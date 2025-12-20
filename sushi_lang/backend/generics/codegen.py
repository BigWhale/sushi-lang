"""
Inline emission for generic types (Result<T>, Maybe<T>).

This module contains the inline emitters for built-in generic types that cannot
be precompiled in the stdlib due to the infinite number of possible type parameters.

Modules:
- results.py: Result<T> inline emission (.realise() method)
- maybe.py: Maybe<T> inline emission (.is_some(), .is_none(), .realise(), .expect() methods)

Why inline emission?
Generic types like Result<T> and Maybe<T> work with any type parameter T (built-in
or user-defined). Pre-generating all possible monomorphizations is impractical, so
these methods are emitted on-demand during compilation.

See docs/stdlib/ISSUES.md for detailed explanation.
"""
