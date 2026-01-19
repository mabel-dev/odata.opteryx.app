"""Check how 'not' is being parsed"""

import sys
from pathlib import Path

from odata_query.grammar import ODataLexer
from odata_query.grammar import ODataParser

_vendored_path = str(Path(__file__).parent / "app" / "vendored" / "odata-query")
sys.path.insert(0, _vendored_path)


# Test parsing
filter_str = "not(number_of_moons eq 0)"
lexer = ODataLexer()
parser = ODataParser()
tokens = lexer.tokenize(filter_str)
ast = parser.parse(tokens)

print(f"Filter: {filter_str}")
print(f"AST type: {type(ast).__name__}")
print(f"AST: {ast}")

if hasattr(ast, "func"):
    print(f"  func: {ast.func}")
    print(f"  func.name: {ast.func.name}")
if hasattr(ast, "op"):
    print(f"  op: {ast.op}")
if hasattr(ast, "operand"):
    print(f"  operand: {ast.operand}")
