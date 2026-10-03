# ADR-001: Stable physical schema with evolving semantic ontology

Status: accepted

The PostgreSQL schema remains stable while classes, predicates, constraints, aliases, and inheritance evolve as data. MCP cannot create tables or execute DDL. This preserves operational stability and lets semantic coverage grow without schema migration for every domain concept.
