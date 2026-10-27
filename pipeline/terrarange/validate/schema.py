"""Schema validation for processed outputs."""


def validate_schema(record: dict) -> bool:
    required = {"cell_id", "methodology_version", "source_id"}
    return required.issubset(record)
