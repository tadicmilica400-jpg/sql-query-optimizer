# Cost-Based SQL Query Optimizer

Python program that parses and semantically validates a restricted SQL query against a JSON catalog of table, attribute and index statistics, estimates execution costs in block transfers, and selects a physical plan. Developed for Database Systems 2 coursework at the University of Belgrade, School of Electrical Engineering.

## What it implements

- SQL parsing and type/column validation; logical predicate and projection pushdown.
- Table cardinality and selection estimates; linear scans and eligible B+ tree/hash index access paths.
- Join candidate generation and cost comparison for nested loops, block nested loops, sort-merge, hash and indexed strategies where eligible.
- Dynamic programming over partial table sets, retaining useful physical properties; final projection, sorting, aggregation and supported set operations.
- Explain output with plan costs and optional alternative plan/pruning details.

Supported input is limited to at most four tables and six atomic `WHERE` conditions, comma-separated `FROM` tables and joins specified in `WHERE`; subqueries and `JOIN ... ON` are outside its supported grammar. This is a cost estimator and plan selector, not a database engine executing queries.

## Run

Python 3.10+; standard library only. From this directory:

```bash
python main.py primer_ulaza.json --query-file primer_upita.txt
python main.py primer_ulaza.json --query-file primer_upita.txt --all-plans --explain-all
```

`python main.py primer_ulaza.json` accepts an interactive, semicolon-terminated query. `--show-pruned` and `--show-statistics` add details. The sample JSON contains schema/statistics rather than individual student records.

## Structure

`main.py` provides the CLI; `sql_parser.py` and `semantic_analyzer.py` process input; `catalog.py`, `models.py` and `statistics.py` model the catalog and estimates; `logical_optimizer.py` transforms logical operations; `access_paths.py`, `join_planner.py`, `physical_search.py` and `operators.py` evaluate physical plans; `optimizer.py` connects the components. `primer_ulaza.json` and `primer_upita.txt` are example inputs. Example inputs are included. The program estimates plans; it does not execute SQL against a database.
