from lark import Lark
from lark.indenter import Indenter

class LangIndenter(Indenter):
    NL_type = '_NEWLINE'
    OPEN_PAREN_types = ['LPAR', 'LSQB']   # ( and [
    CLOSE_PAREN_types = ['RPAR', 'RSQB']  # ) and ]
    INDENT_type = '_INDENT'
    DEDENT_type = '_DEDENT'
    tab_len = 8

