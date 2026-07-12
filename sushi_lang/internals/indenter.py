from lark.indenter import DedentError, Indenter

from sushi_lang.internals.diagnostics import SyntaxDiagnostic
from sushi_lang.internals.report import Span


class LangIndenter(Indenter):
    NL_type = '_NEWLINE'
    OPEN_PAREN_types = ['LPAR', 'LSQB']   # ( and [
    CLOSE_PAREN_types = ['RPAR', 'RSQB']  # ) and ]
    INDENT_type = '_INDENT'
    DEDENT_type = '_DEDENT'
    tab_len = 8

    def handle_NL(self, token):
        """Delegate to Lark, but give a bad dedent the position Lark drops.

        DedentError carries no location, and this is the only place that knows
        one: the newline token whose indentation failed to line up.
        """
        indent_str = token.rsplit('\n', 1)[1]
        indent = indent_str.count(' ') + indent_str.count('\t') * self.tab_len
        try:
            yield from super().handle_NL(token)
        except DedentError as exc:
            line = getattr(token, "end_line", None) or getattr(token, "line", 1)
            raise SyntaxDiagnostic(
                "CE6004",
                span=Span(line, 1, line, max(1, indent)),
                got=indent,
                expected=self.indent_level[-1],
            ) from exc
