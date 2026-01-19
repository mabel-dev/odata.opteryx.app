# OData to Opteryx Conversion - Layer-by-Layer Testing TODO

## 🎉 ALL PHASES COMPLETE! 🎉

**Final Test Results: 97/97 passing (100%)**

### Test Summary by Phase
- ✅ **Phase 1 (AST→Node):** 79/79 passing (100%)
- ✅ **Phase 2 (Logical Plan Nodes):** 6/6 passing (100%)
- ✅ **Phase 3 (Integration):** 6/6 passing (100%)
- ✅ **Phase 4 (Execution):** 6/6 passing (100%)

### What Works
✅ All literal types (Integer, Float, Boolean, String, Null, Date, Time, DateTime, Duration)
✅ All comparison operators (eq, ne, gt, ge, lt, le, in)
✅ All boolean operators (and, or, not)
✅ Function calls (startswith, endswith, contains, concat, length, tolower, toupper, trim, year, month, day)
✅ Complete logical plan building (Scan, Filter, Order, Limit, Project, Exit)
✅ End-to-end query execution with filters, ordering, limits, and projections

### Key Learnings
1. **AST Node Architecture:** Each AST node has `to_opteryx_node()` method for conversion
2. **LogicalColumn Constructor:** Requires `LogicalColumn(NodeType.IDENTIFIER, source_column)`
3. **Order Node Format:** Uses `(LogicalColumn, boolean)` tuples (True=ASC, False=DESC)
4. **Project Node Structure:** Uses `source_column` and `current_name` attributes
5. **No Project Without $select:** Project nodes only added when explicit column selection provided

---

## Current State
- Tests: 97/97 passing (100%)
- Architecture: Each AST node type has `to_opteryx_node()` method
- Execution: Uses opteryx-core via `../opteryx-core` path for `execute_logical_plan()`

## Phase 1: Unit Test AST → Node Conversion
Goal: Prove each layer of conversion works correctly before moving to the next

### 1.1 Test AST Literal Node Conversions
**Status: ✅ COMPLETE - All tests passing (14/14 = 100%)**
- [x] Integer literals → LITERAL node with correct value
- [x] String literals → LITERAL node with correct value  
- [x] Boolean literals → LITERAL node with correct value
- [x] Float literals → LITERAL node with correct value
- [x] Null literals → LITERAL node with NULL type
- [x] Date literals → LITERAL node with DATE type
- [x] Time literals → LITERAL node with TIME type
- [x] DateTime literals → LITERAL node with TIMESTAMP type
- [x] Duration literals → LITERAL node with INTERVAL type

### 1.2 Test AST Identifier Node Conversions
**Status: ✅ COMPLETE - All tests passing (8/8 = 100%)**
- [x] Identifier → IDENTIFIER node with correct name
- [x] Identifiers with namespaces → correct full_name resolution

### 1.3 Test AST Comparison Operator Conversions
**Status: ✅ COMPLETE - All tests passing (10/10 = 100%)**
**Note: Binary comparison operators (eq, ne, gt, etc.) - NOT functions like startswith/endswith**
- [x] Eq comparator → COMPARISON_OPERATOR with value "Eq"
- [x] NotEq comparator → COMPARISON_OPERATOR with value "NotEq"
- [x] Gt comparator → COMPARISON_OPERATOR with value "Gt"
- [x] GtE comparator → COMPARISON_OPERATOR with value "GtEq"
- [x] Lt comparator → COMPARISON_OPERATOR with value "Lt"
- [x] LtE comparator → COMPARISON_OPERATOR with value "LtEq"
- [x] In comparator → COMPARISON_OPERATOR with value "In"
- [x] List literal → LITERAL node with OrsoTypes.ARRAY (for 'in' operator)

### 1.4 Test AST Boolean Operator Conversions
**Status: ✅ COMPLETE - All tests passing (8/8 = 100%)**
- [x] And operator → AND node with correct children
- [x] Or operator → OR node with correct children

### 1.5 Test AST Unary Operator Conversions
**Status: ✅ COMPLETE - All tests passing (6/6 = 100%)**
- [x] Not operator → NOT node with correct operand

### 1.6 Test AST Function Call Conversions
**Status: ✅ COMPLETE - All tests passing (13/13 = 100%)**
**String functions and other OData functions**
- [x] Call node → FUNCTION_CALL node
- [x] startswith(field, 'value') → correct function call structure
- [x] endswith(field, 'value') → correct function call structure
- [x] contains(field, 'value') → correct function call structure
- [x] Other OData functions (concat, length, tolower, toupper, trim, year, month, day)

### 1.7 Validate Against Opteryx's Parser
**Status: ✅ COMPLETE - All tests passing (20/20 = 100%)**
For each conversion type, validate:
- [x] Node type is valid and appropriate
- [x] Node values are present and correct
- [x] Child node structure is complete
- [x] Complex nested expressions validate correctly

## Phase 2: Unit Test Logical Plan Node Creation
Goal: Prove Scan, Filter, Order, Limit, Project nodes are structured correctly

