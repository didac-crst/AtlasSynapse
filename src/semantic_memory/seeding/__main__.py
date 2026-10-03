"""CLI entrypoint: python -m semantic_memory.seeding"""

from semantic_memory.db import session_scope
from semantic_memory.seeding.ontology import seed_core_ontology


def main() -> None:
    with session_scope() as session:
        result = seed_core_ontology(session)
    print(result)


if __name__ == "__main__":
    main()
