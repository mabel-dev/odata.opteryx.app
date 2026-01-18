#!/usr/bin/env python3
"""
Spike: Explore odata-query parser AST structure
Goal: Understand the AST nodes produced by odata-query library
"""

import sys
sys.path.insert(0, "/Users/justin/Nextcloud/opteryx-cloud/odata.opteryx")
sys.path.insert(0, "/Users/justin/Nextcloud/opteryx-cloud/odata.opteryx/app/vendored/odata-query")

from odata_query.grammar import ODataLexer, ODataParser
from odata_query import ast as odata_ast

# Initialize parser (only once)
lexer = ODataLexer()
parser = ODataParser()
test_cases = [
    # Simple comparison
    ("vendor eq 'Oracle'", "Simple string equality"),
    
    # Case-sensitive (OData standard)
    ("Name ne 'Test'", "String inequality"),
    
    # Numeric comparisons
    ("Price gt 100", "Greater than number"),
    ("Stock le 50", "Less than or equal number"),
    
    # Logical operators
    ("vendor eq 'Oracle' and Price gt 100", "AND operator"),
    ("Status eq 'Active' or Status eq 'Inactive'", "OR operator"),
    ("not (Status eq 'Deleted')", "NOT operator"),
    
    # Functions
    ("contains(ProductName, 'Server')", "contains function"),
    ("startswith(ProductName, 'SQL')", "startswith function"),
    ("endswith(ProductName, 'Pro')", "endswith function"),
    
    # NULL handling
    ("Description eq null", "NULL literal"),
    ("Description ne null", "NOT NULL"),
    
    # Complex expressions
    ("(Category eq 'Software' or Category eq 'Services') and Price gt 1000", "Complex AND/OR"),
]

print("=" * 80)
print("ODATA-QUERY AST EXPLORATION")
print("=" * 80)
print()

for filter_expr, description in test_cases:
    print(f"Expression: {filter_expr}")
    print(f"Description: {description}")
    try:
        ast = parser.parse(lexer.tokenize(filter_expr))
        print(f"AST Type: {type(ast).__name__}")
        print(f"AST: {ast}")
        print(f"AST repr: {repr(ast)}")
        
        # Try to access common attributes
        if hasattr(ast, 'left'):
            print(f"  left: {ast.left} (type: {type(ast.left).__name__})")
        if hasattr(ast, 'right'):
            print(f"  right: {ast.right} (type: {type(ast.right).__name__})")
        if hasattr(ast, 'op'):
            print(f"  op: {ast.op}")
        if hasattr(ast, 'value'):
            print(f"  value: {ast.value}")
        if hasattr(ast, 'name'):
            print(f"  name: {ast.name}")
        if hasattr(ast, 'func'):
            print(f"  func: {ast.func}")
        if hasattr(ast, 'args'):
            print(f"  args: {ast.args}")
            
    except (ValueError, RuntimeError, TypeError) as e:
        print(f"ERROR: {e}")
    
    print("-" * 80)
    print()

# Also inspect the AST module to understand available node types
print("=" * 80)
print("AVAILABLE AST NODE TYPES")
print("=" * 80)
print()

node_types = [name for name in dir(odata_ast) if not name.startswith('_') and name[0].isupper()]
for node_type in sorted(node_types):
    cls = getattr(odata_ast, node_type)
    if isinstance(cls, type):
        print(f"{node_type}: {cls.__doc__ if cls.__doc__ else 'No documentation'}")