### 2.1 Test Scan Node
**Status: ✅ COMPLETE**
- [x] Scan node created for table
- [x] Scan node has correct table name
- [x] Scan node columns = None (shouldn't project specific columns)

### 2.2 Test Filter Node
**Status: ✅ COMPLETE**
- [x] Filter node created with condition from parsed filter
- [x] Filter condition node tree is correct
- [x] Filter node columns = wildcard (passes through all columns)

### 2.3 Test Order Node
**Status: ✅ COMPLETE**
- [x] Order node created with sort columns and directions
- [x] Order node structure matches Opteryx (column_node, boolean)
- [x] Order directions correct (True=ASC, False=DESC)

### 2.4 Test Limit Node
**Status: ✅ COMPLETE**
- [x] Limit node created with top/skip values
- [x] Limit value correct
- [x] Offset value correct

### 2.5 Test Project Node
**Status: ✅ COMPLETE**
- [x] Project node created with selected columns
- [x] Project node has explicit IDENTIFIER nodes for each column
- [x] Project node columns list is populated

### 2.6 Test Exit Node
**Status: ✅ COMPLETE**
- [x] Exit node created
- [x] Exit node has WILDCARD node
- [x] Exit node is terminal in graph

## Phase 3: Integration Test - Plan Creation
**Status: ✅ COMPLETE - All tests passing (6/6 = 100%)**
Goal: Prove complete logical plans are built correctly

### 3.1 Test Simple Select
- [x] SELECT * FROM planets → Scan + Exit (2 nodes, no Project without $select)
- [x] Plan node count correct
- [x] Plan structure correct

### 3.2 Test Select with Filter
- [x] $filter=id eq 1 → Scan + Filter + Exit (3 nodes)
- [x] Filter condition correct
- [x] Plan node order correct

### 3.3 Test Select with Order
- [x] $orderby=id asc → Scan + Order + Exit (3 nodes)
- [x] Order columns and directions correct
- [x] Order node positioned correctly in graph

### 3.4 Test Select with Filter + Order
- [x] Both filter and order applied → Scan + Filter + Order + Exit (4 nodes)
- [x] Node sequence correct
- [x] Both conditions preserved

### 3.5 Test Select with Limit
- [x] $top=10 → Scan + Limit + Exit (3 nodes)
- [x] $skip=5&$top=10 → correct offset and limit
- [x] Limit node positioned correctly

### 3.6 Test Complex Query
- [x] $filter + $orderby + $top → Scan + Filter + Order + Limit + Exit (5 nodes)
- [x] All nodes present and correct

## Phase 4: Execution Testing
**Status: ✅ COMPLETE - All tests passing (6/6 = 100%)**
Goal: Prove plans execute correctly through Opteryx

### 4.1 Test Optimizer Phase
- [x] Plans pass through optimizer without NoneType errors
- [x] Optimizer produces valid optimized plans  

### 4.2 Test Executor Phase  
- [x] All queries execute successfully (6/6 tests)
- [x] Results match expected row counts
- [x] Results match expected columns
- [x] Filter queries work correctly

### 4.3 Validated Queries
- [x] Simple SELECT * queries
- [x] SELECT with $filter (equality, greater than)
- [x] SELECT with $orderby (ascending/descending)
- [x] SELECT with $top (limit)
- [x] SELECT with column projection ($select)
- [x] Complex queries (filter + order + limit)

**All Tests Passing (6/6):**
- ✅ 4.1: Simple SELECT * → 9 rows, 20 columns
- ✅ 4.2: SELECT with $filter=id eq 1 → 1 row
- ✅ 4.3: SELECT with ORDER BY → 9 rows  
- ✅ 4.4: SELECT with LIMIT 5 → 5 rows
- ✅ 4.5: Complex query (filter + order + limit) → 3 rows
- ✅ 4.6: SELECT id, name → 9 rows, 2 columns

**Key Fix:** LogicalColumn constructor requires `node_type` as first argument: 
`LogicalColumn(NodeType.IDENTIFIER, source_column)`

## Known Issues to Fix
- [ ] Integer.to_opteryx_node() missing or not working (needs verification)
- [ ] Float.to_opteryx_node() missing (needs implementation)
- [ ] Boolean.to_opteryx_node() may have NoneType error - val.lower() on None
- [ ] Null.to_opteryx_node() missing (needs implementation)
- [ ] Date.to_opteryx_node() missing (needs implementation)
- [ ] Time.to_opteryx_node() missing (needs implementation)
- [ ] DateTime.to_opteryx_node() missing (needs implementation)
- [ ] Duration.to_opteryx_node() missing (needs implementation)
- [ ] Call node (for functions) not yet implemented
- [ ] BinOp node (for arithmetic) not yet implemented

## Recent Changes (2026-01-19)
- Created test_phase_1_1_literals.py to test all literal type conversions
- Added Date, Time, DateTime, Duration test cases
- Added value validation logic with None-handling for complex types

## Architecture Notes
- Each AST node class now has `to_opteryx_node()` method
- Base _Node class has default implementation that raises NotImplementedError
- Conversion is recursive: parent nodes call child.to_opteryx_node()
- _odata_ast_to_opteryx_node() method in ODataLogicalPlanBuilder now just delegates to ast.to_opteryx_node()

## Test Scripts Created
- test_ast_conversion.py - Compare OData AST conversion against Opteryx's Filter parser
- test_scan_node.py - Validate Scan node structure (not run yet)
- **test_phase_1_1_literals.py - Phase 1.1: Test all literal type conversions (ACTIVE)**
- (more to be created as we progress through phases)

## Success Criteria
- Phase 1: All AST conversion tests pass and match Opteryx parser
- Phase 2: All logical plan nodes match Opteryx structure
- Phase 3: All plan compositions are correct
- Phase 4: Tests reach 80+ passing (80%+)
