# Cost-Based SQL Query Optimizer

## Project overview

This Python coursework project parses a restricted SQL dialect, validates queries against a JSON catalogue of schema and statistics, estimates relational-operation costs in block transfers, and selects a physical execution plan. It is an optimizer and cost model, not a database engine: it does not store data or execute the selected plan.

## Academic context

Developed for Database Systems 2 coursework at the University of Belgrade, School of Electrical Engineering. The project focuses on relational algebra, cardinality estimation, access-path selection, join ordering and physical-plan enumeration.

## Technologies and methods

- Python 3.10+; standard library only
- Hand-written SQL tokenizer and recursive-descent parser
- Catalogue-driven type checking and semantic validation
- Selectivity, cardinality and result-block estimation
- Dynamic programming over subsets of joined tables
- Dominance pruning that retains relevant physical properties, including sort order
- Deterministic tie-breaking for reproducible plan selection

## Supported functionality

- `SELECT`, comma-separated `FROM`, `WHERE`, `ORDER BY`, aggregate expressions and supported set operations
- Boolean `WHERE` expressions with parentheses, `AND` and `OR`
- Projection and local-predicate pushdown
- Linear scans and eligible B+ tree or hash-index access paths
- Nested-loop, block nested-loop, sort-merge, hash and eligible indexed joins
- Final projection, sorting, aggregation and set-operation costing
- Explain output containing formulas, substituted values, cumulative cost, estimated rows/blocks and pruning statistics

The semantic analyser intentionally limits a query to four tables and six atomic `WHERE` conditions. Table aliases, subqueries and `JOIN ... ON` syntax are not supported; joins are expressed as predicates in `WHERE`.

## Technical implementation

`sql_parser.py` builds a typed query representation without relying on a third-party SQL parser. `semantic_analyzer.py` resolves attributes, checks ambiguity and validates operand types. `statistics.py` estimates selectivities, join cardinalities, projected row widths and external-sort costs. `access_paths.py` evaluates scans and indexes. `physical_search.py` extends partial plans and prunes dominated candidates while preserving physical properties. `join_planner.py` compares applicable join algorithms. `operators.py` adds final relational operators and set-operation alternatives.

The objective is an estimated I/O cost in block transfers. Estimates depend entirely on the supplied catalogue statistics and the implemented independence/uniformity assumptions; they are not measured runtimes.

## Installation and usage

No external packages are required.

```bash
python main.py primer_ulaza.json --query-file primer_upita.txt
python main.py primer_ulaza.json --query-file primer_upita.txt --all-plans --explain-all --show-pruned
```

Running `python main.py primer_ulaza.json` without a query argument starts semicolon-terminated interactive input. `primer_ulaza.json` contains schema and statistics, not database records.

## Repository structure

| Path | Responsibility |
| --- | --- |
| `main.py` | CLI and explain output |
| `sql_parser.py` | Tokenization and parsing |
| `semantic_analyzer.py` | Name, type and grammar-level validation |
| `catalog.py`, `models.py` | Catalogue loading and domain models |
| `statistics.py` | Selectivity, cardinality, width and sort estimates |
| `logical_optimizer.py` | Predicate/projection transformations |
| `access_paths.py` | Scan and index alternatives |
| `join_planner.py`, `physical_search.py` | Join algorithms, DP enumeration and pruning |
| `operators.py` | Projection, aggregation, sorting and set operations |
| `primer_ulaza.json`, `primer_upita.txt` | Example catalogue and query |

## Verification, limitations and future work

- The repository contains no automated test suite or measured comparison with a real DBMS optimizer.
- Correctness is therefore inspectable through the example input and explain output, but not established by regression tests.
- Cost formulas are educational approximations and omit buffer-management, caching, CPU, concurrency and data-correlation effects.
- The sample could not be executed during this audit because Python was unavailable in the review environment.
- Useful extensions would include parser tests, plan-invariant tests, richer SQL syntax, histograms/correlation statistics and comparison against plans from an actual database system.


